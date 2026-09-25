import csv
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from openpyxl import Workbook

from config import (
    MOYSKLAD_API_BASE_URL,
    MOYSKLAD_CA_FILE,
    MOYSKLAD_SALE_PRICE_TYPE,
    MOYSKLAD_SSL_VERIFY,
    MOYSKLAD_TOKEN,
)
from modules.marking.storage import normalize_gtin
from modules.moysklad.client import MoySkladClient, MoySkladError


NET_PRICE_QUANTUM = Decimal("0.00000001")
VAT_MULTIPLIER = Decimal("1.07")
ARTICLE_CHARACTERISTIC_NAMES = {"артикул", "article"}


class TrendExportValidationError(RuntimeError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("В исходных данных найдены ошибки.")


def parse_bool(value, default=True):
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "да"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "нет"}:
        return False
    return default


def build_moysklad_client():
    return MoySkladClient(
        token=MOYSKLAD_TOKEN,
        base_url=MOYSKLAD_API_BASE_URL,
        ssl_verify=parse_bool(MOYSKLAD_SSL_VERIFY),
        ca_file=MOYSKLAD_CA_FILE or None,
    )


def response_rows(payload):
    return payload.get("rows", []) if isinstance(payload, dict) else []


def extract_cis_codes(value):
    codes = []

    def walk(node):
        if isinstance(node, dict):
            cis = node.get("cis")
            if cis:
                codes.append(str(cis))

            for child in node.get("trackingCodes") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return codes


def find_retireorder(client, document_name):
    documents_by_id = {}
    attempts = [
        {"filter": f"name={document_name}", "limit": 10},
        {"search": document_name, "limit": 10},
    ]

    for params in attempts:
        payload = client.list_entities("retireorder", params=params)
        for document in response_rows(payload):
            document_id = str(document.get("id") or "").strip()
            if document_id:
                documents_by_id[document_id] = document

    exact_matches = [
        document
        for document in documents_by_id.values()
        if str(document.get("name") or "").strip() == document_name
    ]
    matches = exact_matches or list(documents_by_id.values())

    if not matches:
        raise MoySkladError(f"Документ вывода из оборота «{document_name}» не найден.")
    if len(matches) > 1:
        names = ", ".join(str(document.get("name") or document.get("id")) for document in matches[:5])
        raise MoySkladError(f"Найдено несколько документов: {names}. Уточните название документа.")

    return matches[0]


def get_assortment_name(position):
    assortment = position.get("assortment") or {}
    name = str(assortment.get("name") or "").strip()
    if name:
        return name

    meta = assortment.get("meta") or {}
    href = str(meta.get("href") or "").strip()
    return href.rsplit("/", 1)[-1] if href else "Без названия"


def get_all_position_tracking_codes(client, entity_type, entity_id, position_id):
    codes = []
    limit = 100
    offset = 0

    while True:
        payload = client.get_position_tracking_codes(
            entity_type,
            entity_id,
            position_id,
            params={"codetype": "gs1", "limit": limit, "offset": offset},
        )
        rows = response_rows(payload)
        codes.extend(extract_cis_codes(rows))

        if not rows:
            break
        meta = payload.get("meta") or {}
        size = int(meta.get("size") or 0)
        offset += len(rows)
        if (size and offset >= size) or len(rows) < limit:
            break

    return codes


def expand_parent_product(client, assortment, cache):
    product = assortment.get("product") or {}
    if not isinstance(product, dict):
        return {}
    if product.get("article") or product.get("salePrices") or product.get("barcodes"):
        return product

    href = str((product.get("meta") or {}).get("href") or "").strip()
    if not href:
        return product
    if href not in cache:
        cache[href] = client.get_href(href)
    return cache[href]


def extract_article(assortment, parent_product):
    characteristic_article = extract_characteristic_value(
        assortment.get("characteristics"),
        ARTICLE_CHARACTERISTIC_NAMES,
    )
    return str(
        characteristic_article
        or assortment.get("article")
        or parent_product.get("article")
        or ""
    ).strip()


def extract_characteristic_value(characteristics, wanted_names):
    for characteristic in characteristics or []:
        if not isinstance(characteristic, dict):
            continue
        name = str(characteristic.get("name") or "").strip().casefold()
        if name not in wanted_names:
            continue
        value = characteristic.get("value")
        if isinstance(value, dict):
            value = value.get("name") or value.get("value")
        if str(value or "").strip():
            return value
    return ""


def extract_gtin(assortment, parent_product):
    return extract_gtin_from_barcodes(assortment.get("barcodes")) or extract_gtin_from_barcodes(
        parent_product.get("barcodes")
    )


def extract_gtin_from_barcodes(barcodes):
    items = [item for item in barcodes or [] if isinstance(item, dict)]
    for barcode_type in ("gtin", "ean13", "ean8", "upc"):
        for item in items:
            value = str(item.get(barcode_type) or "").strip()
            if value:
                return value
    return ""


def extract_sale_price(assortment, parent_product, price_type_name):
    price = sale_price_by_type(assortment.get("salePrices"), price_type_name)
    if price is not None:
        return price
    return sale_price_by_type(parent_product.get("salePrices"), price_type_name)


def sale_price_by_type(sale_prices, price_type_name):
    expected = str(price_type_name or "").strip().casefold()
    for sale_price in sale_prices or []:
        if not isinstance(sale_price, dict):
            continue
        actual = str((sale_price.get("priceType") or {}).get("name") or "").strip().casefold()
        if actual != expected:
            continue
        value = sale_price.get("value")
        try:
            return Decimal(str(value)) / Decimal("100")
        except (InvalidOperation, TypeError, ValueError):
            return value
    return None


def get_all_positions(client, entity_type, entity_id):
    positions = []
    limit = 1000
    offset = 0
    while True:
        payload = client.get_positions(
            entity_type,
            entity_id,
            params={"expand": "assortment", "limit": limit, "offset": offset},
        )
        rows = response_rows(payload)
        if not rows:
            break
        positions.extend(rows)
        meta = payload.get("meta") or {}
        size = int(meta.get("size") or 0)
        offset += len(rows)
        if (size and offset >= size) or len(rows) < limit:
            break
    return positions


def get_retireorder_export_rows(client, document_name, sale_price_type=MOYSKLAD_SALE_PRICE_TYPE):
    document = find_retireorder(client, document_name)
    document_id = document["id"]

    result = []
    product_cache = {}
    for position in get_all_positions(client, "retireorder", document_id):
        position_id = str(position.get("id") or "").strip()
        if not position_id:
            continue

        assortment = position.get("assortment") or {}
        parent_product = expand_parent_product(client, assortment, product_cache)
        codes = get_all_position_tracking_codes(client, "retireorder", document_id, position_id)
        result.append(
            {
                "name": get_assortment_name(position),
                "article": extract_article(assortment, parent_product),
                "gtin": extract_gtin(assortment, parent_product),
                "sale_price": extract_sale_price(assortment, parent_product, sale_price_type),
                "codes": codes,
                "assortment": assortment,
                "product": parent_product,
            }
        )

    return document, result


def normalize_assortment_lookup(value):
    normalized = str(value or "").strip().casefold().replace("ё", "е")
    normalized = re.sub(r"[^0-9a-zа-я]+", " ", normalized)
    return " ".join(normalized.split())


def unmarked_model_name(product):
    name = str(product.get("honest_sign_name") or "").strip()
    quoted_parts = re.findall(r'"([^"]+)"', name)
    return quoted_parts[-1].strip() if quoted_parts else name


def assortment_matches_unmarked_product(assortment, product):
    model_name = normalize_assortment_lookup(unmarked_model_name(product))
    assortment_name = normalize_assortment_lookup(assortment.get("name"))
    return bool(
        model_name
        and assortment_name
        and (
            assortment_name == model_name
            or assortment_name == f"{model_name} bag"
            or model_name in assortment_name
        )
    )


def unmarked_assortment_score(assortment):
    entity_type = str((assortment.get("meta") or {}).get("type") or "").casefold()
    has_ean13 = any(
        str((barcode or {}).get("ean13") or "").strip()
        for barcode in assortment.get("barcodes") or []
        if isinstance(barcode, dict)
    )
    return (
        1 if entity_type == "variant" else 0,
        1 if has_ean13 else 0,
        1 if str(assortment.get("article") or "").strip() else 0,
    )


def get_all_assortment_rows(client):
    rows = []
    limit = 1000
    offset = 0
    while True:
        payload = client.list_entities(
            "assortment",
            params={
                "limit": limit,
                "offset": offset,
                "expand": "product",
            },
        )
        page = response_rows(payload)
        if not page:
            break
        rows.extend(page)
        size = int((payload.get("meta") or {}).get("size") or 0)
        offset += len(page)
        if (size and offset >= size) or len(page) < limit:
            break
    return rows


def get_unmarked_moysklad_rows(
    client,
    unmarked_products,
    sale_price_type=MOYSKLAD_SALE_PRICE_TYPE,
    existing_rows=None,
):
    result = []
    product_cache = {}
    products_to_find = [
        product
        for product in unmarked_products or []
        if not any(
            assortment_matches_unmarked_product(
                {
                    "name": (
                        (row.get("assortment") or {}).get("name")
                        or row.get("name")
                    )
                },
                product,
            )
            for row in existing_rows or []
        )
    ]
    if not products_to_find:
        return result

    try:
        assortment_rows = get_all_assortment_rows(client)
    except MoySkladError:
        return result

    for product in products_to_find:
        model_name = unmarked_model_name(product)
        candidates = [
            assortment
            for assortment in assortment_rows
            if assortment_matches_unmarked_product(assortment, product)
        ]
        if not candidates:
            continue

        assortment = max(candidates, key=unmarked_assortment_score)
        parent_product = expand_parent_product(client, assortment, product_cache)
        result.append(
            {
                "name": str(assortment.get("name") or model_name).strip(),
                "article": extract_article(assortment, parent_product),
                "gtin": extract_gtin(assortment, parent_product),
                "sale_price": extract_sale_price(
                    assortment,
                    parent_product,
                    sale_price_type,
                ),
                "codes": [],
                "assortment": assortment,
                "product": parent_product,
            }
        )
    return result


def calculate_net_price(value):
    try:
        gross_price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Цена имеет некорректный формат.") from error
    if not gross_price.is_finite() or gross_price < 0:
        raise ValueError("Цена имеет некорректный формат.")
    return (gross_price / VAT_MULTIPLIER).quantize(NET_PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def build_trend_island_upd_rows(rows, catalog_names):
    normalized_catalog = {}
    for gtin, name in (catalog_names or {}).items():
        try:
            normalized_catalog[normalize_gtin(gtin)] = str(name or "").strip()
        except ValueError:
            continue

    errors = []
    prepared_positions = []
    seen_codes = {}

    for position_index, item in enumerate(rows, start=1):
        source_name = str(item.get("name") or f"Позиция {position_index}").strip()
        article = str(item.get("article") or "").strip()
        raw_gtin = str(item.get("gtin") or "").strip()
        codes = list(item.get("codes") or [])

        # Немаркируемые позиции из того же документа добавляются только в Excel
        # после явного выбора пользователя.
        if not codes:
            try:
                possible_gtin = normalize_gtin(raw_gtin)
            except ValueError:
                possible_gtin = ""
            if not possible_gtin or possible_gtin not in normalized_catalog:
                continue

        if not article:
            errors.append(f"{source_name}: отсутствует артикул в МойСклад.")

        normalized_gtin = ""
        if not raw_gtin:
            errors.append(f"{source_name}: отсутствует GTIN.")
        else:
            try:
                normalized_gtin = normalize_gtin(raw_gtin)
            except ValueError as error:
                errors.append(f"{source_name}: некорректный GTIN {raw_gtin}: {error}")

        honest_sign_name = normalized_catalog.get(normalized_gtin, "")
        if normalized_gtin and not honest_sign_name:
            errors.append(f"{source_name}: GTIN {raw_gtin} отсутствует в справочнике Честного ЗНАКа.")

        sale_price = item.get("sale_price")
        net_price = None
        if sale_price is None or str(sale_price).strip() == "":
            errors.append(f"{source_name}: отсутствует цена продажи в МойСклад.")
        else:
            try:
                net_price = calculate_net_price(sale_price)
            except ValueError:
                errors.append(f"{source_name}: цена продажи «{sale_price}» имеет некорректный формат.")

        if not codes:
            errors.append(f"{source_name}: отсутствуют коды маркировки.")

        for code in codes:
            code_text = str(code)
            previous_name = seen_codes.get(code_text)
            if previous_name is not None:
                errors.append(
                    f"КИЗ {code_text} встречается несколько раз: {previous_name}; {source_name}."
                )
            else:
                seen_codes[code_text] = source_name

        prepared_positions.append(
            {
                "article": article,
                "gtin": normalized_gtin,
                "honest_sign_name": honest_sign_name,
                "net_price": net_price,
                "codes": codes,
            }
        )

    if errors:
        raise TrendExportValidationError(errors)

    quantities_by_gtin = {}
    for item in prepared_positions:
        quantities_by_gtin[item["gtin"]] = (
            quantities_by_gtin.get(item["gtin"], 0) + len(item["codes"])
        )

    csv_rows = []
    row_numbers_by_gtin = {}
    for item in sorted(prepared_positions, key=lambda item: item["article"].casefold()):
        row_number = row_numbers_by_gtin.setdefault(
            item["gtin"],
            len(row_numbers_by_gtin) + 1,
        )
        position_name = f"{item['article']} {item['honest_sign_name']}"
        price_text = format(item["net_price"], ".8f")
        for code in item["codes"]:
            csv_rows.append(
                [
                    str(row_number),
                    position_name,
                    price_text,
                    str(quantities_by_gtin[item["gtin"]]),
                    "796",
                    "7%",
                    "КИЗ",
                    str(code),
                ]
            )
    return csv_rows


def write_csv_rows(rows, output_path):
    output_path = Path(output_path)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter=",", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerows(rows)
    return output_path


def create_trend_island_upd_csv(rows, catalog_names, output_path):
    return write_csv_rows(build_trend_island_upd_rows(rows, catalog_names), output_path)


def create_stock_marking_codes_xlsx(rows, output_path):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Коды маркировки"

    row_number = 1
    for item in rows:
        for code in item.get("codes") or []:
            code_text = str(code or "")
            if not code_text:
                continue
            worksheet.cell(row=row_number, column=1, value=code_text)
            row_number += 1

    output_path = Path(output_path)
    workbook.save(output_path)
    return output_path


def create_honest_sign_catalog_csv(products, output_path):
    rows = [["gtin", "honest_sign_name", "size"]]
    rows.extend(
        [
            [
                product.get("gtin") or "",
                product["honest_sign_name"],
                product.get("size") or "",
            ]
            for product in products
        ]
    )
    return write_csv_rows(rows, output_path)
