from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from modules.receiving.postgres_storage import save_incoming_good
from core.keyboards import (
    build_category_keyboard,
    build_incoming_date_keyboard,
    build_models_keyboard,
    build_product_colors_keyboard,
    build_sizes_keyboard,
)
from modules.receiving.products import CATEGORIES, SIZES
from handlers.common import show_main_menu, show_receiving_menu
from modules.consumables.storage import apply_receiving_consumable_usage, format_quantity


SELECT_DATE, SELECT_CATEGORY, SELECT_MODEL, SELECT_COLOR, SELECT_SIZE, ENTER_PACKED, ENTER_DEFECTIVE, ENTER_REWORK = range(8)


def build_incoming_category_keyboard():
    return build_category_keyboard(
        back_callback="back:dates",
        back_text="⬅️ Назад к датам",
    )


def build_incoming_models_keyboard(category_id):
    return build_models_keyboard(
        category_id,
        home_callback="back:dates",
        home_text="⬅️ Назад к датам",
    )


def build_incoming_product_colors_keyboard(category_id, model_id):
    return build_product_colors_keyboard(
        category_id,
        model_id,
        home_callback="back:categories",
        home_text="⬅️ Назад к группам",
    )


def build_incoming_sizes_keyboard():
    return build_sizes_keyboard(
        home_callback="back:models",
        home_text="⬅️ Назад к моделям",
    )


def parse_non_negative_number(text):
    text = text.strip()

    if not text.isdigit():
        return None

    value = int(text)

    if value < 0:
        return None

    return value


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "Выберите дату оприходования:",
        reply_markup=build_incoming_date_keyboard(),
    )

    return SELECT_DATE


async def menu_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "Выберите дату оприходования:",
        reply_markup=build_incoming_date_keyboard(),
    )

    return SELECT_DATE


async def incoming_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    record_date = query.data.replace("incdate:", "")
    context.user_data["record_date"] = record_date

    await query.edit_message_text(
        f"Дата оприходования: {record_date}\n\n"
        "Выберите группу товара:",
        reply_markup=build_incoming_category_keyboard(),
    )

    return SELECT_CATEGORY


async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = query.data.replace("cat:", "")

    if category_id not in CATEGORIES:
        await query.edit_message_text("Такой группы нет. Начните заново: /start")
        return ConversationHandler.END

    context.user_data["category_id"] = category_id

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        f"Группа: {CATEGORIES[category_id]['name']}\n\n"
        "Выберите модель:",
        reply_markup=build_incoming_models_keyboard(category_id),
    )

    return SELECT_MODEL


async def model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    model_id = query.data.replace("model:", "")
    category_id = context.user_data.get("category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text("Не выбрана группа. Начните заново: /start")
        return ConversationHandler.END

    models = CATEGORIES[category_id]["models"]

    if model_id not in models:
        await query.edit_message_text("Такой модели нет. Начните заново: /start")
        return ConversationHandler.END

    context.user_data["model_id"] = model_id

    variants = models[model_id]["variants"]

    if len(variants) == 1:
        variant_data = list(variants.values())[0]
        product_id = variant_data["id"]

        context.user_data["product_id"] = product_id

        await query.edit_message_text(
            f"Дата: {context.user_data.get('record_date', '-')}\n"
            f"Товар: {variant_data['name']}\n\n"
            "Выберите размер:",
            reply_markup=build_incoming_sizes_keyboard(),
        )

        return SELECT_SIZE

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        f"Модель: {models[model_id]['name']}\n\n"
        "Выберите цвет / вариант:",
        reply_markup=build_incoming_product_colors_keyboard(category_id, model_id),
    )

    return SELECT_COLOR


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.replace("prod:", "")
    category_id = context.user_data.get("category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text("Не выбрана группа. Начните заново: /start")
        return ConversationHandler.END

    products = CATEGORIES[category_id]["products"]

    if product_id not in products:
        await query.edit_message_text("Такого товара нет. Начните заново: /start")
        return ConversationHandler.END

    context.user_data["product_id"] = product_id

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        f"Товар: {products[product_id]}\n\n"
        "Выберите размер:",
        reply_markup=build_incoming_sizes_keyboard(),
    )

    return SELECT_SIZE


async def size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    size = query.data.replace("size:", "")

    if size not in SIZES:
        await query.edit_message_text("Такого размера нет. Начните заново: /start")
        return ConversationHandler.END

    context.user_data["size"] = size

    category_id = context.user_data["category_id"]
    product_id = context.user_data["product_id"]
    product_name = CATEGORIES[category_id]["products"][product_id]

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        f"Товар: {product_name}\n"
        f"Размер: {size}\n\n"
        "Введите количество в статусе «Упаковано».\n"
        "Если нет — введите 0."
    )

    return ENTER_PACKED


async def packed_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)

    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return ENTER_PACKED

    context.user_data["packed"] = value

    await update.message.reply_text(
        "Введите количество в статусе «Брак».\n"
        "Если нет — введите 0."
    )

    return ENTER_DEFECTIVE


async def defective_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)

    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return ENTER_DEFECTIVE

    context.user_data["defective"] = value

    await update.message.reply_text(
        "Введите количество в статусе «Доработка».\n"
        "Если нет — введите 0."
    )

    return ENTER_REWORK


async def rework_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)

    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return ENTER_REWORK

    context.user_data["rework"] = value

    record_date = context.user_data.get("record_date")
    category_id = context.user_data.get("category_id")
    product_id = context.user_data.get("product_id")
    size = context.user_data.get("size")
    packed = context.user_data.get("packed", 0)
    defective = context.user_data.get("defective", 0)
    rework = context.user_data.get("rework", 0)

    if not record_date or not category_id or not product_id or not size:
        await update.message.reply_text("Данные потерялись. Нажмите /start и начните заново.")
        context.user_data.clear()
        return ConversationHandler.END

    user = update.effective_user
    username = user.username or user.full_name

    record_id = save_incoming_good(
        user_id=user.id,
        username=username,
        category_id=category_id,
        product_id=product_id,
        size=size,
        packed=packed,
        defective=defective,
        rework=rework,
        record_date=record_date,
    )

    category_name = CATEGORIES[category_id]["name"]
    product_name = CATEGORIES[category_id]["products"][product_id]
    consumable_movements = apply_receiving_consumable_usage(
        product_id=product_id,
        product_name=product_name,
        packed_quantity=packed,
        source_id=record_id,
        created_by_user_id=user.id,
        created_by_name=username,
    )
    consumables_text = format_consumable_usage_text(consumable_movements)

    await update.message.reply_text(
        "Товар оприходован ✅\n\n"
        f"Дата: {record_date}\n"
        f"Группа: {category_name}\n"
        f"Товар: {product_name}\n"
        f"Размер: {size}\n"
        f"Упаковано: {packed}\n"
        f"Брак: {defective}\n"
        f"Доработка: {rework}\n\n"
        "PostgreSQL: запись добавлена ✅\n\n"
        f"{consumables_text}\n\n"
        "Выберите размер для следующей записи:",
        reply_markup=build_incoming_sizes_keyboard(),
    )

    context.user_data.pop("size", None)
    context.user_data.pop("packed", None)
    context.user_data.pop("defective", None)
    context.user_data.pop("rework", None)
    return SELECT_SIZE


def format_consumable_usage_text(movements):
    if not movements:
        return "Расходники: нормы для товара не настроены"

    lines = ["Расходники списаны:"]
    for movement in movements:
        delta = abs(float(movement.get("quantity_delta") or 0))
        lines.append(f"- {movement.get('item_name')}: {format_quantity(delta)}")
    return "\n".join(lines)


async def back_to_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "Выберите дату оприходования:",
        reply_markup=build_incoming_date_keyboard(),
    )

    return SELECT_DATE


async def back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    record_date = context.user_data.get("record_date")

    context.user_data.clear()

    if record_date:
        context.user_data["record_date"] = record_date

    await query.edit_message_text(
        f"Дата: {record_date or '-'}\n\n"
        "Выберите группу товара:",
        reply_markup=build_incoming_category_keyboard(),
    )

    return SELECT_CATEGORY


async def back_to_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = context.user_data.get("category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text("Группа не найдена. Нажмите /start и начните заново.")
        return ConversationHandler.END

    context.user_data.pop("model_id", None)
    context.user_data.pop("product_id", None)
    context.user_data.pop("size", None)

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        f"Группа: {CATEGORIES[category_id]['name']}\n\n"
        "Выберите модель:",
        reply_markup=build_incoming_models_keyboard(category_id),
    )

    return SELECT_MODEL


async def back_to_colors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = context.user_data.get("category_id")
    model_id = context.user_data.get("model_id")

    if not category_id or not model_id:
        await query.edit_message_text("Данные потерялись. Нажмите /start и начните заново.")
        return ConversationHandler.END

    context.user_data.pop("product_id", None)
    context.user_data.pop("size", None)

    variants = CATEGORIES[category_id]["models"][model_id]["variants"]

    if len(variants) == 1:
        context.user_data.pop("model_id", None)

        await query.edit_message_text(
            f"Дата: {context.user_data.get('record_date', '-')}\n"
            f"Группа: {CATEGORIES[category_id]['name']}\n\n"
            "Выберите модель:",
            reply_markup=build_incoming_models_keyboard(category_id),
        )

        return SELECT_MODEL

    await query.edit_message_text(
        f"Дата: {context.user_data.get('record_date', '-')}\n"
        "Выберите цвет / вариант:",
        reply_markup=build_incoming_product_colors_keyboard(category_id, model_id),
    )

    return SELECT_COLOR


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Ввод отменён. Нажмите /start, чтобы открыть меню.")
    return ConversationHandler.END


def get_incoming_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(menu_add_start, pattern=r"^menu:add$"),
        ],
        states={
            SELECT_DATE: [
                CallbackQueryHandler(incoming_date_selected, pattern=r"^incdate:"),
                CallbackQueryHandler(show_receiving_menu, pattern=r"^section:receiving$"),
                CallbackQueryHandler(show_main_menu, pattern=r"^menu:start$"),
            ],
            SELECT_CATEGORY: [
                CallbackQueryHandler(category_selected, pattern=r"^cat:"),
                CallbackQueryHandler(back_to_dates, pattern=r"^back:dates$"),
            ],
            SELECT_MODEL: [
                CallbackQueryHandler(model_selected, pattern=r"^model:"),
                CallbackQueryHandler(back_to_categories, pattern=r"^back:categories$"),
            ],
            SELECT_COLOR: [
                CallbackQueryHandler(product_selected, pattern=r"^prod:"),
                CallbackQueryHandler(back_to_models, pattern=r"^back:models$"),
            ],
            SELECT_SIZE: [
                CallbackQueryHandler(size_selected, pattern=r"^size:"),
                CallbackQueryHandler(back_to_colors, pattern=r"^back:colors$"),
            ],
            ENTER_PACKED: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, packed_received),
            ],
            ENTER_DEFECTIVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, defective_received),
            ],
            ENTER_REWORK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rework_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
