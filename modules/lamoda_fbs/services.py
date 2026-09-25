import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from config import (
    LAMODA_API_BASE_URL,
    LAMODA_CLIENT_ID,
    LAMODA_CLIENT_SECRET,
    LAMODA_SELLER_ID,
)
from modules.lamoda_fbs.client import LamodaClient, LamodaDocumentError, LamodaValidationError
from modules.lamoda_fbs.constants import LABEL_SIZE_ITEM, LABEL_SIZE_PACK, LABEL_SIZE_PALLET, MAX_LABEL_BATCH
from modules.lamoda_fbs.pdf_reports import (
    create_manifest_pdf,
    create_marking_pdf,
    create_marking_xlsx,
    create_picking_list_pdf,
    merge_pdfs,
)
from modules.lamoda_fbs.storage import (
    LamodaOrder,
    LamodaOrderItem,
    LamodaPack,
    Shipment,
    attach_packs,
    cancel_session_order_packs,
    cargo_manifest,
    create_assembly_session,
    create_marking_batch,
    get_active_session,
    get_order_preparation,
    get_shipment_request_state,
    get_session_packs,
    get_session_order_refs,
    get_sync_value,
    marking_batch_rows,
    persist_order,
    save_shipment,
    set_order_preparation,
    set_shipment_request_state,
    set_sync_value,
    update_pack_lamoda_status,
    update_pack_return_info,
    validate_cargo_complete,
)
from modules.storage.postgres import session_scope


logger = logging.getLogger(__name__)
_shared_client = None


LABEL_ELIGIBLE_STATUSES = {
    "NEW", "CREATED", "PENDING", "CONFIRMED", "READY_FOR_ASSEMBLY",
    "AWAITING_SHIPMENT",
}


def get_client():
    global _shared_client
    if _shared_client is None:
        _shared_client = LamodaClient(
            LAMODA_CLIENT_ID, LAMODA_CLIENT_SECRET, LAMODA_SELLER_ID, LAMODA_API_BASE_URL,
        )
    return _shared_client


def _as_list(value, *keys):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def _order_id(order):
    return str(order.get("orderId") or order.get("id") or "").strip()


def _order_resource_id(order):
    """ID accepted in /v2/orders/{orderId} paths (the response field `id`)."""
    return str(order.get("id") or order.get("orderId") or "").strip()


def _item_id(item):
    """Position ID from v2 order details, with legacy payload compatibility."""
    return str(item.get("id") or item.get("itemId") or "").strip()


def _order_items(order):
    return _as_list(order, "items", "orderItems")


def _parse_dt(value):
    if not value:
        return datetime.max.replace(tzinfo=UTC)
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except ValueError:
        return datetime.max.replace(tzinfo=UTC)


def _cutoff(order):
    delivery = order.get("deliveryMethod") or {}
    return delivery.get("cutOff") or order.get("cutOff")


def _assembly_allowed(order):
    explicit = order.get("allowedForAssembly")
    if explicit is False:
        return False
    if explicit is True:
        return True
    status = str(order.get("status") or "").upper()
    allowed = {"NEW", "CREATED", "PENDING", "CONFIRMED", "READY_FOR_ASSEMBLY"}
    return status in allowed


def _labels_allowed(order):
    if order.get("allowedForAssembly") is True:
        return True
    return str(order.get("status") or "").upper() in LABEL_ELIGIBLE_STATUSES


async def discover_orders(client=None):
    client = client or get_client()
    orders = await client.list_orders(sellerId=client.seller_id, fulfillmentType="FBS")
    order_ids = [_order_id(order) for order in orders if _order_id(order)]
    with session_scope() as session:
        recoverable = set(session.execute(
            select(LamodaOrder.order_id).where(
                LamodaOrder.order_id.in_(order_ids),
                LamodaOrder.preparation_state.in_({"ASSEMBLED", "PACKS_CREATED"}),
            )
        ).scalars()) if order_ids else set()
    eligible = [
        order for order in orders
        if _order_id(order) and (
            _assembly_allowed(order)
            or (_order_id(order) in recoverable and _labels_allowed(order))
        )
    ]
    eligible.sort(key=lambda row: (_parse_dt(_cutoff(row)), _parse_dt(row.get("createdAt"))))
    details = []
    for order in eligible:
        detail = await client.get_order(_order_resource_id(order))
        item_ids = [_item_id(item) for item in _order_items(detail)]
        with session_scope() as session:
            existing = set(session.execute(select(LamodaPack.item_id).where(LamodaPack.item_id.in_(item_ids))).scalars()) if item_ids else set()
        if (
            _assembly_allowed(detail)
            or (_order_id(detail) in recoverable and _labels_allowed(detail))
        ) and any(item_id not in existing for item_id in item_ids):
            details.append(detail)
    return details


def order_summary(orders):
    item_count = sum(len(_order_items(order)) for order in orders)
    cutoffs = [_parse_dt(_cutoff(order)) for order in orders if _cutoff(order)]
    nearest = min(cutoffs) if cutoffs else None
    return {"orders": len(orders), "items": item_count, "nearest_cutoff": nearest}


def _pack_numbers(payload):
    rows = _as_list(payload, "packs", "items")
    result = []
    for row in rows:
        if isinstance(row, str):
            result.append(row)
        elif isinstance(row, dict):
            value = row.get("packNumber") or row.get("packId") or row.get("barcode")
            if value:
                result.append(str(value))
    return result


async def _existing_pack_numbers(client, order_id):
    try:
        return sorted(await client.existing_order_pack_numbers(order_id))
    except Exception:
        logger.warning("Could not recover existing Lamoda packs: order_id=%s", order_id, exc_info=True)
        return []


async def prepare_orders(orders, user_id, user_name, client=None):
    """Create one assembly pack per item, preserving successes across order failures."""
    client = client or get_client()
    active = get_active_session()
    excluded_orders = await reconcile_assembly_session(active.id, client) if active else []
    assembly_session = create_assembly_session(user_id, user_name)
    successes, errors = [], []
    for order in orders:
        order_id = _order_id(order)
        resource_id = _order_resource_id(order)
        items = _order_items(order)
        persist_order(order, items)
        item_ids_all = [_item_id(item) for item in items]
        with session_scope() as session:
            existing = set(session.execute(select(LamodaPack.item_id).where(LamodaPack.item_id.in_(item_ids_all))).scalars())
        pending_items = [item for item in items if _item_id(item) not in existing]
        if not pending_items:
            successes.append(order_id)
            continue
        item_ids = sorted(_item_id(item) for item in pending_items)
        try:
            preparation = get_order_preparation(order_id)
            if preparation["state"] in {"ASSEMBLY_REQUESTED", "PACKS_REQUESTED", "NEEDS_RECONCILIATION"}:
                raise RuntimeError(
                    f"По заказу {order_id} есть неопределённый результат API; "
                    "повтор заблокирован до сверки."
                )
            if preparation["state"] not in {"ASSEMBLED", "PACKS_CREATED"}:
                set_order_preparation(order_id, "ASSEMBLY_REQUESTED")
                try:
                    await client.create_assembly(resource_id, [{"itemIds": [item_id]} for item_id in item_ids])
                except Exception as error:
                    state = "NEEDS_RECONCILIATION" if getattr(error, "uncertain", False) else "PREPARATION_FAILED"
                    set_order_preparation(order_id, state, error=str(error))
                    raise
                set_order_preparation(order_id, "ASSEMBLED")
            if preparation["state"] == "PACKS_CREATED":
                numbers = sorted(str(value) for value in preparation["data"].get("pack_numbers", []))
            else:
                numbers = (
                    await _existing_pack_numbers(client, order_id)
                    if preparation["state"] == "ASSEMBLED"
                    else []
                )
                if not numbers:
                    set_order_preparation(order_id, "PACKS_REQUESTED")
                    try:
                        packs_payload = await client.create_packs(resource_id, len(item_ids))
                        numbers = sorted(_pack_numbers(packs_payload))
                    except LamodaValidationError as error:
                        numbers = await _existing_pack_numbers(client, order_id)
                        if not numbers:
                            set_order_preparation(order_id, "ASSEMBLED", error=str(error))
                            raise
                    except Exception as error:
                        state = "NEEDS_RECONCILIATION" if getattr(error, "uncertain", False) else "ASSEMBLED"
                        set_order_preparation(order_id, state, error=str(error))
                        raise
            if len(numbers) != len(item_ids):
                error = RuntimeError(
                    f"Lamoda вернула {len(numbers)} packNumber для {len(item_ids)} товаров заказа {order_id}."
                )
                set_order_preparation(order_id, "ASSEMBLED", error=str(error))
                raise error
            set_order_preparation(order_id, "PACKS_CREATED", data={"pack_numbers": numbers})
            attach_packs(assembly_session.id, [
                {"order_id": order_id, "item_id": item_id, "pack_number": pack_number}
                for item_id, pack_number in zip(item_ids, numbers, strict=True)
            ])
            set_order_preparation(order_id, "PREPARED", data={"pack_numbers": numbers})
            successes.append(order_id)
        except Exception as error:
            logger.exception("Lamoda order preparation failed: order_id=%s", order_id)
            errors.append({"order_id": order_id, "error": str(error)})
    return {
        "session_id": assembly_session.id, "successes": successes, "errors": errors,
        "excluded_orders": excluded_orders,
    }


def _chunks(values, size=MAX_LABEL_BATCH):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _label_result(payload, excluded_key):
    file_url = payload.get("fileUrl") or payload.get("url") if isinstance(payload, dict) else ""
    excluded = payload.get(excluded_key) or [] if isinstance(payload, dict) else []
    return file_url, [str(value) for value in excluded]


async def assembly_label_documents(session_id, client=None):
    client = client or get_client()
    excluded_orders = await reconcile_assembly_session(session_id, client)
    packs = get_session_packs(session_id)
    if not packs:
        if excluded_orders:
            details = ", ".join(f"{row['order_id']} ({row['status']})" for row in excluded_orders)
            raise RuntimeError(f"После сверки с Lamoda в сборке не осталось подходящих заказов: {details}.")
        raise RuntimeError("В сборке нет упаковок.")
    item_parts, pack_parts, excluded_items, excluded_packs = [], [], [], []
    for batch in _chunks([row["item_id"] for row in packs]):
        payload = await client.order_item_labels(batch, LABEL_SIZE_ITEM)
        url, excluded = _label_result(payload, "excludedItems")
        excluded_items.extend(excluded)
        if url:
            item_parts.append(await client.download_pdf(url))
        else:
            raise LamodaDocumentError("Lamoda не вернула fileUrl товарных этикеток.")
    for batch in _chunks([row["pack_number"] for row in packs]):
        payload = await client.order_pack_labels(batch, LABEL_SIZE_PACK)
        url, excluded = _label_result(payload, "excludedPacks")
        excluded_packs.extend(excluded)
        if url:
            pack_parts.append(await client.download_pdf(url))
        else:
            raise LamodaDocumentError("Lamoda не вернула fileUrl паковых этикеток.")
    if not item_parts or not pack_parts:
        raise LamodaDocumentError("Lamoda не сформировала полный комплект этикеток.")
    return {
        "item_pdf": merge_pdfs(item_parts), "pack_pdf": merge_pdfs(pack_parts),
        "excluded_items": excluded_items, "excluded_packs": excluded_packs,
        "excluded_orders": excluded_orders,
    }


async def reconcile_assembly_session(session_id, client=None):
    """Remove terminal, unprocessed orders before requesting assembly labels."""
    client = client or get_client()
    excluded = []
    for order_ref in get_session_order_refs(session_id):
        detail = await client.get_order(order_ref["lamoda_id"])
        status = str(detail.get("status") or "").upper()
        if _labels_allowed(detail):
            continue
        cancel_session_order_packs(session_id, order_ref["order_id"], status or "UNKNOWN")
        excluded.append({"order_id": order_ref["order_id"], "status": status or "UNKNOWN"})
    return excluded


def assembly_picking_document(session_id):
    packs = get_session_packs(session_id)
    if not packs:
        raise RuntimeError("В сборке нет товаров для листа подбора.")
    return create_picking_list_pdf(packs, session_id)


def shipment_request(session_id):
    manifest = validate_cargo_complete(session_id)
    pallets = []
    for cargo in manifest:
        pallets.append({
            "packs": [
                {
                    "packId": pack["pack_number"],
                    "items": [{"unitload": pack["item_id"]}],
                }
                for pack in cargo["packs"]
            ]
        })
    return manifest, pallets


def _response_pallets(payload):
    return _as_list(payload, "pallets")


def _pallet_pack_set(pallet):
    values = set()
    for pack in _as_list(pallet, "packs", "items"):
        if isinstance(pack, str):
            values.add(pack)
        elif isinstance(pack, dict):
            value = pack.get("packId") or pack.get("packNumber") or pack.get("barcode")
            if value:
                values.add(str(value))
    return values


def match_pallets(manifest, response_pallets):
    response_by_set = {}
    for pallet in response_pallets:
        pack_set = frozenset(_pallet_pack_set(pallet))
        pallet_id = pallet.get("palletId") or pallet.get("palletBarcode") or pallet.get("id")
        if not pack_set or not pallet_id or pack_set in response_by_set:
            raise RuntimeError("Lamoda вернула неоднозначный состав грузовых мест.")
        response_by_set[pack_set] = str(pallet_id)
    mapping = {}
    for cargo in manifest:
        expected = frozenset(pack["pack_number"] for pack in cargo["packs"])
        if expected not in response_by_set:
            raise RuntimeError(
                f"Не удалось сопоставить грузовое место №{cargo['local_number']} по составу упаковок."
            )
        mapping[cargo["id"]] = response_by_set[expected]
    if len(mapping) != len(response_pallets):
        raise RuntimeError("Lamoda вернула лишние грузовые места.")
    return mapping


async def create_lamoda_shipment(session_id, client=None):
    client = client or get_client()
    with session_scope() as session:
        existing = session.execute(select(Shipment).where(Shipment.session_id == int(session_id))).scalar_one_or_none()
        if existing:
            return existing.shipment_id
    request_state, request_error = get_shipment_request_state(session_id)
    if request_state in {"REQUESTED", "NEEDS_RECONCILIATION"}:
        reconciled = await reconcile_uncertain_shipment(session_id, client)
        if reconciled:
            return reconciled
        raise RuntimeError(
            "Результат предыдущего POST отгрузки неопределён. "
            "Повторная отправка заблокирована до сверки с Lamoda."
            + (f" Ошибка: {request_error}" if request_error else "")
        )
    manifest, pallets = shipment_request(session_id)
    ship_at = datetime.now(UTC)
    set_shipment_request_state(session_id, "REQUESTED")
    try:
        payload = await client.create_shipment(ship_at.isoformat().replace("+00:00", "Z"), pallets)
    except Exception as error:
        state = "NEEDS_RECONCILIATION" if getattr(error, "uncertain", False) else "READY"
        set_shipment_request_state(session_id, state, str(error))
        raise
    shipment_id = str(payload.get("shipmentId") or payload.get("id") or "")
    if not shipment_id:
        raise RuntimeError("Lamoda создала отгрузку без shipmentId. Требуется сверка.")
    response_pallets = _response_pallets(payload)
    if not response_pallets:
        detail = await client.get_shipment(shipment_id)
        response_pallets = _response_pallets(detail)
    mapping = match_pallets(manifest, response_pallets)
    save_shipment(
        session_id, shipment_id, ship_at.replace(tzinfo=None), payload, mapping,
    )
    return shipment_id


async def reconcile_uncertain_shipment(session_id, client=None):
    """Find an already-created shipment by its exact pallet/pack composition."""
    client = client or get_client()
    manifest = validate_cargo_complete(session_id)
    candidates = []
    for summary in await client.list_shipments(sellerId=client.seller_id):
        shipment_id = str(summary.get("shipmentId") or summary.get("id") or "")
        if not shipment_id:
            continue
        try:
            detail = await client.get_shipment(shipment_id)
            mapping = match_pallets(manifest, _response_pallets(detail))
        except Exception:
            continue
        candidates.append((shipment_id, detail, mapping))
    if len(candidates) != 1:
        return None
    shipment_id, detail, mapping = candidates[0]
    created_at = _parse_dt(detail.get("createdAt"))
    if created_at == datetime.max.replace(tzinfo=UTC):
        created_at = datetime.now(UTC)
    save_shipment(
        session_id, shipment_id, created_at.astimezone(UTC).replace(tzinfo=None),
        detail, mapping,
    )
    return shipment_id


async def shipment_documents(session_id, client=None):
    client = client or get_client()
    with session_scope() as session:
        shipment = session.execute(select(Shipment).where(Shipment.session_id == int(session_id))).scalar_one_or_none()
        if not shipment:
            raise RuntimeError("Отгрузка ещё не создана.")
        shipment_id = shipment.shipment_id
    manifest = cargo_manifest(session_id)
    documents = []
    for cargo in manifest:
        if not cargo.get("pallet_id"):
            raise RuntimeError(f"Для грузового места №{cargo['local_number']} не сохранён palletId.")
        payload = await client.pallet_labels([cargo["pallet_id"]], LABEL_SIZE_PALLET)
        url, excluded = _label_result(payload, "excludedPallets")
        if excluded:
            raise LamodaDocumentError(f"Lamoda исключила pallet: {', '.join(excluded)}")
        content = await client.download_pdf(url)
        documents.append({
            "local_number": cargo["local_number"], "pallet_id": cargo["pallet_id"],
            "pack_count": len(cargo["packs"]), "content": content,
        })
    return {
        "shipment_id": shipment_id, "pallet_documents": documents,
        "manifest_pdf": create_manifest_pdf(manifest, shipment_id),
    }


def create_marking_documents(batch_type, user_id, user_name=""):
    batch_id = create_marking_batch(batch_type, user_id, user_name)
    rows = marking_batch_rows(batch_id)
    return {
        "batch_id": batch_id, "rows": rows,
        "pdf": create_marking_pdf(rows, batch_type),
        "xlsx": create_marking_xlsx(rows, batch_type),
    }


async def sync_lamoda_statuses(client=None):
    client = client or get_client()
    now = datetime.now(UTC)
    cursor_text = get_sync_value("orders_updated_at", "")
    try:
        cursor = datetime.fromisoformat(cursor_text.replace("Z", "+00:00"))
    except ValueError:
        cursor = now - timedelta(days=7)
    updated_from = min(cursor, now) - timedelta(minutes=5)
    orders = await client.list_orders(
        sellerId=client.seller_id,
        fulfillmentType="FBS",
        updatedAtFrom=updated_from.isoformat().replace("+00:00", "Z"),
    )
    changed = 0
    for order in orders:
        order_id = _order_id(order)
        if not order_id:
            continue
        detail = await client.get_order(order_id)
        items = _order_items(detail)
        persist_order(detail, items)
        for item in items:
            item_id = str(item.get("itemId") or item.get("id") or "")
            if not item_id:
                continue
            with session_scope() as session:
                pack_number = session.execute(
                    select(LamodaPack.pack_number).where(LamodaPack.item_id == item_id)
                ).scalar_one_or_none()
            if pack_number and update_pack_lamoda_status(pack_number, item.get("status")):
                changed += 1
        statuses = await client.get_order_item_statuses(order_id)
        status_rows = _as_list(statuses, "items", "itemStatuses", "statuses")
        status_rows.sort(key=lambda row: _parse_dt(row.get("createdAt")))
        for status_row in status_rows:
            item_id = str(status_row.get("itemId") or status_row.get("id") or "")
            status = status_row.get("status") or status_row.get("itemStatus")
            with session_scope() as session:
                statement = (
                    select(LamodaPack.pack_number)
                    .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
                    .where(LamodaPack.order_id == order_id)
                )
                if item_id:
                    statement = statement.where(LamodaPack.item_id == item_id)
                else:
                    sku = str(status_row.get("sku") or "")
                    external_sku = str(status_row.get("externalSku") or "")
                    sku_filters = []
                    if sku:
                        sku_filters.append(LamodaOrderItem.sku == sku)
                    if external_sku:
                        sku_filters.append(LamodaOrderItem.external_sku == external_sku)
                    if not sku_filters:
                        continue
                    statement = statement.where(or_(*sku_filters))
                pack_number = session.execute(statement.limit(1)).scalar_one_or_none()
            if pack_number and update_pack_lamoda_status(pack_number, status):
                changed += 1
    return_items = await client.list_return_items(
        sellerId=client.seller_id,
        updatedAtFrom=updated_from.isoformat().replace("+00:00", "Z"),
    )
    for item in return_items:
        item_id = str(item.get("orderItemId") or item.get("itemId") or "")
        with session_scope() as session:
            pack_number = session.execute(select(LamodaPack.pack_number).where(LamodaPack.item_id == item_id)).scalar_one_or_none()
        if pack_number and update_pack_lamoda_status(pack_number, item.get("status") or "RETURN"):
            update_pack_return_info(pack_number, item)
            changed += 1
    set_sync_value("orders_updated_at", now.isoformat().replace("+00:00", "Z"))
    return {"orders": len(orders), "return_items": len(return_items), "changed": changed}


async def resolve_return_barcode(barcode, client=None):
    """Resolve locally first, then via container and return-items."""
    from modules.lamoda_fbs.storage import find_pack

    value = str(barcode or "").strip()
    local = find_pack(value)
    if local:
        return local
    client = client or get_client()
    try:
        container = await client.get_order_container(value)
    except Exception:
        container = {}
    candidates = _as_list(container, "packs", "items", "orderItems")
    for candidate in candidates:
        pack_number = candidate.get("packNumber") or candidate.get("packId")
        local = find_pack(pack_number)
        if local:
            return local
    return_items = await client.list_return_items(sellerId=client.seller_id)
    for item in return_items:
        identifiers = {
            str(item.get("id") or ""), str(item.get("itemId") or ""),
            str(item.get("orderId") or ""), str(item.get("sku") or ""),
            str(item.get("externalSku") or ""),
        }
        if value not in identifiers:
            continue
        item_id = str(item.get("orderItemId") or item.get("itemId") or "")
        with session_scope() as session:
            pack_number = session.execute(select(LamodaPack.pack_number).where(LamodaPack.item_id == item_id)).scalar_one_or_none()
        local = find_pack(pack_number) if pack_number else None
        if local:
            local.update({
                "return_item_id": str(item.get("returnItemId") or item.get("id") or ""),
                "return_type": str(item.get("returnType") or ""),
                "return_status": str(item.get("status") or ""),
                "return_date": item.get("returnDate"),
            })
            return local
    return None


async def resolve_return_item_barcode(barcode, client=None):
    """Resolve the mandatory order-item label used on every Lamoda return."""
    from modules.lamoda_fbs.storage import find_pack_by_item_id

    value = str(barcode or "").strip()
    local = find_pack_by_item_id(value)
    client = client or get_client()
    try:
        return_items = await client.list_return_items(sellerId=client.seller_id)
    except Exception:
        if local:
            logger.warning("Could not refresh Lamoda return item: item_id=%s", value, exc_info=True)
            return local
        raise
    for item in return_items:
        identifiers = {
            str(item.get("id") or ""),
            str(item.get("orderItemId") or ""),
            str(item.get("itemId") or ""),
        }
        if value not in identifiers:
            continue
        item_id = str(item.get("orderItemId") or item.get("itemId") or item.get("id") or "")
        local = find_pack_by_item_id(item_id)
        if not local:
            return {
                "pack_number": "", "order_id": str(item.get("orderId") or ""),
                "item_id": item_id, "product_name": str(item.get("itemName") or ""),
                "size": str(item.get("dimensionSymbol") or ""),
                "sku": str(item.get("externalSku") or item.get("sku") or ""),
                "raw_code": "", "fingerprint": "", "lamoda_status": str(item.get("status") or ""),
                "return_item_id": str(item.get("returnItemId") or item.get("id") or ""),
                "return_type": str(item.get("returnType") or ""), "return_status": str(item.get("status") or ""),
                "return_date": item.get("returnDate"),
            }
        local.update({
            "return_item_id": str(item.get("returnItemId") or item.get("id") or ""),
            "return_type": str(item.get("returnType") or ""),
            "return_status": str(item.get("status") or ""),
            "return_date": item.get("returnDate"),
        })
        return local
    return local


async def resolve_return_order(order_id, client=None):
    from modules.lamoda_fbs.storage import find_packs_by_order

    value = str(order_id or "").strip()
    local = find_packs_by_order(value)
    if local:
        return local
    client = client or get_client()
    rows = await client.list_return_items(sellerId=client.seller_id, orderId=value)
    result = []
    for item in rows:
        item_id = str(item.get("itemId") or "")
        with session_scope() as session:
            pack_number = session.execute(
                select(LamodaPack.pack_number).where(LamodaPack.item_id == item_id)
            ).scalar_one_or_none()
        if pack_number:
            from modules.lamoda_fbs.storage import find_pack
            pack = find_pack(pack_number)
            if pack:
                pack.update({
                    "return_item_id": str(item.get("id") or ""),
                    "return_type": str(item.get("returnType") or ""),
                    "return_status": str(item.get("status") or ""),
                    "return_date": item.get("returnDate"),
                })
                result.append(pack)
    return result
