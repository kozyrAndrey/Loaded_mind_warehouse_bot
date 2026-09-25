import asyncio
import logging
import time

from modules.marking.export import (
    build_moysklad_client,
    extract_article,
    get_all_assortment_rows,
)


logger = logging.getLogger(__name__)
CATALOG_CACHE_TTL_SECONDS = 15 * 60
_catalog_cache = None
_catalog_cached_at = 0.0


def _normalized_article(value):
    return str(value or "").strip().casefold()


def _assortment_priority(row):
    entity_type = str((row.get("meta") or {}).get("type") or "").casefold()
    return (
        1 if entity_type == "variant" else 0,
        1 if str(row.get("name") or "").strip() else 0,
    )


def _catalog_rows(client, *, force_refresh=False):
    global _catalog_cache, _catalog_cached_at
    now = time.monotonic()
    if (
        not force_refresh
        and _catalog_cache is not None
        and now - _catalog_cached_at < CATALOG_CACHE_TTL_SECONDS
    ):
        return _catalog_cache

    _catalog_cache = get_all_assortment_rows(client)
    _catalog_cached_at = now
    return _catalog_cache


def find_product_names_by_articles(client, articles, *, assortment_rows=None):
    """Return exact article -> full assortment name matches from MoySklad.

    In this catalog seller articles are stored on variants as the characteristic
    named ``Артикул``, so MoySklad's regular ``article=`` filter cannot find them.
    """
    requested = {
        _normalized_article(article): str(article).strip()
        for article in articles or []
        if str(article or "").strip()
    }
    if not requested:
        return {}

    rows = assortment_rows if assortment_rows is not None else _catalog_rows(client)
    candidates = {key: [] for key in requested}
    for row in rows:
        if not isinstance(row, dict):
            continue
        article = extract_article(row, row.get("product") or {})
        key = _normalized_article(article)
        name = str(row.get("name") or "").strip()
        if key in candidates and name:
            candidates[key].append(row)

    result = {}
    for key, matching_rows in candidates.items():
        if not matching_rows:
            continue
        selected = max(matching_rows, key=_assortment_priority)
        result[requested[key]] = str(selected.get("name") or "").strip()
    return result


def _item_article(item):
    return str(item.get("externalSku") or item.get("sellerSku") or "").strip()


async def enrich_order_product_names(orders, client=None):
    """Attach MoySklad names to Lamoda item dictionaries without blocking assembly."""
    articles = sorted({
        article
        for order in orders or []
        for item in (order.get("items") or order.get("orderItems") or [])
        if (article := _item_article(item))
    })
    if not articles:
        return {"matched": {}, "missing": [], "error": ""}

    try:
        client = client or build_moysklad_client()
        matched = await asyncio.to_thread(find_product_names_by_articles, client, articles)
    except Exception as error:
        logger.exception("Could not enrich Lamoda products from MoySklad")
        return {"matched": {}, "missing": articles, "error": str(error)}

    by_normalized_article = {
        _normalized_article(article): name for article, name in matched.items()
    }
    for order in orders or []:
        for item in (order.get("items") or order.get("orderItems") or []):
            name = by_normalized_article.get(_normalized_article(_item_article(item)))
            if name:
                item["_moysklad_name"] = name

    missing = [
        article for article in articles
        if _normalized_article(article) not in by_normalized_article
    ]
    return {"matched": matched, "missing": missing, "error": ""}
