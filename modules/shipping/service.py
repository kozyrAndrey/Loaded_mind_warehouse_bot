import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from modules.marking.duplicate_chz import GROUP_SEPARATOR, normalize_chz_text
from modules.moysklad.client import MoySkladError


MONEY_QUANTUM = Decimal("0.01")
PAGE_LIMIT = 100
SHIPPING_ORDER_ATTRIBUTE_NAME = "[CloudPayments] Ссылка на оплату"
SHIPPING_ORDER_ATTRIBUTE_VALUE = "уедет"
CDEK_TRACK_RE = re.compile(r"^\[CDK\]\s*(\d+)$", re.IGNORECASE)


class ShippingValidationError(ValueError):
    pass


class ShippingServiceError(RuntimeError):
    pass


def _response_rows(payload):
    return payload.get("rows", []) if isinstance(payload, dict) else []


def _all_pages(fetch_page, params=None):
    base_params = dict(params or {})
    offset = 0
    rows = []
    while True:
        page_params = {**base_params, "limit": PAGE_LIMIT, "offset": offset}
        payload = fetch_page(page_params)
        page = _response_rows(payload)
        rows.extend(page)
        offset += len(page)
        size = int((payload.get("meta") or {}).get("size") or 0) if isinstance(payload, dict) else 0
        if not page or len(page) < PAGE_LIMIT or (size and offset >= size):
            break
    return rows


def _reference_name(client, value, cache=None):
    if not isinstance(value, dict):
        return ""
    name = str(value.get("name") or "").strip()
    if name:
        return name
    href = str((value.get("meta") or {}).get("href") or "").strip()
    if not href:
        return ""
    cache = cache if cache is not None else {}
    if href not in cache:
        cache[href] = client.get_href(href)
    return str((cache[href] or {}).get("name") or "").strip()


def is_cancelled_order(client, order, cache=None):
    state_name = _reference_name(client, order.get("state"), cache=cache)
    normalized = state_name.casefold().replace("ё", "е")
    return "отмен" in normalized


def parse_order_lookup_query(query):
    raw_query = str(query or "").strip()
    cdek_match = CDEK_TRACK_RE.fullmatch(raw_query)
    if cdek_match:
        return {
            "value": cdek_match.group(1),
            "kind": "cdek_track",
        }
    if raw_query.isdigit():
        return {
            "value": raw_query,
            "kind": "order_number",
        }
    raise ShippingValidationError(
        "Введите цифры из номера заказа или отсканируйте накладную CDEK."
    )


def search_customer_orders(client, query):
    lookup = parse_order_lookup_query(query)
    search_value = lookup["value"]

    def fetch(params):
        try:
            return client.list_entities("customerorder", params=params)
        except MoySkladError:
            fallback = {key: value for key, value in params.items() if key != "expand"}
            return client.list_entities("customerorder", params=fallback)

    try:
        rows = _all_pages(
            fetch,
            {"search": search_value, "expand": "state"},
        )
    except MoySkladError as error:
        raise ShippingServiceError(str(error)) from error

    if lookup["kind"] == "cdek_track":
        # Context search in MoySklad checks the order's string fields. A CDEK
        # tracking number does not have to occur in the order name, so all
        # results returned for an explicitly prefixed CDEK scan are relevant.
        matching = rows
    else:
        matching = [
            row for row in rows
            if search_value in str(row.get("name") or "")
        ]
    state_cache = {}
    active = []
    cancelled = []
    for order in matching:
        try:
            target = cancelled if is_cancelled_order(client, order, cache=state_cache) else active
        except MoySkladError as error:
            raise ShippingServiceError(str(error)) from error
        target.append(order)

    active.sort(key=lambda row: str(row.get("name") or "").casefold())
    return {
        "orders": active,
        "cancelled_count": len(cancelled),
        "query": search_value,
        "query_kind": lookup["kind"],
    }


def set_order_shipping_flag(client, order_id):
    order_id = str(order_id or "").strip()
    if not order_id:
        raise ShippingServiceError("У заказа отсутствует идентификатор «МойСклад».")
    try:
        payload = client.get("entity/customerorder/metadata/attributes")
    except MoySkladError as error:
        raise ShippingServiceError(str(error)) from error

    attribute = next(
        (
            row for row in _response_rows(payload)
            if str(row.get("name") or "").strip() == SHIPPING_ORDER_ATTRIBUTE_NAME
        ),
        None,
    )
    if not attribute:
        raise ShippingServiceError(
            f"В «МойСклад» не найдено поле «{SHIPPING_ORDER_ATTRIBUTE_NAME}»."
        )
    if str(attribute.get("type") or "") != "link":
        raise ShippingServiceError(
            f"Поле «{SHIPPING_ORDER_ATTRIBUTE_NAME}» должно иметь тип link."
        )

    attribute_meta = dict(attribute.get("meta") or {})
    if not attribute_meta.get("href"):
        attribute_id = str(attribute.get("id") or "").strip()
        if not attribute_id:
            raise ShippingServiceError("У дополнительного поля отсутствует идентификатор.")
        attribute_meta = {
            "href": f"{client.base_url}/entity/customerorder/metadata/attributes/{attribute_id}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        }

    update_payload = {
        "attributes": [
            {
                "meta": attribute_meta,
                "value": SHIPPING_ORDER_ATTRIBUTE_VALUE,
            }
        ]
    }
    try:
        return client.update_entity("customerorder", order_id, update_payload)
    except MoySkladError as error:
        raise ShippingServiceError(str(error)) from error


def _expanded_reference(client, value, cache):
    if not isinstance(value, dict):
        return {}
    href = str((value.get("meta") or {}).get("href") or "").strip()
    if value.get("barcodes") is not None and (value.get("name") or not href):
        return value
    if not href:
        return value
    if href not in cache:
        cache[href] = client.get_href(href)
    merged = dict(value)
    merged.update(cache[href] or {})
    return merged


def _normalize_catalog_gtin(value):
    code = str(value or "").strip()
    if len(code) == 13 and code.isdigit():
        code = "0" + code
    if len(code) != 14 or not code.isdigit() or not gtin_check_digit_is_valid(code):
        return ""
    return code


def gtin_from_assortment(assortment):
    """Return only the GTIN assigned to the exact order assortment.

    For a variant, a barcode inherited from the parent product must not make
    that variant marked: marking is configured independently per modification.
    """
    barcodes = (assortment or {}).get("barcodes") or []
    for barcode_type in ("gtin", "ean13"):
        for barcode in barcodes:
            if not isinstance(barcode, dict):
                continue
            gtin = _normalize_catalog_gtin(barcode.get(barcode_type))
            if gtin:
                return gtin
    return ""


def position_unit_price(position):
    try:
        price = Decimal(str(position["price"])) / Decimal("100")
        discount = Decimal(str(position.get("discount") or 0))
    except (KeyError, InvalidOperation, TypeError, ValueError) as error:
        raise ShippingServiceError("Не удалось определить цену позиции заказа.") from error
    if price < 0 or discount < 0 or discount > 100:
        raise ShippingServiceError("В позиции заказа указана некорректная цена или скидка.")
    return (price * (Decimal("1") - discount / Decimal("100"))).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _integer_quantity(position):
    try:
        quantity = Decimal(str(position.get("quantity") or 0))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ShippingServiceError("В позиции заказа указано некорректное количество.") from error
    integral = quantity.to_integral_value()
    if quantity <= 0 or quantity != integral:
        raise ShippingServiceError(
            "Модуль отгрузки поддерживает только положительное целое количество товара."
        )
    return int(integral)


def _entity_id(value):
    if not isinstance(value, dict):
        return ""
    direct = str(value.get("id") or "").strip()
    if direct:
        return direct
    href = str((value.get("meta") or {}).get("href") or "").rstrip("/")
    return href.rsplit("/", 1)[-1] if href else ""


def get_order_positions(client, order_id):
    def fetch(params):
        try:
            return client.get_positions("customerorder", order_id, params=params)
        except MoySkladError:
            fallback = {key: value for key, value in params.items() if key != "expand"}
            return client.get_positions("customerorder", order_id, params=fallback)

    try:
        return _all_pages(fetch, {"expand": "assortment,assortment.product"})
    except MoySkladError as error:
        raise ShippingServiceError(str(error)) from error


def build_order_units(client, order):
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        raise ShippingServiceError("У заказа отсутствует идентификатор «МойСклад».")
    positions = get_order_positions(client, order_id)
    if not positions:
        raise ShippingServiceError("В выбранном заказе нет товарных позиций.")

    reference_cache = {}
    units = []
    unmarked_count = 0
    for position in positions:
        assortment = _expanded_reference(client, position.get("assortment") or {}, reference_cache)
        parent = assortment.get("product") or {}
        gtin = gtin_from_assortment(assortment)
        quantity = _integer_quantity(position)
        price = position_unit_price(position)
        product_name = str(assortment.get("name") or parent.get("name") or "Без названия").strip()
        if not gtin:
            unmarked_count += quantity
            continue

        for unit_number in range(1, quantity + 1):
            units.append(
                {
                    "position_id": str(position.get("id") or ""),
                    "assortment_id": _entity_id(assortment),
                    "product_name": product_name,
                    "gtin": gtin,
                    "price": format(price, ".2f"),
                    "unit_number": unit_number,
                    "position_quantity": quantity,
                }
            )
    return {
        "units": units,
        "unmarked_count": unmarked_count,
        "position_count": len(positions),
    }


def gtin_check_digit_is_valid(gtin):
    value = str(gtin or "")
    if len(value) != 14 or not value.isdigit():
        return False
    total = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(value[:-1]))
    )
    expected = (10 - total % 10) % 10
    return expected == int(value[-1])


def _parenthesized_values(code):
    import re

    matches = re.findall(r"\((01|21|91|92)\)([^()]*)", code)
    return {ai: value.strip(GROUP_SEPARATOR) for ai, value in matches}


def _plain_values(code):
    if not code.startswith("01") or len(code) < 31:
        raise ShippingValidationError("Код должен начинаться с AI 01 и содержать AI 21.")
    gtin = code[2:16]
    if code[16:18] != "21":
        raise ShippingValidationError("После GTIN не найден AI 21 серийного номера.")
    serial = code[18:31]
    tail = code[31:].strip(GROUP_SEPARATOR)
    if not tail.startswith("91"):
        raise ShippingValidationError("В полном коде не найден криптохвост AI 91.")
    crypto = tail[2:]
    if GROUP_SEPARATOR in crypto:
        value91, rest = crypto.split(GROUP_SEPARATOR, 1)
        rest = rest.lstrip(GROUP_SEPARATOR)
        if not rest.startswith("92"):
            raise ShippingValidationError("После AI 91 не найден AI 92.")
        value92 = rest[2:].strip(GROUP_SEPARATOR)
    else:
        marker = crypto.find("92", 1)
        if marker < 0:
            raise ShippingValidationError("После AI 91 не найден AI 92.")
        value91 = crypto[:marker]
        value92 = crypto[marker + 2:]
    return {"01": gtin, "21": serial, "91": value91, "92": value92}


def parse_shipping_marking_code(raw_code, expected_gtin=None):
    code = normalize_chz_text(raw_code).strip()
    if not code:
        raise ShippingValidationError("Код маркировки пустой.")
    values = _parenthesized_values(code) if "(01)" in code else _plain_values(code)

    gtin = str(values.get("01") or "")
    serial = str(values.get("21") or "").strip(GROUP_SEPARATOR)
    value91 = str(values.get("91") or "").strip(GROUP_SEPARATOR)
    value92 = str(values.get("92") or "").strip(GROUP_SEPARATOR)
    if len(gtin) != 14 or not gtin.isdigit():
        raise ShippingValidationError("AI 01 должен содержать 14-значный GTIN.")
    if not gtin_check_digit_is_valid(gtin):
        raise ShippingValidationError("У GTIN неверная контрольная цифра.")
    if len(serial) != 13:
        raise ShippingValidationError("AI 21 должен содержать серийный номер из 13 символов.")
    if not value91:
        raise ShippingValidationError("AI 91 не содержит значения.")
    if not value92:
        raise ShippingValidationError("AI 92 не содержит значения.")

    normalized_expected = _normalize_catalog_gtin(expected_gtin) if expected_gtin else ""
    if expected_gtin and not normalized_expected:
        raise ShippingValidationError("В карточке товара указан некорректный GTIN.")
    if normalized_expected and gtin != normalized_expected:
        raise ShippingValidationError(
            f"GTIN кода {gtin} не совпадает с GTIN товара {normalized_expected}."
        )

    short_code = f"01{gtin}21{serial}"
    if len(short_code) != 31:
        raise ShippingValidationError("Короткий код должен содержать ровно 31 символ.")
    return {"short_code": short_code, "gtin": gtin, "serial": serial}
