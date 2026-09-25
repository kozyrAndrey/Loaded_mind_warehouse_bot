import re
from dataclasses import dataclass, field

from modules.marking.duplicate_chz import (
    GROUP_SEPARATOR,
    extract_gs1_ai_values,
    normalize_chz_text,
)
from modules.marking.storage import get_honest_sign_product


class MarkingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedMarkingCode:
    raw: str
    normalized: str
    uit: str
    gtin: str
    serial: str
    warnings: list[str] = field(default_factory=list)


def _normalized_fingerprint_source(value):
    return normalize_chz_text(value).replace(" ", "")


def parse_marking_code(value):
    raw = str(value or "")
    normalized = _normalized_fingerprint_source(raw)
    if not normalized:
        raise MarkingValidationError("Код маркировки пустой.")
    values = extract_gs1_ai_values(normalized)
    gtin = str(values.get("01") or "")
    serial = str(values.get("21") or "").strip(GROUP_SEPARATOR)
    if len(gtin) != 14 or not gtin.isdigit():
        raise MarkingValidationError("В КИЗ не найден корректный 14-значный GTIN (AI 01).")
    if not serial:
        raise MarkingValidationError("В КИЗ не найден серийный номер (AI 21).")
    if len(serial) > 64:
        raise MarkingValidationError("Серийный номер КИЗ имеет недопустимую длину.")
    uit = f"01{gtin}21{serial}"
    return ParsedMarkingCode(raw=raw, normalized=normalized, uit=uit, gtin=gtin, serial=serial)


def normalize_size(value):
    return re.sub(r"[^0-9a-zа-я]+", "", str(value or "").lower())


def validate_against_catalog(parsed, product_name="", size=""):
    """Return warnings; block only an explicit catalog size conflict."""
    try:
        catalog = get_honest_sign_product(parsed.gtin)
    except Exception:
        catalog = None
    if not catalog:
        return ["GTIN отсутствует в локальном справочнике Честного ЗНАКа."]
    expected_size = normalize_size(catalog.get("size"))
    actual_size = normalize_size(size)
    if expected_size and actual_size and expected_size != actual_size:
        raise MarkingValidationError(
            f"Размер по КИЗ ({catalog.get('size')}) не совпадает с размером Lamoda ({size})."
        )
    warnings = []
    catalog_name = str(catalog.get("honest_sign_name") or "").strip()
    if catalog_name and product_name and catalog_name.casefold() not in product_name.casefold() and product_name.casefold() not in catalog_name.casefold():
        warnings.append(f"Наименование в справочнике ЧЗ: {catalog_name}.")
    return warnings

def short_kiz(value):
    text = normalize_chz_text(value)
    return text if len(text) <= 18 else f"{text[:8]}…{text[-8:]}"
