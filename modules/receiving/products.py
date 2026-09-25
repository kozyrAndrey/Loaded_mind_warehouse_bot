import hashlib
import json
from copy import deepcopy
from pathlib import Path


# ============================================================
# СПИСОК ТОВАРОВ
# ============================================================
# Теперь каталог устроен так:
# группа → модель → цвет / вариант.
#
# Бот будет выбирать:
# группа → модель → цвет / вариант → размер.
#
# Поле CATEGORIES дополнительно создаёт плоский список products,
# чтобы старые модули database.py и google_sheets.py могли продолжать
# получать название товара по product_id.

PRODUCT_CATALOG = {
    "hoodies": {
        "name": "Худи / Зипы",
        "models": {
            "culture_hoodie": {
                "name": "CULTURE HOODIE",
                "variants": {
                    "grey": {
                        "id": "h001",
                        "color": "GREY",
                        "name": "CULTURE HOODIE GREY",
                    },
                    "blue": {
                        "id": "h002",
                        "color": "BLUE",
                        "name": "CULTURE HOODIE BLUE",
                    },
                    "black": {
                        "id": "h003",
                        "color": "BLACK",
                        "name": "CULTURE HOODIE BLACK",
                    },
                },
            },
            "diamond_hoodie": {
                "name": "DIAMOND HOODIE",
                "variants": {
                    "black": {
                        "id": "h004",
                        "color": "BLACK",
                        "name": "DIAMOND HOODIE BLACK",
                    },
                    "melange": {
                        "id": "h005",
                        "color": "MELANGE",
                        "name": "DIAMOND HOODIE MELANGE",
                    },
                    "pink": {
                        "id": "h006",
                        "color": "PINK",
                        "name": "DIAMOND HOODIE PINK",
                    },
                },
            },
            "diamond_v2_zip_hoodie": {
                "name": "DIAMOND V2 ZIP HOODIE",
                "variants": {
                    "dark_blue": {
                        "id": "h010",
                        "color": "DARK BLUE",
                        "name": "DIAMOND V2 ZIP HOODIE DARK BLUE",
                    },
                    "pink": {
                        "id": "h011",
                        "color": "PINK",
                        "name": "DIAMOND V2 ZIP HOODIE PINK",
                    },
                    "black": {
                        "id": "h012",
                        "color": "BLACK",
                        "name": "DIAMOND V2 ZIP HOODIE BLACK",
                    },
                },
            },
            "diamond_zip_hoodie": {
                "name": "DIAMOND ZIP HOODIE",
                "variants": {
                    "one": {
                        "id": "h007",
                        "color": "ONE COLOR",
                        "name": "DIAMOND ZIP HOODIE",
                    },
                },
            },
            "not_paris_zip_hoodie": {
                "name": "NOT PARIS ZIP HOODIE",
                "variants": {
                    "black": {
                        "id": "h008",
                        "color": "BLACK",
                        "name": "NOT PARIS ZIP HOODIE BLACK",
                    },
                },
            },
            "sweetheart_zip_hoodie_crop": {
                "name": "SWEETHEART ZIP HOODIE CROP",
                "variants": {
                    "one": {
                        "id": "h009",
                        "color": "ONE COLOR",
                        "name": "SWEETHEART ZIP HOODIE CROP",
                    },
                },
            },
        },
    },

    "tshirts": {
        "name": "Футболки",
        "models": {
            "not_paris_tshirt": {
                "name": "NOT PARIS T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t001",
                        "color": "BLACK",
                        "name": "NOT PARIS T-SHIRT BLACK",
                    },
                    "white": {
                        "id": "t002",
                        "color": "WHITE",
                        "name": "NOT PARIS T-SHIRT WHITE",
                    },
                },
            },
            "paramount_tshirt": {
                "name": "PARAMOUNT T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t003",
                        "color": "BLACK",
                        "name": "PARAMOUNT T-SHIRT BLACK",
                    },
                    "ecru": {
                        "id": "t004",
                        "color": "ECRU",
                        "name": "PARAMOUNT T-SHIRT ECRU",
                    },
                },
            },
            "stamp_tshirt": {
                "name": "STAMP T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t005",
                        "color": "BLACK",
                        "name": "STAMP T-SHIRT BLACK",
                    },
                    "grey": {
                        "id": "t006",
                        "color": "GREY",
                        "name": "STAMP T-SHIRT GREY",
                    },
                    "ecru": {
                        "id": "t007",
                        "color": "ECRU",
                        "name": "STAMP T-SHIRT ECRU",
                    },
                },
            },
            "stickers_tshirt": {
                "name": "STICKERS T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t008",
                        "color": "BLACK",
                        "name": "STICKERS T-SHIRT BLACK",
                    },
                    "ecru": {
                        "id": "t009",
                        "color": "ECRU",
                        "name": "STICKERS T-SHIRT ECRU",
                    },
                },
            },
            "humble_tshirt": {
                "name": "HUMBLE T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t010",
                        "color": "BLACK",
                        "name": "HUMBLE T-SHIRT BLACK",
                    },
                },
            },
            "network_tshirt": {
                "name": "NETWORK T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t011",
                        "color": "BLACK",
                        "name": "NETWORK T-SHIRT BLACK",
                    },
                    "ecru": {
                        "id": "t012",
                        "color": "ECRU",
                        "name": "NETWORK T-SHIRT ECRU",
                    },
                },
            },
            "hommeless_tshirt": {
                "name": "HOMMELESS T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t013",
                        "color": "BLACK",
                        "name": "HOMMELESS T-SHIRT BLACK",
                    },
                    "grey": {
                        "id": "t014",
                        "color": "GREY",
                        "name": "HOMMELESS T-SHIRT GREY",
                    },
                    "ecru": {
                        "id": "t015",
                        "color": "ECRU",
                        "name": "HOMMELESS T-SHIRT ECRU",
                    },
                },
            },
            "base_logo_tshirt": {
                "name": "BASE LOGO T-SHIRT",
                "variants": {
                    "black": {
                        "id": "t016",
                        "color": "BLACK",
                        "name": "BASE LOGO T-SHIRT BLACK",
                    },
                    "white": {
                        "id": "t017",
                        "color": "WHITE",
                        "name": "BASE LOGO T-SHIRT WHITE",
                    },
                    "grey": {
                        "id": "t018",
                        "color": "GREY",
                        "name": "BASE LOGO T-SHIRT GREY",
                    },
                    "brown": {
                        "id": "t019",
                        "color": "BROWN",
                        "name": "BASE LOGO T-SHIRT BROWN",
                    },
                },
            },
            "fleur_logo_tshirt": {
                "name": "FLEUR LOGO T-SHIRT",
                "variants": {
                    "white": {
                        "id": "t020",
                        "color": "WHITE",
                        "name": "FLEUR LOGO T-SHIRT WHITE",
                    },
                    "black": {
                        "id": "t024",
                        "color": "BLACK",
                        "name": "FLEUR LOGO T-SHIRT BLACK",
                    },
                },
            },
            "cloud_logo_tshirt": {
                "name": "CLOUD LOGO T-SHIRT",
                "variants": {
                    "white": {
                        "id": "t021",
                        "color": "WHITE",
                        "name": "CLOUD LOGO T-SHIRT WHITE",
                    },
                    "black": {
                        "id": "t025",
                        "color": "BLACK",
                        "name": "CLOUD LOGO T-SHIRT BLACK",
                    },
                },
            },
            "honey_logo_tshirt": {
                "name": "HONEY LOGO T-SHIRT",
                "variants": {
                    "one": {
                        "id": "t022",
                        "color": "ONE COLOR",
                        "name": "HONEY LOGO T-SHIRT",
                    },
                },
            },
            "statue_logo_tshirt": {
                "name": "STATUE LOGO T-SHIRT",
                "variants": {
                    "white": {
                        "id": "t023",
                        "color": "WHITE",
                        "name": "STATUE LOGO T-SHIRT WHITE",
                    },
                    "black": {
                        "id": "t027",
                        "color": "BLACK",
                        "name": "STATUE LOGO T-SHIRT BLACK",
                    },
                },
            },
            "chain_logo_tshirt": {
                "name": "CHAIN LOGO T-SHIRT",
                "variants": {
                    "one": {
                        "id": "t026",
                        "color": "ONE COLOR",
                        "name": "CHAIN LOGO T-SHIRT",
                    },
                },
            },
        },
    },

    "shirts": {
    "name": "Рубашки",
        "models": {
            "diamond_shirt": {
                "name": "DIAMOND SHIRT",
                "variants": {
                    "one": {
                        "id": "sh001",
                        "color": "ONE COLOR",
                        "name": "DIAMOND SHIRT",
                    },
                },
            },
        },
    },

    "pants": {
        "name": "Штаны / Джинсы",
        "models": {
            "culture_pants": {
                "name": "CULTURE PANTS",
                "variants": {
                    "grey": {
                        "id": "p001",
                        "color": "GREY",
                        "name": "CULTURE PANTS GREY",
                    },
                    "blue": {
                        "id": "p002",
                        "color": "BLUE",
                        "name": "CULTURE PANTS BLUE",
                    },
                    "black": {
                        "id": "p003",
                        "color": "BLACK",
                        "name": "CULTURE PANTS BLACK",
                    },
                },
            },
            "diamond_pants": {
                "name": "DIAMOND PANTS",
                "variants": {
                    "black": {
                        "id": "p004",
                        "color": "BLACK",
                        "name": "DIAMOND PANTS BLACK",
                    },
                    "melange": {
                        "id": "p005",
                        "color": "MELANGE",
                        "name": "DIAMOND PANTS MELANGE",
                    },
                    "pink": {
                        "id": "p006",
                        "color": "PINK",
                        "name": "DIAMOND PANTS PINK",
                    },
                },
            },
            "diamond_jeans": {
                "name": "DIAMOND JEANS",
                "variants": {
                    "one": {
                        "id": "p007",
                        "color": "ONE COLOR",
                        "name": "DIAMOND JEANS",
                    },
                },
            },
            "monogram_jeans": {
                "name": "MONOGRAM JEANS",
                "variants": {
                    "stone_black": {
                        "id": "p008",
                        "color": "STONE BLACK",
                        "name": "MONOGRAM JEANS STONE BLACK",
                    },
                    "washed_blue": {
                        "id": "p009",
                        "color": "WASHED BLUE",
                        "name": "MONOGRAM JEANS WASHED BLUE",
                    },
                    "sky_blue": {
                        "id": "p010",
                        "color": "SKY BLUE",
                        "name": "MONOGRAM JEANS SKY BLUE",
                    },
                },
            },
        },
    },

    "shorts": {
        "name": "Шорты",
        "models": {
            "apparel_shorts": {
                "name": "APPAREL SHORTS",
                "variants": {
                    "black": {
                        "id": "s001",
                        "color": "BLACK",
                        "name": "APPAREL SHORTS BLACK",
                    },
                    "melange": {
                        "id": "s002",
                        "color": "MELANGE",
                        "name": "APPAREL SHORTS MELANGE",
                    },
                },
            },
            "hm_shorts": {
                "name": "HM SHORTS",
                "variants": {
                    "black": {
                        "id": "s003",
                        "color": "BLACK",
                        "name": "HM SHORTS BLACK",
                    },
                    "melange": {
                        "id": "s004",
                        "color": "MELANGE",
                        "name": "HM SHORTS MELANGE",
                    },
                    "blue": {
                        "id": "s005",
                        "color": "BLUE",
                        "name": "HM SHORTS BLUE",
                    },
                },
            },
            "homme_shorts": {
                "name": "HOMME SHORTS",
                "variants": {
                    "black": {
                        "id": "s006",
                        "color": "BLACK",
                        "name": "HOMME SHORTS BLACK",
                    },
                    "melange": {
                        "id": "s007",
                        "color": "MELANGE",
                        "name": "HOMME SHORTS MELANGE",
                    },
                },
            },
        },
    },

    "bombers": {
        "name": "Бомберы",
        "models": {
            "base_bomber": {
                "name": "BASE BOMBER",
                "variants": {
                    "black": {
                        "id": "b001",
                        "color": "BLACK",
                        "name": "BASE BOMBER BLACK",
                    },
                    "grey": {
                        "id": "b002",
                        "color": "GREY",
                        "name": "BASE BOMBER GREY",
                    },
                },
            },
            "diamond_bomber": {
                "name": "DIAMOND BOMBER",
                "variants": {
                    "one": {
                        "id": "b003",
                        "color": "ONE COLOR",
                        "name": "DIAMOND BOMBER",
                    },
                },
            },
            "corset_bomber": {
                "name": "CORSET BOMBER",
                "variants": {
                    "one": {
                        "id": "b004",
                        "color": "ONE COLOR",
                        "name": "CORSET BOMBER",
                    },
                },
            },
        },
    },

    "belts": {
        "name": "Ремни",
        "models": {
            "og_belt": {
                "name": "OG BELT",
                "variants": {
                    "diamond": {
                        "id": "belt001",
                        "color": "DIAMOND",
                        "name": "DIAMOND OG BELT",
                    },
                    "base": {
                        "id": "belt002",
                        "color": "BASE",
                        "name": "BASE OG BELT",
                    },
                    "black": {
                        "id": "belt003",
                        "color": "BLACK",
                        "name": "BLACK OG BELT",
                    },
                    "pink": {
                        "id": "belt004",
                        "color": "PINK",
                        "name": "PINK OG BELT",
                    },
                    "white": {
                        "id": "belt006",
                        "color": "WHITE",
                        "name": "WHITE OG BELT",
                    },
                    "leo": {
                        "id": "belt005",
                        "color": "LEO",
                        "name": "LEO OG BELT",
                    },
                },
            },
        },
    },

    "vests": {
        "name": "Жилетки",
        "models": {
            "base_puffer_vest": {
                "name": "BASE PUFFER VEST",
                "variants": {
                    "black": {
                        "id": "v001",
                        "color": "BLACK",
                        "name": "BASE PUFFER VEST BLACK",
                    },
                },
            },
            "reversible_puffer_vest": {
                "name": "REVERSIBLE PUFFER VEST",
                "variants": {
                    "white": {
                        "id": "v002",
                        "color": "WHITE",
                        "name": "REVERSIBLE PUFFER VEST WHITE",
                    },
                    "red": {
                        "id": "v003",
                        "color": "RED",
                        "name": "REVERSIBLE PUFFER VEST RED",
                    },
                },
            },
            "carbon_puffer_vest": {
                "name": "CARBON PUFFER VEST",
                "variants": {
                    "black": {
                        "id": "v004",
                        "color": "BLACK",
                        "name": "CARBON BLACK PUFFER VEST",
                    },
                },
            },
            "diamond_puffer_vest": {
                "name": "DIAMOND PUFFER VEST",
                "variants": {
                    "one": {
                        "id": "v005",
                        "color": "ONE COLOR",
                        "name": "DIAMOND PUFFER VEST",
                    },
                },
            },
        },
    },

    "leather": {
        "name": "Кожанки",
        "models": {
            "homme_leather_jacket": {
                "name": "HOMME LEATHER JACKET",
                "variants": {
                    "black": {
                        "id": "l001",
                        "color": "BLACK",
                        "name": "HOMME LEATHER JACKET BLACK",
                    },
                    "grey": {
                        "id": "l002",
                        "color": "GREY",
                        "name": "HOMME LEATHER JACKET GREY",
                    },
                    "green": {
                        "id": "l003",
                        "color": "GREEN",
                        "name": "HOMME LEATHER JACKET GREEN",
                    },
                },
            },
        },
    },

    "bags": {
        "name": "Сумки",
        "models": {
            "million_dollar_birkin_bag": {
                "name": "MILLION DOLLAR BIRKIN BAG",
                "variants": {
                    "one": {
                        "id": "bag001",
                        "color": "ONE COLOR",
                        "name": "MILLION DOLLAR BIRKIN BAG",
                    },
                },
            },
            "homme_birkin_messenger_bag": {
                "name": "HOMME BIRKIN MESSENGER BAG",
                "variants": {
                    "one": {
                        "id": "bag002",
                        "color": "ONE COLOR",
                        "name": "HOMME BIRKIN MESSENGER BAG",
                    },
                },
            },
            "homme_birkin_shoulder_bag": {
                "name": "HOMME BIRKIN SHOULDER BAG",
                "variants": {
                    "one": {
                        "id": "bag003",
                        "color": "ONE COLOR",
                        "name": "HOMME BIRKIN SHOULDER BAG",
                    },
                },
            },
            "hm_messenger_bag": {
                "name": "HM MESSENGER BAG",
                "variants": {
                    "one": {
                        "id": "bag004",
                        "color": "ONE COLOR",
                        "name": "HM MESSENGER BAG",
                    },
                },
            },
        },
    },

    "accessories": {
        "name": "Аксессуары",
        "models": {
            "gift_card": {
                "name": "Подарочный сертификат",
                "variants": {
                    "5000": {
                        "id": "a001",
                        "color": "5000",
                        "name": "Подарочный сертификат 5000",
                    },
                },
            },
        },
    },
}


CUSTOM_PRODUCTS_PATH = Path(__file__).resolve().parents[2] / "resources" / "products" / "custom_products.json"
PRODUCT_OVERRIDES_KEY = "__product_overrides__"


def _hash_key(*parts):
    raw = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _generated_key(prefix, *parts):
    return f"{prefix}_{_hash_key(*parts)}"


def _read_custom_catalog():
    if not CUSTOM_PRODUCTS_PATH.exists():
        return {}

    try:
        with CUSTOM_PRODUCTS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def _write_custom_catalog(catalog):
    CUSTOM_PRODUCTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CUSTOM_PRODUCTS_PATH.open("w", encoding="utf-8") as file:
        json.dump(catalog, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _find_category_id_by_name(category_name):
    target = str(category_name or "").strip().lower()
    for category_id, category_data in CATEGORIES.items():
        if str(category_data.get("name", "")).strip().lower() == target:
            return category_id
    return None


def _find_model_id_by_name(category_id, model_name):
    target = str(model_name or "").strip().lower()
    models = CATEGORIES.get(category_id, {}).get("models", {})
    for model_id, model_data in models.items():
        if str(model_data.get("name", "")).strip().lower() == target:
            return model_id
    return None


def _build_flat_products(category_data):
    products = {}

    for model_data in category_data["models"].values():
        for variant_data in model_data["variants"].values():
            products[variant_data["id"]] = variant_data["name"]

    return products


CATEGORIES = {}

def _merge_category(category_id, category_data):
    CATEGORIES[category_id] = {
        "name": category_data["name"],
        "models": deepcopy(category_data["models"]),
        "products": _build_flat_products(category_data),
    }


def _refresh_category_products(category_id):
    CATEGORIES[category_id]["products"] = _build_flat_products(CATEGORIES[category_id])


def _merge_custom_catalog(catalog):
    for category_id, category_data in catalog.items():
        if category_id == PRODUCT_OVERRIDES_KEY:
            continue
        if category_id not in CATEGORIES:
            CATEGORIES[category_id] = {
                "name": category_data.get("name", category_id),
                "models": {},
                "products": {},
            }

        if category_data.get("name"):
            CATEGORIES[category_id]["name"] = category_data["name"]

        for model_id, model_data in category_data.get("models", {}).items():
            if model_id not in CATEGORIES[category_id]["models"]:
                CATEGORIES[category_id]["models"][model_id] = {
                    "name": model_data.get("name", model_id),
                    "variants": {},
                }

            if model_data.get("name"):
                CATEGORIES[category_id]["models"][model_id]["name"] = model_data["name"]

            CATEGORIES[category_id]["models"][model_id]["variants"].update(model_data.get("variants", {}))

        _refresh_category_products(category_id)


def _find_product(product_id):
    product_id = str(product_id or "").strip()
    for category_id, category_data in CATEGORIES.items():
        for model_id, model_data in category_data["models"].items():
            for variant_id, variant_data in model_data["variants"].items():
                if str(variant_data.get("id")) == product_id:
                    return category_id, model_id, variant_id, variant_data
    return None


def _apply_product_override(product_id, override):
    location = _find_product(product_id)
    if not location:
        return

    found_category_id, found_model_id, found_variant_id, variant_data = location
    category_id = str(override.get("category_id") or found_category_id)
    model_id = str(override.get("model_id") or found_model_id)
    category = CATEGORIES.get(category_id)
    model = category.get("models", {}).get(model_id) if category else None
    if not category or not model:
        category_id, model_id, found_variant_id, variant_data = location
        category = CATEGORIES[category_id]
        model = category["models"][model_id]

    category_name = str(override.get("category_name") or category["name"]).strip()
    model_name = str(override.get("model_name") or model["name"]).strip()
    color = str(override.get("color") or variant_data.get("color") or "ONE COLOR").strip().upper()

    category["name"] = category_name
    model["name"] = model_name
    for current_variant in model["variants"].values():
        current_color = str(current_variant.get("color") or "ONE COLOR").strip().upper()
        current_variant["name"] = (
            model_name if current_color == "ONE COLOR" else f"{model_name} {current_color}"
        )
    variant_data["color"] = color
    variant_data["name"] = model_name if color == "ONE COLOR" else f"{model_name} {color}"
    _refresh_category_products(category_id)


def _apply_product_overrides(catalog):
    overrides = catalog.get(PRODUCT_OVERRIDES_KEY, {})
    if not isinstance(overrides, dict):
        return
    for product_id, override in overrides.items():
        if isinstance(override, dict):
            _apply_product_override(product_id, override)


def reload_product_catalog():
    custom_catalog = _read_custom_catalog()
    CATEGORIES.clear()
    for category_id, category_data in PRODUCT_CATALOG.items():
        _merge_category(category_id, category_data)
    _merge_custom_catalog(custom_catalog)
    _apply_product_overrides(custom_catalog)


def add_custom_product(category_name, model_name, color):
    category_name = str(category_name or "").strip()
    model_name = str(model_name or "").strip()
    color = str(color or "").strip()

    if not category_name or not model_name:
        raise ValueError("Нужно указать группу и модель.")

    if not color:
        color = "ONE COLOR"
    color = color.upper()

    reload_product_catalog()

    category_id = _find_category_id_by_name(category_name) or _generated_key("cat", category_name)
    model_id = _find_model_id_by_name(category_id, model_name) or _generated_key("model", category_name, model_name)
    existing_variants = CATEGORIES.get(category_id, {}).get("models", {}).get(model_id, {}).get("variants", {})
    if any(
        str(variant.get("color") or "ONE COLOR").strip().casefold() == color.casefold()
        for variant in existing_variants.values()
    ):
        raise ValueError("Товар такого цвета уже существует в этой модели.")
    variant_id = _generated_key("variant", category_name, model_name, color)
    product_id = _generated_key("custom", category_name, model_name, color)
    product_name = model_name if color == "ONE COLOR" else f"{model_name} {color}"

    catalog = _read_custom_catalog()
    category_data = catalog.setdefault(category_id, {"name": category_name, "models": {}})
    category_data["name"] = CATEGORIES.get(category_id, {}).get("name", category_name)
    model_data = category_data.setdefault("models", {}).setdefault(model_id, {"name": model_name, "variants": {}})
    model_data["name"] = CATEGORIES.get(category_id, {}).get("models", {}).get(model_id, {}).get("name", model_name)
    model_data.setdefault("variants", {})[variant_id] = {
        "id": product_id,
        "color": color,
        "name": product_name,
    }

    _write_custom_catalog(catalog)
    reload_product_catalog()

    return {
        "category_id": category_id,
        "category_name": CATEGORIES[category_id]["name"],
        "model_id": model_id,
        "model_name": CATEGORIES[category_id]["models"][model_id]["name"],
        "product_id": product_id,
        "product_name": CATEGORIES[category_id]["products"][product_id],
        "color": color,
    }


def get_catalog_product(product_id):
    location = _find_product(product_id)
    if not location:
        return None
    category_id, model_id, variant_id, variant_data = location
    category_data = CATEGORIES[category_id]
    model_data = category_data["models"][model_id]
    return {
        "category_id": category_id,
        "category_name": category_data["name"],
        "model_id": model_id,
        "model_name": model_data["name"],
        "variant_id": variant_id,
        "product_id": str(variant_data["id"]),
        "product_name": variant_data["name"],
        "color": variant_data.get("color") or "ONE COLOR",
    }


def update_catalog_product(product_id, category_name, model_name, color):
    product_id = str(product_id or "").strip()
    category_name = str(category_name or "").strip()
    model_name = str(model_name or "").strip()
    color = str(color or "").strip().upper() or "ONE COLOR"
    current_product = get_catalog_product(product_id)
    if not product_id or not current_product:
        raise ValueError("Товар не найден.")
    if not category_name or not model_name:
        raise ValueError("Группа и модель не должны быть пустыми.")

    current_category_id = current_product["category_id"]
    current_model_id = current_product["model_id"]
    for category_id, category_data in CATEGORIES.items():
        if (
            category_id != current_category_id
            and category_data["name"].strip().casefold() == category_name.casefold()
        ):
            raise ValueError("Группа с таким названием уже существует.")
    for model_id, model_data in CATEGORIES[current_category_id]["models"].items():
        if (
            model_id != current_model_id
            and model_data["name"].strip().casefold() == model_name.casefold()
        ):
            raise ValueError("Модель с таким названием уже существует в этой группе.")
    for variant_data in CATEGORIES[current_category_id]["models"][current_model_id]["variants"].values():
        if (
            str(variant_data["id"]) != product_id
            and str(variant_data.get("color") or "ONE COLOR").strip().casefold() == color.casefold()
        ):
            raise ValueError("Товар такого цвета уже существует в этой модели.")

    catalog = _read_custom_catalog()
    overrides = catalog.setdefault(PRODUCT_OVERRIDES_KEY, {})
    affected_product_ids = []
    for catalog_model_id, model_data in CATEGORIES[current_category_id]["models"].items():
        updated_model_name = model_name if catalog_model_id == current_model_id else model_data["name"]
        for variant_data in model_data["variants"].values():
            current_id = str(variant_data["id"])
            current_color = str(variant_data.get("color") or "ONE COLOR").strip().upper()
            if current_id == product_id:
                current_color = color
            overrides[current_id] = {
                "category_id": current_category_id,
                "model_id": catalog_model_id,
                "category_name": category_name,
                "model_name": updated_model_name,
                "color": current_color,
            }
            if catalog_model_id == current_model_id:
                affected_product_ids.append(current_id)
    _write_custom_catalog(catalog)
    reload_product_catalog()
    result = get_catalog_product(product_id)
    result["updated_products"] = [
        get_catalog_product(affected_product_id) for affected_product_id in affected_product_ids
    ]
    return result


reload_product_catalog()


SIZES = ["2XS", "XS", "S", "M", "L", "XL", "2XL", "3XL", "ONE SIZE"]


def get_product_name(category_id, product_id):
    return CATEGORIES[category_id]["products"][product_id]


def get_model_name(category_id, model_id):
    return CATEGORIES[category_id]["models"][model_id]["name"]


def get_variant_by_product_id(category_id, product_id):
    models = CATEGORIES[category_id]["models"]

    for model_id, model_data in models.items():
        for variant_key, variant_data in model_data["variants"].items():
            if variant_data["id"] == product_id:
                return model_id, variant_key, variant_data

    return None, None, None
