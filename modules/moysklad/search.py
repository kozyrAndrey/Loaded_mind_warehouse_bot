"""Поиск номенклатуры прямо в МойСклад без локального каталога."""

import re

from modules.marking.duplicate_chz import extract_gtin
from modules.marking.export import build_moysklad_client, response_rows
from modules.marking.moysklad_lookup import gtin_barcode_variants, size_from_characteristics
from modules.moysklad.client import MoySkladError


def normalize_query(raw_query):
    value = str(raw_query or "").strip()
    return extract_gtin(value) or value


def compact_product_name(name, size=""):
    """Убирает служебные характеристики варианта и оставляет название с размером."""
    name = str(name or "").strip()
    size = str(size or "").strip()
    if size and size != "—":
        suffix = re.search(r"\s*\(([^()]*)\)\s*$", name)
        if suffix and suffix.group(1).split(",", 1)[0].strip().casefold() == size.casefold():
            name = name[:suffix.start()].strip()
        if not re.search(rf"(?:^|\s){re.escape(size)}$", name, flags=re.IGNORECASE):
            return f"{name} {size}".strip()
    return name


def name_matches_query(display_name, query):
    name_words = re.findall(r"\w+", str(display_name or "").casefold())
    query_words = re.findall(r"\w+", str(query or "").casefold())
    return bool(query_words) and all(
        any(word == part if len(part) <= 2 else word.startswith(part) for word in name_words)
        for part in query_words
    )


def product_from_row(row):
    meta = row.get("meta") or {}
    name = str(row.get("name") or "").strip()
    size = size_from_characteristics(row)
    display_name = compact_product_name(name, size)
    base_name = display_name.removesuffix(f" {size}") if size and display_name.endswith(f" {size}") else display_name
    return {
        "id": str(row.get("id") or meta.get("id") or ""),
        "name": name,
        "base_name": base_name,
        "display_name": display_name,
        "size": size,
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
    if found:
        return list(found.values())[:limit]

    # Поиск МойСклад может вернуть соседние цвета даже на запрос с цветом.
    # Читаем страницы результата и проверяем все слова по названию варианта.
    page_size = max(100, limit)
    max_scan = 500
    for offset in range(0, max_scan, page_size):
        try:
            payload = client.list_entities(
                "assortment", params={"search": query, "limit": page_size, "offset": offset}
            )
            rows = response_rows(payload)
        except MoySkladError as error:
            last_error = error
            break
        last_error = None
        for row in rows:
            product = product_from_row(row)
            if product["id"] and product["name"] and name_matches_query(product["display_name"], query):
                found[product["id"]] = product
        total = (payload.get("meta") or {}).get("size") if isinstance(payload, dict) else None
        if len(rows) < page_size or (total is not None and offset + len(rows) >= int(total)):
            break
        if len(found) >= limit:
            break
    if not found and last_error:
        raise last_error
    products = list(found.values())
    sized_bases = {product["base_name"].casefold() for product in products if product["size"]}
    products = [product for product in products
                if product["size"] or product["base_name"].casefold() not in sized_bases]
    return products[:limit]
