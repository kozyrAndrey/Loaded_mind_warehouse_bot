import logging

from modules.moysklad.client import MoySkladError


def reference_description(client, value, cache=None):
    """Return a custom-directory item's description without exposing its label."""
    if not isinstance(value, dict):
        return ""

    description = str(value.get("description") or "").strip()
    if description:
        return description

    href = str((value.get("meta") or {}).get("href") or "").strip()
    if not href:
        return ""

    cache = cache if cache is not None else {}
    if href not in cache:
        try:
            cache[href] = client.get_href(href)
        except (AttributeError, MoySkladError):
            logging.exception("Не удалось получить элемент справочника МойСклад: %s", href)
            cache[href] = {}
    return str((cache[href] or {}).get("description") or "").strip()


def resolve_reference_attribute_descriptions(client, entity, attribute_names, cache=None):
    """Replace selected custom-directory references with their descriptions."""
    wanted = {str(name).strip().casefold() for name in attribute_names}
    for attribute in (entity or {}).get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        if str(attribute.get("name") or "").strip().casefold() not in wanted:
            continue
        description = reference_description(client, attribute.get("value"), cache=cache)
        if description:
            attribute["value"] = description
    return entity
