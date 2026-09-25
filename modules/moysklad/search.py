"""Поиск номенклатуры прямо в МойСклад без локального каталога."""

from modules.marking.duplicate_chz import extract_gtin
from modules.marking.export import build_moysklad_client, response_rows
from modules.marking.moysklad_lookup import gtin_barcode_variants, size_from_characteristics
from modules.moysklad.client import MoySkladError


def normalize_query(raw_query):
    value = str(raw_query or "").strip()
    return extract_gtin(value) or value


def product_from_row(row):
    meta = row.get("meta") or {}
    return {
        "id": str(row.get("id") or meta.get("id") or ""),
        "name": str(row.get("name") or "").strip(),
        "size": size_from_characteristics(row),
        "article": str(row.get("article") or "").strip(),
        "barcodes": [str(value) for code in row.get("barcodes") or []
                     if isinstance(code, dict) for value in code.values() if value],
    }


def search_products(raw_query, *, client=None, limit=20):
    query = normalize_query(raw_query)
    if len(query) < 3:
        raise ValueError("Введите хотя бы 3 символа названия или полный штрихкод.")
    client = client or build_moysklad_client()
    attempts = []
    if query.isdigit() and len(query) in {13, 14}:
        attempts.extend((entity_type, {"filter": f"barcode={barcode}", "limit": limit})
                        for barcode in gtin_barcode_variants(query)
                        for entity_type in ("assortment", "variant", "product"))
    attempts.append(("assortment", {"search": query, "limit": limit}))
    found = {}
    last_error = None
    for entity_type, params in attempts:
        try:
            rows = response_rows(client.list_entities(entity_type, params=params))
        except MoySkladError as error:
            last_error = error
            continue
        last_error = None
        for row in rows:
            product = product_from_row(row)
            if product["id"] and product["name"]:
                found[product["id"]] = product
        if found:
            break
    if not found and last_error:
        raise last_error
    return list(found.values())[:limit]
