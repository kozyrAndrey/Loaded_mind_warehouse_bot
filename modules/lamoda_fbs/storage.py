import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from modules.lamoda_fbs.constants import CodeState, PackState
from modules.marking.duplicate_chz import DuplicateChzError, extract_short_marking_code
from modules.storage.postgres import Base, get_engine, session_scope


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class LamodaOrder(Base):
    __tablename__ = "lamoda_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    lamoda_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at_lamoda: Mapped[datetime | None] = mapped_column(DateTime)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at_lamoda: Mapped[datetime | None] = mapped_column(DateTime)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    preparation_state: Mapped[str] = mapped_column(String(50), nullable=False, default="NEW")
    preparation_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preparation_data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LamodaOrderItem(Base):
    __tablename__ = "lamoda_order_items"
    __table_args__ = (Index("ix_lamoda_order_items_order_id", "order_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("lamoda_orders.order_id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    external_sku: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    product_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    moysklad_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    size: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class AssemblySession(Base):
    __tablename__ = "lamoda_assembly_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ASSEMBLING")
    labels_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shipment_request_state: Mapped[str] = mapped_column(String(50), nullable=False, default="READY")
    shipment_request_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MarkingCode(Base):
    __tablename__ = "lamoda_marking_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    raw_code: Mapped[str] = mapped_column(Text, nullable=False)
    uit: Mapped[str] = mapped_column(Text, nullable=False, default="")
    gtin: Mapped[str] = mapped_column(String(14), nullable=False, default="")
    serial: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(String(50), nullable=False, default=CodeState.AVAILABLE)
    current_pack_number: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class LamodaPack(Base):
    __tablename__ = "lamoda_packs"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_lamoda_pack_item"),
        Index("ix_lamoda_packs_session", "session_id"),
        Index("ix_lamoda_packs_state", "marking_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    item_id: Mapped[str] = mapped_column(ForeignKey("lamoda_order_items.item_id"), nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("lamoda_orders.order_id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("lamoda_assembly_sessions.id"), nullable=False)
    marking_code_id: Mapped[int | None] = mapped_column(ForeignKey("lamoda_marking_codes.id"))
    scanned_raw_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kiz_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    scanned_gtin: Mapped[str] = mapped_column(String(14), nullable=False, default="")
    scanned_serial: Mapped[str] = mapped_column(Text, nullable=False, default="")
    marking_state: Mapped[str] = mapped_column(String(50), nullable=False, default=PackState.ASSEMBLING)
    lamoda_status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_item_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_date: Mapped[datetime | None] = mapped_column(DateTime)
    pack_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime)
    kiz_scanned_at: Mapped[datetime | None] = mapped_column(DateTime)
    requires_marking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)
    return_received_at: Mapped[datetime | None] = mapped_column(DateTime)
    reintroduced_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class CargoPlace(Base):
    __tablename__ = "lamoda_cargo_places"
    __table_args__ = (UniqueConstraint("session_id", "local_number", name="uq_lamoda_cargo_local"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("lamoda_assembly_sessions.id"), nullable=False)
    local_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="OPEN")
    pallet_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CargoPlacePack(Base):
    __tablename__ = "lamoda_cargo_place_packs"
    __table_args__ = (UniqueConstraint("pack_number", name="uq_lamoda_cargo_pack"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cargo_place_id: Mapped[int] = mapped_column(ForeignKey("lamoda_cargo_places.id"), nullable=False)
    pack_number: Mapped[str] = mapped_column(ForeignKey("lamoda_packs.pack_number"), nullable=False)
    scanned_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Shipment(Base):
    __tablename__ = "lamoda_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("lamoda_assembly_sessions.id"), unique=True, nullable=False)
    shipment_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    ship_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(100), nullable=False, default="CREATED")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MarkingBatch(Base):
    __tablename__ = "lamoda_marking_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="EXPORTED")
    created_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(100))
    confirmed_by_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class MarkingBatchItem(Base):
    __tablename__ = "lamoda_marking_batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "pack_number", name="uq_lamoda_batch_pack"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("lamoda_marking_batches.id"), nullable=False)
    pack_number: Mapped[str] = mapped_column(ForeignKey("lamoda_packs.pack_number"), nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    error_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ReturnReceipt(Base):
    __tablename__ = "lamoda_return_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_number: Mapped[str | None] = mapped_column(String(100))
    order_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    item_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_item_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_status: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    return_date: Mapped[datetime | None] = mapped_column(DateTime)
    condition: Mapped[str] = mapped_column(String(20), nullable=False)
    defect_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    label_photo_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    scanned_kiz_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    problematic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    problem_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    received_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ReturnPhoto(Base):
    __tablename__ = "lamoda_return_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("lamoda_return_receipts.id"), nullable=False)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    photo_type: Mapped[str] = mapped_column(String(30), nullable=False, default="DEFECT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SyncState(Base):
    __tablename__ = "lamoda_sync_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class OperationLog(Base):
    __tablename__ = "lamoda_operation_log"
    __table_args__ = (Index("ix_lamoda_operation_log_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="SYSTEM")
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class CancellationNotice(Base):
    __tablename__ = "lamoda_cancellation_notices"
    __table_args__ = (
        UniqueConstraint("service_type", "service_date", name="uq_lamoda_cancellation_notice"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_type: Mapped[str] = mapped_column(String(30), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SENDING")
    recipient: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


LAMODA_TABLES = [
    LamodaOrder.__table__, LamodaOrderItem.__table__, AssemblySession.__table__,
    MarkingCode.__table__, LamodaPack.__table__, CargoPlace.__table__,
    CargoPlacePack.__table__, Shipment.__table__, MarkingBatch.__table__,
    MarkingBatchItem.__table__, ReturnReceipt.__table__, ReturnPhoto.__table__,
    SyncState.__table__, OperationLog.__table__, CancellationNotice.__table__,
]


def init_lamoda_storage():
    Base.metadata.create_all(get_engine(), tables=LAMODA_TABLES)
    ensure_lamoda_storage_schema()


def ensure_lamoda_storage_schema():
    statements = [
        (
            "alter table lamoda_order_items add column if not exists "
            "moysklad_name text not null default ''"
        ),
        "alter table lamoda_packs add column if not exists packed_at timestamp",
        (
            "alter table lamoda_packs add column if not exists "
            "requires_marking boolean not null default true"
        ),
        (
            "update lamoda_packs set packed_at = kiz_scanned_at "
            "where packed_at is null and kiz_scanned_at is not null"
        ),
    ]
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def json_dumps(value):
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def sold_price_from_item_json(raw_json):
    try:
        item = json.loads(raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    price_container = item.get("price") if isinstance(item.get("price"), dict) else {}
    for field in ("paidPrice", "salePrice", "sellerAgreedPrice", "basePrice"):
        price = item.get(field) or price_container.get(field)
        if not isinstance(price, dict) or price.get("amount") in (None, ""):
            continue
        try:
            value = Decimal(str(price["amount"])) / Decimal("100")
        except (InvalidOperation, TypeError, ValueError):
            continue
        return int(value) if value == value.to_integral_value() else float(value)
    return None


def short_marking_code(code, raw_code):
    try:
        return extract_short_marking_code(raw_code)
    except DuplicateChzError:
        return str(code.uit or "")[:31]


def code_fingerprint(raw_code):
    value = str(raw_code or "").strip()
    for source in ("\\x1d", "\\u001d", "<GS>", "[GS]", "{GS}", "␝"):
        value = value.replace(source, "\x1d")
    value = value.replace(" ", "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def log_operation(session, operation, entity_type, entity_id, user_id="SYSTEM", details=None):
    session.add(OperationLog(
        operation=operation,
        entity_type=entity_type,
        entity_id=str(entity_id),
        user_id=str(user_id or "SYSTEM"),
        details_json=json_dumps(details),
    ))


def get_active_session():
    with session_scope() as session:
        return session.execute(
            select(AssemblySession)
            .where(AssemblySession.status.in_(["ASSEMBLING", "CARGO"]))
            .order_by(AssemblySession.id.desc())
        ).scalars().first()


def create_assembly_session(user_id, user_name):
    with session_scope() as session:
        existing = session.execute(
            select(AssemblySession)
            .where(AssemblySession.status.in_(["ASSEMBLING", "CARGO"]))
            .order_by(AssemblySession.id.desc())
        ).scalars().first()
        if existing:
            started = session.scalar(
                select(func.count()).select_from(LamodaPack).where(
                    LamodaPack.session_id == existing.id,
                    or_(LamodaPack.pack_scanned_at.is_not(None), LamodaPack.packed_at.is_not(None)),
                )
            ) or 0
            if existing.status == "CARGO" or existing.labels_ready or started:
                raise RuntimeError(
                    f"Сборка №{existing.id} уже начата. Завершите её перед подготовкой новых заказов."
                )
            return existing
        row = AssemblySession(created_by_user_id=str(user_id), created_by_name=str(user_name or ""))
        session.add(row)
        session.flush()
        log_operation(session, "SESSION_CREATED", "assembly_session", row.id, user_id)
        return row


def set_session_labels_ready(session_id, ready):
    with session_scope() as session:
        row = session.get(AssemblySession, int(session_id))
        if not row:
            raise RuntimeError("Сессия сборки не найдена.")
        row.labels_ready = bool(ready)
        row.updated_at = utcnow()


def get_shipment_request_state(session_id):
    with session_scope() as session:
        row = session.get(AssemblySession, int(session_id))
        if not row:
            raise RuntimeError("Сессия сборки не найдена.")
        return row.shipment_request_state, row.shipment_request_error


def set_shipment_request_state(session_id, state, error=""):
    with session_scope() as session:
        row = session.execute(
            select(AssemblySession).where(AssemblySession.id == int(session_id)).with_for_update()
        ).scalar_one()
        row.shipment_request_state = str(state)
        row.shipment_request_error = str(error or "")[:4000]
        row.updated_at = utcnow()
        log_operation(session, "SHIPMENT_REQUEST_STATE", "assembly_session", session_id, details={"state": state})


def persist_order(order, items):
    order_id = str(order.get("orderId") or order.get("id") or "").strip()
    lamoda_id = str(order.get("id") or order_id).strip()
    if not order_id:
        raise ValueError("Lamoda order has no orderId")
    with session_scope() as session:
        row = session.execute(select(LamodaOrder).where(LamodaOrder.order_id == order_id)).scalar_one_or_none()
        if not row:
            row = LamodaOrder(order_id=order_id, lamoda_id=lamoda_id)
            session.add(row)
        row.lamoda_id = lamoda_id
        row.status = str(order.get("status") or "")
        row.created_at_lamoda = parse_api_datetime(order.get("createdAt"))
        row.updated_at_lamoda = parse_api_datetime(order.get("updatedAt"))
        row.cutoff_at = parse_api_datetime((order.get("deliveryMethod") or {}).get("cutOff") or order.get("cutOff"))
        row.raw_json = json_dumps(order)
        row.updated_at = utcnow()
        for data in items:
            item_id = str(data.get("id") or data.get("itemId") or "").strip()
            if not item_id:
                continue
            item = session.execute(select(LamodaOrderItem).where(LamodaOrderItem.item_id == item_id)).scalar_one_or_none()
            if not item:
                item = LamodaOrderItem(item_id=item_id, order_id=order_id)
                session.add(item)
            item.order_id = order_id
            item.sku = str(data.get("sku") or data.get("sellerSku") or "")
            item.external_sku = str(data.get("externalSku") or data.get("sellerSku") or "")
            item.product_name = str(
                data.get("name") or data.get("productName") or data.get("title") or data.get("description") or ""
            )
            if "_moysklad_name" in data:
                item.moysklad_name = str(data.get("_moysklad_name") or "")
            item.size = str(data.get("size") or data.get("sizeName") or "")
            item.status = str(data.get("status") or "")
            item.raw_json = json_dumps(data)
            item.updated_at = utcnow()


def get_order_preparation(order_id):
    with session_scope() as session:
        row = session.execute(select(LamodaOrder).where(LamodaOrder.order_id == str(order_id))).scalar_one_or_none()
        if not row:
            return {"state": "NEW", "error": "", "data": {}}
        try:
            data = json.loads(row.preparation_data_json or "{}")
        except json.JSONDecodeError:
            data = {}
        return {"state": row.preparation_state, "error": row.preparation_error, "data": data}


def set_order_preparation(order_id, state, *, error="", data=None):
    with session_scope() as session:
        row = session.execute(
            select(LamodaOrder).where(LamodaOrder.order_id == str(order_id)).with_for_update()
        ).scalar_one()
        row.preparation_state = str(state)
        row.preparation_error = str(error or "")[:4000]
        if data is not None:
            row.preparation_data_json = json_dumps(data)
        row.updated_at = utcnow()


def attach_packs(session_id, pack_rows):
    """Persist deterministic itemId -> packNumber mapping; safe to call repeatedly."""
    created = 0
    with session_scope() as session:
        for data in pack_rows:
            item_id = str(data["item_id"])
            pack_number = str(data["pack_number"])
            order_id = str(data["order_id"])
            existing_item = session.execute(select(LamodaPack).where(LamodaPack.item_id == item_id)).scalar_one_or_none()
            existing_pack = session.execute(select(LamodaPack).where(LamodaPack.pack_number == pack_number)).scalar_one_or_none()
            if existing_item:
                if existing_item.pack_number != pack_number:
                    raise RuntimeError(f"Для itemId {item_id} уже сохранёна другая упаковка.")
                continue
            if existing_pack:
                raise RuntimeError(f"Номер упаковки {pack_number} уже занят.")
            session.add(LamodaPack(
                pack_number=pack_number,
                item_id=item_id,
                order_id=order_id,
                session_id=int(session_id),
            ))
            created += 1
        if created:
            assembly = session.get(AssemblySession, int(session_id))
            if assembly:
                assembly.labels_ready = False
                assembly.updated_at = utcnow()
        log_operation(session, "PACKS_ATTACHED", "assembly_session", session_id, details={"created": created})
    return created


def get_session_packs(session_id, *, include_cancelled=False):
    with session_scope() as session:
        statement = (
            select(LamodaPack, LamodaOrderItem)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .where(LamodaPack.session_id == int(session_id))
            .order_by(LamodaPack.id)
        )
        if not include_cancelled:
            statement = statement.where(LamodaPack.marking_state != PackState.CANCELLED)
        rows = session.execute(statement).all()
        return [pack_view(pack, item) for pack, item in rows]


def get_session_order_refs(session_id):
    with session_scope() as session:
        rows = session.execute(
            select(LamodaOrder.order_id, LamodaOrder.lamoda_id)
            .join(LamodaPack, LamodaPack.order_id == LamodaOrder.order_id)
            .where(
                LamodaPack.session_id == int(session_id),
                LamodaPack.marking_state != PackState.CANCELLED,
            )
            .distinct()
            .order_by(LamodaOrder.order_id)
        ).all()
        return [
            {"order_id": str(order_id), "lamoda_id": str(lamoda_id or order_id)}
            for order_id, lamoda_id in rows
        ]


def cancel_session_order_packs(session_id, order_id, lamoda_status):
    """Exclude unprocessed packs of a terminal Lamoda order from assembly."""
    with session_scope() as session:
        packs = session.execute(
            select(LamodaPack).where(
                LamodaPack.session_id == int(session_id),
                LamodaPack.order_id == str(order_id),
                LamodaPack.marking_state != PackState.CANCELLED,
            ).with_for_update()
        ).scalars().all()
        changed = 0
        for pack in packs:
            if pack.packed_at or pack.pack_scanned_at or pack.marking_code_id:
                raise RuntimeError(
                    f"Заказ {order_id} уже начали собирать, но его статус Lamoda — {lamoda_status}. "
                    "Требуется ручная сверка."
                )
            pack.lamoda_status = str(lamoda_status or "")
            pack.marking_state = PackState.CANCELLED
            pack.updated_at = utcnow()
            changed += 1
        if changed:
            assembly = session.get(AssemblySession, int(session_id))
            if assembly:
                assembly.labels_ready = False
                assembly.updated_at = utcnow()
            log_operation(
                session, "ORDER_EXCLUDED_FROM_ASSEMBLY", "assembly_session", session_id,
                details={"order_id": order_id, "status": lamoda_status, "packs": changed},
            )
        return changed


def pack_view(pack, item=None):
    product_name = (item.moysklad_name or item.product_name) if item else ""
    return {
        "id": pack.id,
        "pack_number": pack.pack_number,
        "item_id": pack.item_id,
        "order_id": pack.order_id,
        "session_id": pack.session_id,
        "marking_state": pack.marking_state,
        "lamoda_status": pack.lamoda_status,
        "return_item_id": pack.return_item_id,
        "return_type": pack.return_type,
        "return_status": pack.return_status,
        "return_date": pack.return_date,
        "marking_code_id": pack.marking_code_id,
        "raw_code": pack.scanned_raw_code,
        "fingerprint": pack.kiz_fingerprint,
        "gtin": pack.scanned_gtin,
        "serial": pack.scanned_serial,
        "pack_scanned": bool(pack.pack_scanned_at),
        "packed": bool(pack.packed_at),
        "kiz_scanned": bool(pack.kiz_scanned_at),
        "requires_marking": bool(pack.requires_marking),
        "sku": item.sku if item else "",
        "external_sku": item.external_sku if item else "",
        "product_name": product_name,
        "lamoda_product_name": item.product_name if item else "",
        "moysklad_product_name": item.moysklad_name if item else "",
        "size": item.size if item else "",
    }


def get_next_unscanned_pack(session_id):
    with session_scope() as session:
        row = session.execute(
            select(LamodaPack, LamodaOrderItem)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .where(
                LamodaPack.session_id == int(session_id),
                LamodaPack.packed_at.is_(None),
                LamodaPack.marking_state != PackState.CANCELLED,
            )
            .order_by(LamodaPack.id)
        ).first()
        return pack_view(*row) if row else None


def mark_pack_barcode_scanned(pack_number, expected_pack_number):
    value = str(pack_number).strip()
    if value != str(expected_pack_number):
        raise ValueError("Отсканирована этикетка другого отправления.")
    with session_scope() as session:
        pack = session.execute(
            select(LamodaPack).where(LamodaPack.pack_number == value).with_for_update()
        ).scalar_one()
        pack.pack_scanned_at = utcnow()
        pack.updated_at = utcnow()


def assign_marking_code(pack_number, raw_code, uit, gtin, serial, user_id):
    fingerprint = code_fingerprint(raw_code)
    with session_scope() as session:
        pack = session.execute(
            select(LamodaPack).where(LamodaPack.pack_number == str(pack_number)).with_for_update()
        ).scalar_one()
        if not pack.pack_scanned_at:
            raise RuntimeError("Сначала отсканируйте паковую этикетку.")
        if pack.packed_at and not pack.kiz_scanned_at:
            raise RuntimeError("Товар уже собран как немаркируемый.")
        if pack.kiz_scanned_at:
            if pack.kiz_fingerprint == fingerprint:
                return pack.id
            raise RuntimeError("Для этой упаковки уже отсканирован другой КИЗ.")
        code = session.execute(
            select(MarkingCode).where(MarkingCode.fingerprint == fingerprint).with_for_update()
        ).scalar_one_or_none()
        if code and code.state != CodeState.AVAILABLE:
            raise RuntimeError("Этот КИЗ уже зарезервирован или выведен из оборота.")
        if code and code.current_pack_number and code.current_pack_number != pack.pack_number:
            raise RuntimeError("Этот КИЗ уже привязан к другой упаковке.")
        if not code:
            code = MarkingCode(
                fingerprint=fingerprint, raw_code=raw_code, uit=uit,
                gtin=gtin, serial=serial,
            )
            session.add(code)
            session.flush()
        code.state = CodeState.RESERVED
        code.current_pack_number = pack.pack_number
        code.updated_at = utcnow()
        pack.marking_code_id = code.id
        pack.scanned_raw_code = raw_code
        pack.kiz_fingerprint = fingerprint
        pack.scanned_gtin = gtin
        pack.scanned_serial = serial
        completed_at = utcnow()
        pack.kiz_scanned_at = completed_at
        pack.packed_at = completed_at
        pack.requires_marking = True
        pack.marking_state = PackState.PACKED
        pack.updated_at = utcnow()
        log_operation(session, "KIZ_SCANNED", "pack", pack.pack_number, user_id, {"fingerprint": fingerprint})
        return pack.id


def complete_pack_without_marking(pack_number, user_id):
    with session_scope() as session:
        pack = session.execute(
            select(LamodaPack).where(
                LamodaPack.pack_number == str(pack_number)
            ).with_for_update()
        ).scalar_one()
        if not pack.pack_scanned_at:
            raise RuntimeError("Сначала отсканируйте паковую этикетку.")
        if pack.kiz_scanned_at:
            raise RuntimeError("Для товара уже отсканирован КИЗ.")
        if pack.packed_at:
            if not pack.requires_marking:
                return pack.id
            raise RuntimeError("Товар уже собран.")
        pack.requires_marking = False
        pack.packed_at = utcnow()
        pack.marking_state = PackState.PACKED
        pack.updated_at = utcnow()
        log_operation(
            session, "KIZ_SKIPPED", "pack", pack.pack_number, user_id,
            {"reason": "UNMARKED_PRODUCT"},
        )
        return pack.id


def all_packs_scanned(session_id):
    with session_scope() as session:
        active_filter = (
            LamodaPack.session_id == int(session_id),
            LamodaPack.marking_state != PackState.CANCELLED,
        )
        total = session.scalar(
            select(func.count()).select_from(LamodaPack).where(*active_filter)
        ) or 0
        scanned = session.scalar(select(func.count()).select_from(LamodaPack).where(
            *active_filter, LamodaPack.packed_at.is_not(None)
        )) or 0
        return total > 0 and total == scanned


def create_cargo_place(session_id, user_id):
    if not all_packs_scanned(session_id):
        raise RuntimeError("Не все отправления собраны.")
    if any(pack["marking_state"] != PackState.PACKED for pack in get_session_packs(session_id)):
        raise RuntimeError("В сборке есть отменённые или проблемные позиции.")
    with session_scope() as session:
        assembly = session.get(AssemblySession, int(session_id))
        if not assembly or assembly.status not in {"ASSEMBLING", "CARGO"}:
            raise RuntimeError("Активная сборка не найдена.")
        open_cargo = session.execute(
            select(CargoPlace).where(
                CargoPlace.session_id == int(session_id), CargoPlace.status == "OPEN"
            ).order_by(CargoPlace.id.desc()).with_for_update()
        ).scalars().first()
        if open_cargo:
            return open_cargo
        max_number = session.scalar(select(func.max(CargoPlace.local_number)).where(CargoPlace.session_id == int(session_id))) or 0
        row = CargoPlace(session_id=int(session_id), local_number=max_number + 1, created_by_user_id=str(user_id))
        session.add(row)
        assembly.status = "CARGO"
        assembly.updated_at = utcnow()
        session.flush()
        log_operation(session, "CARGO_CREATED", "cargo_place", row.id, user_id, {"local_number": row.local_number})
        return row


def add_pack_to_cargo(cargo_id, pack_number, user_id):
    with session_scope() as session:
        cargo = session.execute(select(CargoPlace).where(CargoPlace.id == int(cargo_id)).with_for_update()).scalar_one()
        if cargo.status != "OPEN":
            raise RuntimeError("Грузовое место закрыто.")
        pack = session.execute(select(LamodaPack).where(LamodaPack.pack_number == str(pack_number))).scalar_one_or_none()
        if not pack or pack.session_id != cargo.session_id or not pack.packed_at:
            raise RuntimeError("Упаковка не относится к этой сборке или ещё не собрана.")
        exists = session.execute(select(CargoPlacePack).where(CargoPlacePack.pack_number == pack.pack_number)).scalar_one_or_none()
        if exists:
            if exists.cargo_place_id == cargo.id:
                return False
            raise RuntimeError("Эта упаковка уже лежит в грузовом месте.")
        session.add(CargoPlacePack(cargo_place_id=cargo.id, pack_number=pack.pack_number, scanned_by_user_id=str(user_id)))
        log_operation(session, "PACK_ADDED_TO_CARGO", "cargo_place", cargo.id, user_id, {"pack_number": pack.pack_number})
        return True


def set_cargo_status(cargo_id, status, user_id):
    if status not in {"OPEN", "CLOSED"}:
        raise ValueError("Invalid cargo status")
    with session_scope() as session:
        cargo = session.execute(select(CargoPlace).where(CargoPlace.id == int(cargo_id)).with_for_update()).scalar_one()
        if cargo.pallet_id:
            raise RuntimeError("Грузовое место уже передано Lamoda и неизменяемо.")
        count = session.scalar(select(func.count()).select_from(CargoPlacePack).where(CargoPlacePack.cargo_place_id == cargo.id)) or 0
        if status == "CLOSED" and not count:
            raise RuntimeError("Нельзя закрыть пустое грузовое место.")
        cargo.status = status
        cargo.closed_at = utcnow() if status == "CLOSED" else None
        log_operation(session, f"CARGO_{status}", "cargo_place", cargo.id, user_id)


def cargo_manifest(session_id):
    with session_scope() as session:
        cargos = session.execute(select(CargoPlace).where(CargoPlace.session_id == int(session_id)).order_by(CargoPlace.local_number)).scalars().all()
        result = []
        for cargo in cargos:
            rows = session.execute(
                select(CargoPlacePack, LamodaPack, LamodaOrderItem)
                .join(LamodaPack, LamodaPack.pack_number == CargoPlacePack.pack_number)
                .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
                .where(CargoPlacePack.cargo_place_id == cargo.id)
                .order_by(CargoPlacePack.id)
            ).all()
            result.append({
                "id": cargo.id, "local_number": cargo.local_number,
                "status": cargo.status, "pallet_id": cargo.pallet_id,
                "packs": [pack_view(pack, item) for _, pack, item in rows],
            })
        return result


def validate_cargo_complete(session_id):
    manifest = cargo_manifest(session_id)
    if not manifest or any(item["status"] != "CLOSED" for item in manifest):
        raise RuntimeError("Закройте все грузовые места.")
    assigned = [pack["pack_number"] for cargo in manifest for pack in cargo["packs"]]
    expected = [pack["pack_number"] for pack in get_session_packs(session_id)]
    if any(pack["marking_state"] != PackState.PACKED for pack in get_session_packs(session_id)):
        raise RuntimeError("Отгрузка содержит отменённые или проблемные позиции.")
    if len(assigned) != len(set(assigned)) or set(assigned) != set(expected):
        raise RuntimeError("Каждая упаковка должна быть ровно в одном грузовом месте.")
    return manifest


def save_shipment(session_id, shipment_id, ship_at, payload, pallet_mapping):
    with session_scope() as session:
        existing = session.execute(select(Shipment).where(Shipment.session_id == int(session_id))).scalar_one_or_none()
        if existing:
            return existing
        row = Shipment(session_id=int(session_id), shipment_id=str(shipment_id), ship_at=ship_at, raw_json=json_dumps(payload))
        session.add(row)
        for cargo_id, pallet_id in pallet_mapping.items():
            cargo = session.get(CargoPlace, int(cargo_id))
            cargo.pallet_id = str(pallet_id)
            cargo.status = "SHIPPED"
        packs = session.execute(select(LamodaPack).where(
            LamodaPack.session_id == int(session_id),
            LamodaPack.marking_state != PackState.CANCELLED,
        )).scalars().all()
        for pack in packs:
            if pack.requires_marking:
                pack.marking_state = PackState.WAITING_WITHDRAWAL
            pack.updated_at = utcnow()
        assembly = session.get(AssemblySession, int(session_id))
        assembly.status = "SHIPPED"
        assembly.shipment_request_state = "CREATED"
        assembly.shipment_request_error = ""
        assembly.updated_at = utcnow()
        log_operation(session, "SHIPMENT_CREATED", "shipment", shipment_id, details={"session_id": session_id})
        return row


def find_pack(pack_number):
    with session_scope() as session:
        row = session.execute(
            select(LamodaPack, LamodaOrderItem, MarkingCode)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .outerjoin(MarkingCode, MarkingCode.id == LamodaPack.marking_code_id)
            .where(LamodaPack.pack_number == str(pack_number).strip())
        ).first()
        if not row:
            return None
        pack, item, code = row
        result = pack_view(pack, item)
        result.update({
            "raw_code": pack.scanned_raw_code or (code.raw_code if code else ""),
            "fingerprint": pack.kiz_fingerprint or (code.fingerprint if code else ""),
            "gtin": pack.scanned_gtin or (code.gtin if code else ""),
            "serial": pack.scanned_serial or (code.serial if code else ""),
            "code_state": code.state if code else "",
        })
        return result


def find_pack_by_item_id(item_id):
    with session_scope() as session:
        row = session.execute(
            select(LamodaPack, LamodaOrderItem, MarkingCode)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .outerjoin(MarkingCode, MarkingCode.id == LamodaPack.marking_code_id)
            .where(LamodaPack.item_id == str(item_id).strip())
        ).first()
        if not row:
            return None
        pack, item, code = row
        result = pack_view(pack, item)
        result.update({
            "raw_code": pack.scanned_raw_code or (code.raw_code if code else ""),
            "fingerprint": pack.kiz_fingerprint or (code.fingerprint if code else ""),
            "gtin": pack.scanned_gtin or (code.gtin if code else ""),
            "serial": pack.scanned_serial or (code.serial if code else ""),
            "code_state": code.state if code else "",
        })
        return result


def find_packs_by_order(order_id):
    with session_scope() as session:
        rows = session.execute(
            select(LamodaPack, LamodaOrderItem, MarkingCode)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .outerjoin(MarkingCode, MarkingCode.id == LamodaPack.marking_code_id)
            .where(LamodaPack.order_id == str(order_id).strip())
            .order_by(LamodaPack.id)
        ).all()
        result = []
        for pack, item, code in rows:
            view = pack_view(pack, item)
            view.update({
                "raw_code": pack.scanned_raw_code or (code.raw_code if code else ""),
                "fingerprint": pack.kiz_fingerprint or (code.fingerprint if code else ""),
                "gtin": pack.scanned_gtin or (code.gtin if code else ""),
                "serial": pack.scanned_serial or (code.serial if code else ""),
                "code_state": code.state if code else "",
            })
            result.append(view)
        return result


def update_pack_lamoda_status(pack_number, lamoda_status):
    with session_scope() as session:
        pack = session.execute(select(LamodaPack).where(LamodaPack.pack_number == str(pack_number)).with_for_update()).scalar_one_or_none()
        if not pack:
            return False
        status = str(lamoda_status or "")
        pack.lamoda_status = status
        upper = status.upper()
        code = session.get(MarkingCode, pack.marking_code_id) if pack.marking_code_id else None
        if any(token in upper for token in ("RETURN", "NOT_BOUGHT", "NOTBOUGHT")) and pack.marking_state == PackState.WITHDRAWN:
            pack.marking_state = PackState.RETURN_EXPECTED
            if code:
                code.state = CodeState.RETURN_PENDING
        elif any(token in upper for token in ("RETURN", "NOT_BOUGHT", "NOTBOUGHT", "CANCEL")) and pack.marking_state == PackState.WITHDRAWAL_EXPORTED:
            pack.marking_state = PackState.NEEDS_RECONCILIATION
            if code:
                code.state = CodeState.BLOCKED
        elif any(token in upper for token in ("CANCEL", "CANCELLED")) and pack.marking_state in {
            PackState.PACKED, PackState.WAITING_WITHDRAWAL,
        }:
            pack.marking_state = PackState.CANCELLED
            if code:
                code.state = CodeState.AVAILABLE
                code.current_pack_number = None
        pack.updated_at = utcnow()
        return True


def parse_api_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def update_pack_return_info(pack_number, return_item):
    with session_scope() as session:
        pack = session.execute(
            select(LamodaPack).where(LamodaPack.pack_number == str(pack_number)).with_for_update()
        ).scalar_one_or_none()
        if not pack:
            return False
        pack.return_item_id = str(return_item.get("id") or return_item.get("returnItemId") or "")
        pack.return_type = str(return_item.get("returnType") or "")
        pack.return_status = str(return_item.get("status") or "")
        pack.return_date = parse_api_datetime(return_item.get("returnDate"))
        pack.updated_at = utcnow()
        return True


def get_sync_value(key, default=""):
    with session_scope() as session:
        row = session.get(SyncState, str(key))
        return row.value if row else default


def set_sync_value(key, value):
    with session_scope() as session:
        row = session.get(SyncState, str(key))
        if not row:
            row = SyncState(key=str(key))
            session.add(row)
        row.value = str(value)
        row.updated_at = utcnow()


def claim_cancellation_notice(service_type, service_date, recipient, subject):
    with session_scope() as session:
        row = session.execute(
            select(CancellationNotice).where(
                CancellationNotice.service_type == str(service_type),
                CancellationNotice.service_date == service_date,
            ).with_for_update()
        ).scalar_one_or_none()
        if row and row.status in {"SENDING", "SENT"}:
            return False
        if not row:
            row = CancellationNotice(
                service_type=str(service_type),
                service_date=service_date,
            )
            session.add(row)
        row.status = "SENDING"
        row.recipient = str(recipient or "")
        row.subject = str(subject or "")
        row.message_id = ""
        row.error_text = ""
        row.updated_at = utcnow()
        return True


def finish_cancellation_notice(service_type, service_date, status, *, message_id="", error=""):
    with session_scope() as session:
        row = session.execute(
            select(CancellationNotice).where(
                CancellationNotice.service_type == str(service_type),
                CancellationNotice.service_date == service_date,
            ).with_for_update()
        ).scalar_one()
        row.status = str(status)
        row.message_id = str(message_id or "")
        row.error_text = str(error or "")[:4000]
        row.sent_at = utcnow() if status == "SENT" else None
        row.updated_at = utcnow()


def pending_counts():
    with session_scope() as session:
        states = [
            PackState.WAITING_WITHDRAWAL, PackState.WAITING_REINTRODUCTION,
            PackState.RETURN_EXPECTED, PackState.NEEDS_RECONCILIATION,
        ]
        rows = session.execute(
            select(LamodaPack.marking_state, func.count()).where(LamodaPack.marking_state.in_(states)).group_by(LamodaPack.marking_state)
        ).all()
        return {state: count for state, count in rows}


def create_marking_batch(batch_type, user_id, user_name=""):
    if batch_type == "WITHDRAWAL":
        source_state, export_state = PackState.WAITING_WITHDRAWAL, PackState.WITHDRAWAL_EXPORTED
    elif batch_type == "REINTRODUCTION":
        source_state, export_state = PackState.WAITING_REINTRODUCTION, PackState.REINTRODUCTION_EXPORTED
    else:
        raise ValueError("Unknown batch type")
    with session_scope() as session:
        packs = session.execute(
            select(LamodaPack)
            .where(
                LamodaPack.marking_state == source_state,
                LamodaPack.requires_marking.is_(True),
            )
            .order_by(LamodaPack.id)
            .with_for_update()
        ).scalars().all()
        if not packs:
            raise RuntimeError("Нет кодов для этой операции.")
        batch = MarkingBatch(
            batch_type=batch_type, created_by_user_id=str(user_id),
            created_by_name=str(user_name or ""),
        )
        session.add(batch)
        session.flush()
        for pack in packs:
            session.add(MarkingBatchItem(batch_id=batch.id, pack_number=pack.pack_number))
            pack.marking_state = export_state
            pack.updated_at = utcnow()
        log_operation(session, "MARKING_BATCH_EXPORTED", "marking_batch", batch.id, user_id, {
            "type": batch_type, "count": len(packs),
        })
        return batch.id


def marking_batch_rows(batch_id):
    with session_scope() as session:
        rows = session.execute(
            select(MarkingBatch, MarkingBatchItem, LamodaPack, LamodaOrderItem, MarkingCode, Shipment, ReturnReceipt)
            .join(MarkingBatchItem, MarkingBatchItem.batch_id == MarkingBatch.id)
            .join(LamodaPack, LamodaPack.pack_number == MarkingBatchItem.pack_number)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .join(MarkingCode, MarkingCode.id == LamodaPack.marking_code_id)
            .outerjoin(Shipment, Shipment.session_id == LamodaPack.session_id)
            .outerjoin(ReturnReceipt, ReturnReceipt.pack_number == LamodaPack.pack_number)
            .where(MarkingBatch.id == int(batch_id))
            .order_by(MarkingBatchItem.id)
        ).all()
        result = []
        for batch, batch_item, pack, item, code, shipment, receipt in rows:
            raw_code = pack.scanned_raw_code or code.raw_code
            result.append({
                "batch_id": batch.id, "batch_type": batch.batch_type, "batch_status": batch.status,
                "result": batch_item.result, "shipment_id": shipment.shipment_id if shipment else "",
                "date": shipment.ship_at if shipment else pack.created_at,
                "order_id": pack.order_id, "item_id": pack.item_id, "pack_number": pack.pack_number,
                "product_name": item.moysklad_name or item.product_name,
                "size": item.size, "sku": item.sku,
                "gtin": pack.scanned_gtin or code.gtin,
                "raw_code": raw_code,
                "short_code": short_marking_code(code, raw_code),
                "sale_price": sold_price_from_item_json(item.raw_json),
                "withdrawn_at": pack.withdrawn_at, "return_received_at": pack.return_received_at,
                "condition": receipt.condition if receipt else "", "defect_reason": receipt.defect_reason if receipt else "",
                "return_item_id": receipt.return_item_id if receipt else pack.return_item_id,
                "return_status": receipt.return_status if receipt else (pack.return_status or pack.lamoda_status),
            })
        return result


def confirm_marking_batch(batch_id, manager_user_id, failed_pack_numbers=None, manager_name=""):
    failed = {str(value).strip() for value in failed_pack_numbers or [] if str(value).strip()}
    with session_scope() as session:
        batch = session.execute(select(MarkingBatch).where(MarkingBatch.id == int(batch_id)).with_for_update()).scalar_one()
        if batch.status != "EXPORTED":
            if batch.status in {"CONFIRMED", "PARTIAL"}:
                return batch.status
            raise RuntimeError("Эта партия уже обработана.")
        items = session.execute(select(MarkingBatchItem).where(MarkingBatchItem.batch_id == batch.id)).scalars().all()
        known = {item.pack_number for item in items}
        unknown = failed - known
        if unknown:
            raise RuntimeError(f"В партии нет packNumber: {', '.join(sorted(unknown))}")
        now = utcnow()
        for item in items:
            pack = session.execute(select(LamodaPack).where(LamodaPack.pack_number == item.pack_number).with_for_update()).scalar_one()
            code = session.get(MarkingCode, pack.marking_code_id)
            if item.pack_number in failed:
                item.result = "ERROR"
                item.error_text = "Отмечено руководителем"
                item.updated_at = now
                pack.marking_state = PackState.NEEDS_RECONCILIATION
                if code:
                    code.state = CodeState.BLOCKED
            elif batch.batch_type == "WITHDRAWAL":
                item.result = "CONFIRMED"
                item.updated_at = now
                pack.marking_state = PackState.WITHDRAWN
                pack.withdrawn_at = now
                if code:
                    code.state = CodeState.OUT_OF_CIRCULATION
            else:
                item.result = "CONFIRMED"
                item.updated_at = now
                pack.marking_state = PackState.REINTRODUCED
                pack.reintroduced_at = now
                if code:
                    code.state = CodeState.AVAILABLE
                    code.current_pack_number = None
            pack.updated_at = now
            if code:
                code.updated_at = now
        batch.status = "PARTIAL" if failed else "CONFIRMED"
        batch.confirmed_by_user_id = str(manager_user_id)
        batch.confirmed_by_name = str(manager_name or "")
        batch.confirmed_at = now
        batch.updated_at = now
        log_operation(session, "MARKING_BATCH_CONFIRMED", "marking_batch", batch.id, manager_user_id, {
            "failed": sorted(failed),
        })
        return batch.status


def cancel_marking_batch(batch_id, user_id):
    with session_scope() as session:
        batch = session.execute(select(MarkingBatch).where(MarkingBatch.id == int(batch_id)).with_for_update()).scalar_one()
        if batch.status != "EXPORTED":
            if batch.status == "CANCELLED":
                return
            raise RuntimeError("Отменить можно только неподтверждённую выгрузку.")
        target = PackState.WAITING_WITHDRAWAL if batch.batch_type == "WITHDRAWAL" else PackState.WAITING_REINTRODUCTION
        numbers = session.execute(select(MarkingBatchItem.pack_number).where(MarkingBatchItem.batch_id == batch.id)).scalars().all()
        for pack in session.execute(select(LamodaPack).where(LamodaPack.pack_number.in_(numbers))).scalars():
            pack.marking_state = target
            pack.updated_at = utcnow()
        batch.status = "CANCELLED"
        batch.updated_at = utcnow()
        log_operation(session, "MARKING_BATCH_CANCELLED", "marking_batch", batch.id, user_id)


def record_return_receipt(
    *, pack_number, order_id, item_id, return_item_id, condition, defect_reason,
    label_photo_file_id, scanned_kiz_fingerprint, defect_photo_file_ids,
    user_id, user_name, problematic=False, problem_reason="", manager_override=False,
):
    condition = str(condition or "").upper()
    if condition not in {"NORMAL", "DEFECT"}:
        raise ValueError("Состояние возврата должно быть NORMAL или DEFECT.")
    if condition == "DEFECT" and not str(defect_reason or "").strip():
        raise ValueError("Для брака обязательно описание причины.")
    if condition == "DEFECT" and not (defect_photo_file_ids or []):
        raise ValueError("Для брака обязательна хотя бы одна фотография.")
    with session_scope() as session:
        pack = session.execute(
            select(LamodaPack).where(LamodaPack.pack_number == str(pack_number)).with_for_update()
        ).scalar_one_or_none() if pack_number else None
        duplicate_filter = None
        if pack_number:
            duplicate_filter = ReturnReceipt.pack_number == str(pack_number)
        elif item_id:
            duplicate_filter = ReturnReceipt.item_id == str(item_id)
        duplicate = session.execute(
            select(ReturnReceipt).where(duplicate_filter).order_by(ReturnReceipt.id.desc())
        ).scalars().first() if duplicate_filter is not None else None
        mismatch = bool(pack and pack.kiz_fingerprint and scanned_kiz_fingerprint != pack.kiz_fingerprint)
        actual_problem_reason = str(problem_reason or "")
        if mismatch and "КИЗ" not in actual_problem_reason:
            actual_problem_reason = (actual_problem_reason + " КИЗ не совпадает с исходным.").strip()
        actual_problematic = bool(problematic or not pack or mismatch)
        if duplicate and not manager_override:
            raise RuntimeError(
                f"Возврат уже принят {duplicate.created_at:%d.%m.%Y %H:%M} "
                f"сотрудником {duplicate.received_by_name or duplicate.received_by_user_id}."
            )
        if duplicate and manager_override:
            duplicate.condition = condition
            duplicate.defect_reason = str(defect_reason or "")
            duplicate.label_photo_file_id = str(label_photo_file_id)
            duplicate.scanned_kiz_fingerprint = str(scanned_kiz_fingerprint or "")
            duplicate.problematic = actual_problematic
            duplicate.problem_reason = actual_problem_reason
            duplicate.received_by_user_id = str(user_id)
            duplicate.received_by_name = str(user_name or "")
            duplicate.updated_at = utcnow()
            for file_id in defect_photo_file_ids or []:
                session.add(ReturnPhoto(receipt_id=duplicate.id, file_id=str(file_id)))
            log_operation(session, "RETURN_RECEIPT_CORRECTED", "return_receipt", duplicate.id, user_id)
            return duplicate.id
        receipt = ReturnReceipt(
            pack_number=str(pack_number) if pack_number else None,
            order_id=str(order_id or (pack.order_id if pack else "")),
            item_id=str(item_id or (pack.item_id if pack else "")),
            return_item_id=str(return_item_id or ""), condition=str(condition),
            return_type=pack.return_type if pack else "",
            return_status=pack.return_status if pack else "",
            return_date=pack.return_date if pack else None,
            defect_reason=str(defect_reason or ""), label_photo_file_id=str(label_photo_file_id),
            scanned_kiz_fingerprint=str(scanned_kiz_fingerprint or ""),
            problematic=actual_problematic, problem_reason=actual_problem_reason,
            received_by_user_id=str(user_id), received_by_name=str(user_name or ""),
        )
        session.add(receipt)
        session.flush()
        for file_id in defect_photo_file_ids or []:
            session.add(ReturnPhoto(receipt_id=receipt.id, file_id=str(file_id)))
        if pack:
            code = session.get(MarkingCode, pack.marking_code_id) if pack.marking_code_id else None
            if actual_problematic:
                pack.marking_state = PackState.NEEDS_RECONCILIATION
                if code:
                    code.state = CodeState.BLOCKED
            elif pack.marking_state in {PackState.WITHDRAWN, PackState.RETURN_EXPECTED}:
                pack.marking_state = PackState.WAITING_REINTRODUCTION
                pack.return_received_at = utcnow()
                if code:
                    code.state = CodeState.RETURN_PENDING
            elif pack.marking_state in {PackState.WITHDRAWAL_EXPORTED, PackState.NEEDS_RECONCILIATION}:
                pack.marking_state = PackState.NEEDS_RECONCILIATION
                receipt.problematic = True
                receipt.problem_reason = receipt.problem_reason or "Вывод из оборота не был однозначно подтверждён."
            else:
                pack.marking_state = PackState.REINTRODUCED
                pack.return_received_at = utcnow()
                if code:
                    code.state = CodeState.AVAILABLE
                    code.current_pack_number = None
            pack.updated_at = utcnow()
        log_operation(session, "RETURN_RECEIVED", "return_receipt", receipt.id, user_id, {
            "pack_number": pack_number, "problematic": receipt.problematic,
        })
        return receipt.id


def list_return_receipts(limit=20, problematic_only=False):
    with session_scope() as session:
        statement = select(ReturnReceipt).order_by(ReturnReceipt.id.desc()).limit(int(limit))
        if problematic_only:
            statement = statement.where(ReturnReceipt.problematic.is_(True))
        rows = session.execute(statement).scalars().all()
        return [{
            "id": row.id, "pack_number": row.pack_number or "", "order_id": row.order_id,
            "condition": row.condition, "problematic": row.problematic,
            "problem_reason": row.problem_reason, "employee": row.received_by_name,
            "created_at": row.created_at,
        } for row in rows]


def list_expected_returns(limit=50):
    with session_scope() as session:
        rows = session.execute(
            select(LamodaPack, LamodaOrderItem)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .where(LamodaPack.marking_state == PackState.RETURN_EXPECTED)
            .order_by(LamodaPack.updated_at)
            .limit(int(limit))
        ).all()
        return [pack_view(pack, item) for pack, item in rows]


def list_problem_packs(limit=50):
    with session_scope() as session:
        rows = session.execute(
            select(LamodaPack, LamodaOrderItem)
            .join(LamodaOrderItem, LamodaOrderItem.item_id == LamodaPack.item_id)
            .where(LamodaPack.marking_state == PackState.NEEDS_RECONCILIATION)
            .order_by(LamodaPack.updated_at)
            .limit(int(limit))
        ).all()
        return [pack_view(pack, item) for pack, item in rows]


def marking_history(limit=30):
    with session_scope() as session:
        batches = session.execute(select(MarkingBatch).order_by(MarkingBatch.id.desc()).limit(int(limit))).scalars().all()
        return [{
            "id": row.id, "type": row.batch_type, "status": row.status,
            "created_at": row.created_at, "confirmed_at": row.confirmed_at,
            "created_by": row.created_by_user_id, "confirmed_by": row.confirmed_by_user_id,
        } for row in batches]
