from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from core.keyboards import build_products_menu_keyboard
from modules.consumables.storage import (
    get_consumable_items,
    set_product_consumable_rule,
    update_product_consumable_rules_name,
)
from modules.marking.storage import get_honest_sign_product, normalize_gtin, upsert_honest_sign_products
from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager
from modules.receiving.products import (
    CATEGORIES,
    SIZES,
    add_custom_product,
    get_catalog_product,
    reload_product_catalog,
    update_catalog_product,
)


(
    PRODUCT_ADD_CATEGORY,
    PRODUCT_ADD_MODEL,
    PRODUCT_ADD_COLOR,
    PRODUCT_ADD_SIZES,
    PRODUCT_ADD_MARKED,
    PRODUCT_ADD_CHZ_NAME,
    PRODUCT_ADD_GTIN,
    PRODUCT_ADD_RULE_SELECT,
    PRODUCT_ADD_RULE_QUANTITY,
    PRODUCT_ADD_CONFIRM,
) = range(1700, 1710)

(
    PRODUCT_EDIT_CATEGORY,
    PRODUCT_EDIT_MODEL,
    PRODUCT_EDIT_PRODUCT,
    PRODUCT_EDIT_FIELD,
    PRODUCT_EDIT_VALUE,
) = range(1710, 1715)

PRODUCT_ADD_MODEL_SELECT = 1715
PRODUCT_ADD_CUSTOM_SIZE = 1716


def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")]])


def navigation_keyboard(back_target):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"prodback:{back_target}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )


def product_categories_keyboard():
    rows = []
    for category_id, category in sorted(CATEGORIES.items(), key=lambda item: item[1]["name"]):
        rows.append([InlineKeyboardButton(category["name"][:58], callback_data=f"prodcat:{category_id}")])
    rows.extend(
        [
            [InlineKeyboardButton("➕ Новая группа", callback_data="prodcat:new")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_add_models_keyboard(category_id):
    models = CATEGORIES.get(category_id, {}).get("models", {})
    rows = [
        [InlineKeyboardButton(model["name"][:58], callback_data=f"prodaddmodel:{model_id}")]
        for model_id, model in sorted(models.items(), key=lambda item: item[1]["name"])
    ]
    rows.extend(
        [
            [InlineKeyboardButton("➕ Новая модель", callback_data="prodaddmodel:new")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="prodback:category")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_sizes_keyboard(selected_sizes):
    rows = []
    available_sizes = list(SIZES) + [size for size in selected_sizes if size not in SIZES]
    for size in available_sizes:
        marker = "✅ " if size in selected_sizes else ""
        rows.append([InlineKeyboardButton(marker + size, callback_data=f"prodsize:{size}")])
    rows.extend(
        [
            [InlineKeyboardButton("➕ Другой размер", callback_data="prodsize:custom")],
            [InlineKeyboardButton("➡️ Далее", callback_data="prodsize:done")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="prodback:color")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_marking_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏷 Да, маркируемый", callback_data="prodmark:yes")],
            [InlineKeyboardButton("📦 Нет, немаркируемый", callback_data="prodmark:no")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="prodback:sizes")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )


def product_rules_keyboard(items, rules, is_marked=False):
    rules_by_item = {int(rule["item_id"]): rule for rule in rules}
    rows = []
    for item in items:
        rule = rules_by_item.get(int(item["item_id"]))
        suffix = f" ✅ {rule['quantity']} {item['unit']}" if rule else ""
        rows.append(
            [
                InlineKeyboardButton(
                    (item["name"] + suffix)[:58],
                    callback_data=f"prodrule:{item['item_id']}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton("➡️ Далее", callback_data="prodrule:done")],
            [InlineKeyboardButton("Пропустить нормы", callback_data="prodrule:skip")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"prodback:{'gtin' if is_marked else 'marking'}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_confirm_keyboard(has_consumables=True):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Создать товар", callback_data="prodconfirm:yes")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"prodback:{'rules' if has_consumables else 'marking'}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )


def product_edit_categories_keyboard():
    rows = [
        [InlineKeyboardButton(category["name"][:58], callback_data=f"prodeditcat:{category_id}")]
        for category_id, category in sorted(CATEGORIES.items(), key=lambda item: item[1]["name"])
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")])
    return InlineKeyboardMarkup(rows)


def product_edit_models_keyboard(category_id):
    models = CATEGORIES.get(category_id, {}).get("models", {})
    rows = [
        [InlineKeyboardButton(model["name"][:58], callback_data=f"prodeditmodel:{model_id}")]
        for model_id, model in sorted(models.items(), key=lambda item: item[1]["name"])
    ]
    rows.extend(
        [
            [InlineKeyboardButton("⬅️ К группам", callback_data="prodedit:categories")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_edit_products_keyboard(category_id, model_id):
    variants = CATEGORIES.get(category_id, {}).get("models", {}).get(model_id, {}).get("variants", {})
    rows = [
        [InlineKeyboardButton(variant["name"][:58], callback_data=f"prodeditproduct:{variant['id']}")]
        for variant in sorted(variants.values(), key=lambda item: item["name"])
    ]
    rows.extend(
        [
            [InlineKeyboardButton("⬅️ К моделям", callback_data="prodedit:models")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def product_edit_fields_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Группа", callback_data="prodeditfield:category_name")],
            [InlineKeyboardButton("Модель", callback_data="prodeditfield:model_name")],
            [InlineKeyboardButton("Цвет / вариант", callback_data="prodeditfield:color")],
            [InlineKeyboardButton("➕ Добавить цвет / вариант", callback_data="prodedit:add_variant")],
            [InlineKeyboardButton("➕ Добавить модель в группу", callback_data="prodedit:add_model")],
            [InlineKeyboardButton("✅ Сохранить", callback_data="prodedit:save")],
            [InlineKeyboardButton("❌ Отмена", callback_data="prodadmin:cancel")],
        ]
    )


def product_edit_preview_text(data):
    product_name = data["model_name"] if data["color"] == "ONE COLOR" else f"{data['model_name']} {data['color']}"
    return (
        "Изменение товара\n\n"
        f"Группа: {data['category_name']}\n"
        f"Модель: {data['model_name']}\n"
        f"Цвет / вариант: {data['color']}\n"
        f"Итоговое название: {product_name}\n\n"
        "Выберите поле или сохраните изменения."
    )


def product_preview_text(data):
    lines = [
        "Новый товар",
        "",
        f"Группа: {data.get('category_name')}",
        f"Модель: {data.get('model_name')}",
        f"Цвет / вариант: {data.get('color')}",
        "Размеры: " + ", ".join(data.get("sizes", [])),
    ]
    if data.get("is_marked"):
        lines.extend(["", f"Название ЧЗ: {data.get('chz_base_name')}", "GTIN:"])
        for item in data.get("marking", []):
            lines.append(f"• {item['size']}: {item['gtin']} — {item['honest_sign_name']}")
    else:
        lines.append("Маркировка: немаркируемый товар")
    rules = data.get("consumable_rules", [])
    if rules:
        lines.extend(["", "Нормы расходников:"])
        lines.extend(f"• {rule['item_name']}: {rule['quantity']} {rule['unit']}" for rule in rules)
    else:
        lines.append("Нормы расходников: не заданы")
    return "\n".join(lines)


async def prompt_product_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = get_consumable_items(active_only=True)
    data = context.user_data["product_add"]
    if not items:
        if update.callback_query:
            await update.callback_query.edit_message_text(product_preview_text(data), reply_markup=product_confirm_keyboard(False))
        else:
            await update.message.reply_text(product_preview_text(data), reply_markup=product_confirm_keyboard(False))
        return PRODUCT_ADD_CONFIRM
    text = "Выберите расходник и укажите норму на одну единицу товара. Можно выбрать несколько."
    markup = product_rules_keyboard(items, data.get("consumable_rules", []), data.get("is_marked", False))
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)
    return PRODUCT_ADD_RULE_SELECT


def current_employee(update: Update):
    return find_employee_for_telegram_user(update.effective_user)


def ensure_manager(update: Update):
    return is_manager(current_employee(update))


async def product_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление товарами доступно только руководителям.")
        return ConversationHandler.END

    context.user_data["product_add"] = {}
    await query.edit_message_text(
        "Выберите группу товара или добавьте новую:",
        reply_markup=product_categories_keyboard(),
    )
    return PRODUCT_ADD_CATEGORY


async def product_add_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = query.data.replace("prodcat:", "")
    if category_id == "new":
        await query.edit_message_text("Введите название новой группы товара:", reply_markup=navigation_keyboard("category"))
        return PRODUCT_ADD_CATEGORY
    category = CATEGORIES.get(category_id)
    if not category:
        await query.edit_message_text("Группа не найдена. Выберите группу заново:", reply_markup=product_categories_keyboard())
        return PRODUCT_ADD_CATEGORY
    context.user_data["product_add"].update(
        {"category_id": category_id, "category_name": category["name"]}
    )
    await query.edit_message_text(
        "Выберите существующую модель, чтобы добавить новый цвет / вариант, или создайте новую:",
        reply_markup=product_add_models_keyboard(category_id),
    )
    return PRODUCT_ADD_MODEL_SELECT


async def product_add_category_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category_name = (update.message.text or "").strip()
    if not category_name:
        await update.message.reply_text("Введите непустое название группы:", reply_markup=navigation_keyboard("category"))
        return PRODUCT_ADD_CATEGORY

    context.user_data["product_add"]["category_name"] = category_name
    await update.message.reply_text(
        "Введите название модели:",
        reply_markup=navigation_keyboard("category"),
    )
    return PRODUCT_ADD_MODEL


async def product_add_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    model_id = query.data.replace("prodaddmodel:", "", 1)
    if model_id == "new":
        await query.edit_message_text("Введите название новой модели:", reply_markup=cancel_keyboard())
        return PRODUCT_ADD_MODEL

    data = context.user_data.get("product_add") or {}
    category_id = data.get("category_id")
    model = CATEGORIES.get(category_id, {}).get("models", {}).get(model_id)
    if not model:
        await query.edit_message_text(
            "Модель не найдена. Выберите её заново:",
            reply_markup=product_add_models_keyboard(category_id),
        )
        return PRODUCT_ADD_MODEL_SELECT
    data.update({"model_id": model_id, "model_name": model["name"]})
    await query.edit_message_text(
        "Введите новый цвет / вариант.\n\nЕсли цвета нет, отправьте «-».",
        reply_markup=cancel_keyboard(),
    )
    return PRODUCT_ADD_COLOR


async def product_add_model_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model_name = (update.message.text or "").strip()
    if not model_name:
        await update.message.reply_text("Введите непустое название модели:", reply_markup=navigation_keyboard("category"))
        return PRODUCT_ADD_MODEL

    context.user_data["product_add"]["model_name"] = model_name
    await update.message.reply_text(
        "Введите цвет / вариант.\n\n"
        "Если цвета нет, отправьте «-».",
        reply_markup=navigation_keyboard("model"),
    )
    return PRODUCT_ADD_COLOR


async def product_add_color_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    color = (update.message.text or "").strip()
    if color == "-":
        color = "ONE COLOR"
    if not color:
        await update.message.reply_text(
            "Введите цвет / вариант или отправьте «-», если цвета нет:",
            reply_markup=cancel_keyboard(),
        )
        return PRODUCT_ADD_COLOR

    data = context.user_data.get("product_add") or {}
    data["color"] = color.upper()
    data["sizes"] = []
    data["marking"] = []
    data["consumable_rules"] = []
    await update.message.reply_text(
        "Выберите размеры новой позиции:",
        reply_markup=product_sizes_keyboard(data["sizes"]),
    )
    return PRODUCT_ADD_SIZES


async def product_add_size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data["product_add"]
    size = query.data.replace("prodsize:", "")
    if size == "custom":
        await query.edit_message_text("Введите свой размер:", reply_markup=cancel_keyboard())
        return PRODUCT_ADD_CUSTOM_SIZE
    if size == "done":
        if not data.get("sizes"):
            await query.edit_message_text(
                "Выберите хотя бы один размер.",
                reply_markup=product_sizes_keyboard(data.get("sizes", [])),
            )
            return PRODUCT_ADD_SIZES
        await query.edit_message_text(
            "Товар маркируемый?",
            reply_markup=product_marking_keyboard(),
        )
        return PRODUCT_ADD_MARKED
    selected_sizes = data.setdefault("sizes", [])
    if size in selected_sizes:
        selected_sizes.remove(size)
    else:
        selected_sizes.append(size)
    await query.edit_message_text(
        "Выберите размеры новой позиции:",
        reply_markup=product_sizes_keyboard(selected_sizes),
    )
    return PRODUCT_ADD_SIZES


async def product_add_custom_size_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    size = (update.message.text or "").strip().upper()
    if not size:
        await update.message.reply_text("Введите непустой размер:", reply_markup=cancel_keyboard())
        return PRODUCT_ADD_CUSTOM_SIZE
    if len(f"prodsize:{size}".encode("utf-8")) > 64:
        await update.message.reply_text(
            "Размер слишком длинный. Введите более короткое обозначение:",
            reply_markup=cancel_keyboard(),
        )
        return PRODUCT_ADD_CUSTOM_SIZE
    selected_sizes = context.user_data["product_add"].setdefault("sizes", [])
    if size not in selected_sizes:
        selected_sizes.append(size)
    await update.message.reply_text(
        "Размер добавлен. Выберите остальные размеры или нажмите «Далее»:",
        reply_markup=product_sizes_keyboard(selected_sizes),
    )
    return PRODUCT_ADD_SIZES


async def product_add_marking_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data["product_add"]
    data["is_marked"] = query.data == "prodmark:yes"
    if not data["is_marked"]:
        return await prompt_product_rules(update, context)
    await query.edit_message_text(
        "Введите базовое название Честного Знака без размера. Бот добавит размер к названию автоматически:",
        reply_markup=navigation_keyboard("marking"),
    )
    return PRODUCT_ADD_CHZ_NAME


async def product_add_chz_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Введите непустое базовое название Честного Знака:", reply_markup=navigation_keyboard("marking"))
        return PRODUCT_ADD_CHZ_NAME
    data = context.user_data["product_add"]
    data["chz_base_name"] = name
    data["marking_index"] = 0
    await update.message.reply_text(
        f"Введите GTIN для размера {data['sizes'][0]}:",
        reply_markup=navigation_keyboard("chz_name"),
    )
    return PRODUCT_ADD_GTIN


async def product_add_gtin_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["product_add"]
    index = data["marking_index"]
    size = data["sizes"][index]
    try:
        gtin = normalize_gtin(update.message.text)
    except ValueError as error:
        await update.message.reply_text(f"{error} Введите GTIN для размера {size}:", reply_markup=navigation_keyboard("gtin"))
        return PRODUCT_ADD_GTIN
    if gtin in {item["gtin"] for item in data["marking"]} or get_honest_sign_product(gtin):
        await update.message.reply_text(f"GTIN {gtin} уже есть в справочнике. Введите другой GTIN:", reply_markup=navigation_keyboard("gtin"))
        return PRODUCT_ADD_GTIN
    data["marking"].append(
        {
            "gtin": gtin,
            "size": size,
            "honest_sign_name": f"{data['chz_base_name']} {size}",
        }
    )
    index += 1
    data["marking_index"] = index
    if index < len(data["sizes"]):
        await update.message.reply_text(f"Введите GTIN для размера {data['sizes'][index]}:", reply_markup=navigation_keyboard("gtin"))
        return PRODUCT_ADD_GTIN
    data.pop("marking_index", None)
    return await prompt_product_rules(update, context)


async def product_add_rule_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data["product_add"]
    action = query.data.replace("prodrule:", "")
    if action in {"done", "skip"}:
        if action == "skip":
            data["consumable_rules"] = []
        await query.edit_message_text(product_preview_text(data), reply_markup=product_confirm_keyboard())
        return PRODUCT_ADD_CONFIRM
    item = next(
        (item for item in get_consumable_items(active_only=True) if str(item["item_id"]) == action),
        None,
    )
    if not item:
        await query.edit_message_text("Расходник не найден.", reply_markup=cancel_keyboard())
        return ConversationHandler.END
    data["pending_rule_item"] = item
    await query.edit_message_text(
        f"Введите норму «{item['name']}» на одну единицу товара ({item['unit']}):",
        reply_markup=navigation_keyboard("rules"),
    )
    return PRODUCT_ADD_RULE_QUANTITY


async def product_add_rule_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = float((update.message.text or "").replace(",", "."))
    except ValueError:
        quantity = 0
    if quantity <= 0:
        await update.message.reply_text("Введите норму числом больше нуля:", reply_markup=navigation_keyboard("rules"))
        return PRODUCT_ADD_RULE_QUANTITY
    data = context.user_data["product_add"]
    item = data.pop("pending_rule_item")
    rules = [rule for rule in data["consumable_rules"] if rule["item_id"] != item["item_id"]]
    rules.append(
        {
            "item_id": item["item_id"],
            "item_name": item["name"],
            "unit": item["unit"],
            "quantity": quantity,
        }
    )
    data["consumable_rules"] = rules
    return await prompt_product_rules(update, context)


async def product_add_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("product_add") or {}
    try:
        product = add_custom_product(
            category_name=data["category_name"],
            model_name=data["model_name"],
            color=data["color"],
        )
        if data.get("is_marked"):
            upsert_honest_sign_products(data["marking"])
        else:
            upsert_honest_sign_products(
                [
                    {
                        "gtin": None,
                        "honest_sign_name": product["product_name"],
                        "size": size,
                    }
                    for size in data["sizes"]
                ]
            )
        for rule in data.get("consumable_rules", []):
            set_product_consumable_rule(
                product["product_id"],
                product["product_name"],
                rule["item_id"],
                rule["quantity"],
            )
    except Exception as error:
        await query.edit_message_text(
            f"Не удалось создать товар: {error}",
            reply_markup=build_products_menu_keyboard(),
        )
        context.user_data.pop("product_add", None)
        return ConversationHandler.END
    context.user_data.pop("product_add", None)
    await query.edit_message_text(
        "Товар добавлен ✅\n\n"
        f"Группа: {product['category_name']}\n"
        f"Модель: {product['model_name']}\n"
        f"Цвет / вариант: {product['color']}\n"
        f"Название: {product['product_name']}",
        reply_markup=build_products_menu_keyboard(),
    )
    return ConversationHandler.END


async def product_add_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("prodback:", "", 1)
    data = context.user_data.get("product_add") or {}

    if target == "category":
        await query.edit_message_text("Выберите группу товара или добавьте новую:", reply_markup=product_categories_keyboard())
        return PRODUCT_ADD_CATEGORY
    if target == "model":
        category_id = data.get("category_id")
        if category_id in CATEGORIES:
            await query.edit_message_text(
                "Выберите существующую модель или создайте новую:",
                reply_markup=product_add_models_keyboard(category_id),
            )
            return PRODUCT_ADD_MODEL_SELECT
        await query.edit_message_text("Введите название модели:", reply_markup=navigation_keyboard("category"))
        return PRODUCT_ADD_MODEL
    if target == "color":
        await query.edit_message_text("Введите цвет / вариант. Если цвета нет, отправьте «-».", reply_markup=navigation_keyboard("model"))
        return PRODUCT_ADD_COLOR
    if target == "sizes":
        await query.edit_message_text("Выберите размеры новой позиции:", reply_markup=product_sizes_keyboard(data.get("sizes", [])))
        return PRODUCT_ADD_SIZES
    if target == "marking":
        data["marking"] = []
        data.pop("marking_index", None)
        await query.edit_message_text("Товар маркируемый?", reply_markup=product_marking_keyboard())
        return PRODUCT_ADD_MARKED
    if target == "chz_name":
        data["marking"] = []
        data.pop("marking_index", None)
        await query.edit_message_text("Введите базовое название Честного Знака без размера:", reply_markup=navigation_keyboard("marking"))
        return PRODUCT_ADD_CHZ_NAME
    if target == "gtin":
        marking = data.setdefault("marking", [])
        if marking:
            marking.pop()
        index = len(marking)
        data["marking_index"] = index
        await query.edit_message_text(f"Введите GTIN для размера {data['sizes'][index]}:", reply_markup=navigation_keyboard("gtin" if index else "chz_name"))
        return PRODUCT_ADD_GTIN
    if target == "rules":
        data.pop("pending_rule_item", None)
        return await prompt_product_rules(update, context)

    return await products_cancel(update, context)


async def product_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление товарами доступно только руководителям.")
        return ConversationHandler.END

    reload_product_catalog()
    context.user_data["product_edit"] = {}
    await query.edit_message_text(
        "Выберите группу товара:",
        reply_markup=product_edit_categories_keyboard(),
    )
    return PRODUCT_EDIT_CATEGORY


async def product_edit_show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["product_edit"] = {}
    await query.edit_message_text(
        "Выберите группу товара:",
        reply_markup=product_edit_categories_keyboard(),
    )
    return PRODUCT_EDIT_CATEGORY


async def product_edit_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = query.data.replace("prodeditcat:", "", 1)
    category = CATEGORIES.get(category_id)
    if not category:
        await query.edit_message_text(
            "Группа не найдена. Выберите её заново:",
            reply_markup=product_edit_categories_keyboard(),
        )
        return PRODUCT_EDIT_CATEGORY
    context.user_data["product_edit"] = {"selected_category_id": category_id}
    await query.edit_message_text(
        f"Группа: {category['name']}\n\nВыберите модель:",
        reply_markup=product_edit_models_keyboard(category_id),
    )
    return PRODUCT_EDIT_MODEL


async def product_edit_show_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = (context.user_data.get("product_edit") or {}).get("selected_category_id")
    category = CATEGORIES.get(category_id)
    if not category:
        context.user_data["product_edit"] = {}
        await query.edit_message_text(
            "Группа не найдена. Выберите её заново:",
            reply_markup=product_edit_categories_keyboard(),
        )
        return PRODUCT_EDIT_CATEGORY
    await query.edit_message_text(
        f"Группа: {category['name']}\n\nВыберите модель:",
        reply_markup=product_edit_models_keyboard(category_id),
    )
    return PRODUCT_EDIT_MODEL


async def product_edit_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("product_edit") or {}
    category_id = data.get("selected_category_id")
    model_id = query.data.replace("prodeditmodel:", "", 1)
    model = CATEGORIES.get(category_id, {}).get("models", {}).get(model_id)
    if not model:
        await query.edit_message_text(
            "Модель не найдена. Выберите её заново:",
            reply_markup=product_edit_models_keyboard(category_id),
        )
        return PRODUCT_EDIT_MODEL
    data["selected_model_id"] = model_id
    context.user_data["product_edit"] = data
    await query.edit_message_text(
        f"Модель: {model['name']}\n\nВыберите товар:",
        reply_markup=product_edit_products_keyboard(category_id, model_id),
    )
    return PRODUCT_EDIT_PRODUCT


async def product_edit_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.replace("prodeditproduct:", "", 1)
    product = get_catalog_product(product_id)
    if not product:
        await query.edit_message_text(
            "Товар не найден. Начните выбор заново.",
            reply_markup=product_edit_categories_keyboard(),
        )
        return PRODUCT_EDIT_CATEGORY
    context.user_data["product_edit"] = product
    await query.edit_message_text(
        product_edit_preview_text(product),
        reply_markup=product_edit_fields_keyboard(),
    )
    return PRODUCT_EDIT_FIELD


async def product_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("prodeditfield:", "", 1)
    prompts = {
        "category_name": "Введите новое название группы. Оно изменится у всех товаров этой группы:",
        "model_name": "Введите новое название модели. Оно изменится у всех цветов этой модели:",
        "color": "Введите новый цвет / вариант. Если цвета нет, отправьте «-»:",
    }
    if field not in prompts:
        await query.edit_message_text("Поле не найдено.", reply_markup=product_edit_fields_keyboard())
        return PRODUCT_EDIT_FIELD
    context.user_data["product_edit"]["pending_field"] = field
    await query.edit_message_text(prompts[field], reply_markup=cancel_keyboard())
    return PRODUCT_EDIT_VALUE


async def product_add_variant_from_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    edit_data = context.user_data.get("product_edit") or {}
    product = get_catalog_product(edit_data.get("product_id"))
    if not product:
        await query.edit_message_text(
            "Товар не найден. Вернитесь в раздел товаров и повторите попытку.",
            reply_markup=build_products_menu_keyboard(),
        )
        context.user_data.pop("product_edit", None)
        return ConversationHandler.END
    context.user_data.pop("product_edit", None)
    context.user_data["product_add"] = {
        "category_id": product["category_id"],
        "category_name": product["category_name"],
        "model_id": product["model_id"],
        "model_name": product["model_name"],
    }
    await query.edit_message_text(
        f"Добавление варианта к модели «{product['model_name']}».\n\n"
        "Введите новый цвет / вариант. Если цвета нет, отправьте «-»: ",
        reply_markup=cancel_keyboard(),
    )
    return PRODUCT_ADD_COLOR


async def product_add_model_from_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    edit_data = context.user_data.get("product_edit") or {}
    product = get_catalog_product(edit_data.get("product_id"))
    if not product:
        await query.edit_message_text(
            "Товар не найден. Вернитесь в раздел товаров и повторите попытку.",
            reply_markup=build_products_menu_keyboard(),
        )
        context.user_data.pop("product_edit", None)
        return ConversationHandler.END
    context.user_data.pop("product_edit", None)
    context.user_data["product_add"] = {
        "category_id": product["category_id"],
        "category_name": product["category_name"],
    }
    await query.edit_message_text(
        f"Добавление модели в группу «{product['category_name']}».\n\nВведите название новой модели:",
        reply_markup=cancel_keyboard(),
    )
    return PRODUCT_ADD_MODEL


async def product_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("product_edit") or {}
    field = data.pop("pending_field", None)
    value = (update.message.text or "").strip()
    if field == "color" and value == "-":
        value = "ONE COLOR"
    if not field or not value:
        await update.message.reply_text("Значение не должно быть пустым. Введите его ещё раз:", reply_markup=cancel_keyboard())
        return PRODUCT_EDIT_VALUE
    data[field] = value.upper() if field == "color" else value
    await update.message.reply_text(
        product_edit_preview_text(data),
        reply_markup=product_edit_fields_keyboard(),
    )
    return PRODUCT_EDIT_FIELD


async def product_edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("product_edit") or {}
    try:
        product = update_catalog_product(
            product_id=data["product_id"],
            category_name=data["category_name"],
            model_name=data["model_name"],
            color=data["color"],
        )
    except Exception as error:
        await query.edit_message_text(
            f"Не удалось сохранить товар: {error}\n\n{product_edit_preview_text(data)}",
            reply_markup=product_edit_fields_keyboard(),
        )
        return PRODUCT_EDIT_FIELD

    warning = ""
    try:
        for updated_product in product.get("updated_products", [product]):
            update_product_consumable_rules_name(
                updated_product["product_id"],
                updated_product["product_name"],
            )
    except Exception:
        warning = "\n\n⚠️ Названия в нормах расходников обновить не удалось."

    context.user_data.pop("product_edit", None)
    await query.edit_message_text(
        "Товар изменён ✅\n\n"
        f"Группа: {product['category_name']}\n"
        f"Модель: {product['model_name']}\n"
        f"Цвет / вариант: {product['color']}\n"
        f"Название: {product['product_name']}"
        f"{warning}",
        reply_markup=build_products_menu_keyboard(),
    )
    return ConversationHandler.END


async def products_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("product_add", None)
    context.user_data.pop("product_edit", None)
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Действие отменено.", reply_markup=build_products_menu_keyboard())
    elif update.message:
        await update.message.reply_text("Действие отменено.", reply_markup=build_products_menu_keyboard())
    return ConversationHandler.END


def get_product_handlers():
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(product_add_start, pattern=r"^prodadmin:add$"),
            CallbackQueryHandler(product_edit_start, pattern=r"^prodadmin:edit$"),
        ],
        states={
            PRODUCT_ADD_CATEGORY: [
                CallbackQueryHandler(product_add_category_selected, pattern=r"^prodcat:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_category_received),
            ],
            PRODUCT_ADD_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_model_received)],
            PRODUCT_ADD_MODEL_SELECT: [
                CallbackQueryHandler(product_add_model_selected, pattern=r"^prodaddmodel:"),
            ],
            PRODUCT_ADD_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_color_received)],
            PRODUCT_ADD_SIZES: [CallbackQueryHandler(product_add_size_selected, pattern=r"^prodsize:")],
            PRODUCT_ADD_CUSTOM_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_custom_size_received)
            ],
            PRODUCT_ADD_MARKED: [CallbackQueryHandler(product_add_marking_selected, pattern=r"^prodmark:(yes|no)$")],
            PRODUCT_ADD_CHZ_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_chz_name_received)],
            PRODUCT_ADD_GTIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_gtin_received)],
            PRODUCT_ADD_RULE_SELECT: [CallbackQueryHandler(product_add_rule_selected, pattern=r"^prodrule:")],
            PRODUCT_ADD_RULE_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_rule_quantity_received)],
            PRODUCT_ADD_CONFIRM: [CallbackQueryHandler(product_add_confirmed, pattern=r"^prodconfirm:yes$")],
            PRODUCT_EDIT_CATEGORY: [
                CallbackQueryHandler(product_edit_category_selected, pattern=r"^prodeditcat:"),
            ],
            PRODUCT_EDIT_MODEL: [
                CallbackQueryHandler(product_edit_model_selected, pattern=r"^prodeditmodel:"),
                CallbackQueryHandler(product_edit_show_categories, pattern=r"^prodedit:categories$"),
            ],
            PRODUCT_EDIT_PRODUCT: [
                CallbackQueryHandler(product_edit_product_selected, pattern=r"^prodeditproduct:"),
                CallbackQueryHandler(product_edit_show_models, pattern=r"^prodedit:models$"),
            ],
            PRODUCT_EDIT_FIELD: [
                CallbackQueryHandler(product_edit_field_selected, pattern=r"^prodeditfield:"),
                CallbackQueryHandler(product_add_variant_from_edit, pattern=r"^prodedit:add_variant$"),
                CallbackQueryHandler(product_add_model_from_edit, pattern=r"^prodedit:add_model$"),
                CallbackQueryHandler(product_edit_save, pattern=r"^prodedit:save$"),
            ],
            PRODUCT_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, product_edit_value_received)],
        },
        fallbacks=[
            CallbackQueryHandler(product_add_back, pattern=r"^prodback:(category|model|color|sizes|marking|chz_name|gtin|rules)$"),
            CallbackQueryHandler(products_cancel, pattern=r"^prodadmin:cancel$"),
        ],
        name="products_management",
        persistent=False,
    )
    return [conversation]
