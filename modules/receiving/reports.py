import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from config import GROUP_CHAT_ID, RECEIVING_REPORT_TOPIC_ID, SPECIAL_RECEIVING_REPORT_TOPIC_ID
from modules.receiving.postgres_storage import (
    build_receiving_report_text,
    create_special_receiving_report,
    delete_special_receiving_report,
    delete_unexported_receiving_record,
    get_exported_receiving_report_by_export_id,
    get_exported_receiving_report_groups,
    get_receiving_record_by_row,
    get_special_receiving_report,
    get_special_receiving_reports,
    get_unexported_receiving_records,
    has_unexported_receiving_records_for_date,
    mark_receiving_rows_exported,
    update_special_receiving_report,
    unmark_receiving_rows_by_export_id,
)
from core.keyboards import (
    build_category_keyboard,
    build_models_keyboard,
    build_product_colors_keyboard,
    build_receiving_menu_keyboard,
    build_receiving_report_type_keyboard,
    build_report_date_keyboard,
    build_sizes_keyboard,
)
from modules.receiving.handlers import parse_non_negative_number
from modules.receiving.products import CATEGORIES, SIZES


# Локальный fallback-справочник ФИО.
# Нужен, чтобы отчеты читались нормально даже если Google-таблица ЗП
# временно недоступна или лист «Сотрудники» еще не синхронизирован.
LOCAL_EMPLOYEE_NAMES_BY_USER_ID = {
    "413489632": "Андрей Козырь",
    "927075259": "Дмитрий Тарасов",
    "1152528155": "Константин Рогов",
    "597723397": "Егор Репин",
    "272117327": "Никита Комаричев",
    "854197803": "Лев Грунверг",
    "5223200693": "Файсал Сабер",
}

LOCAL_EMPLOYEE_NAMES_BY_USERNAME = {
    "opulent_shooter": "Андрей Козырь",
    "adafagahajakal": "Дмитрий Тарасов",
    "kstyaaaa": "Константин Рогов",
    "whereareyo0o": "Егор Репин",
    "rokiothegoat": "Никита Комаричев",
    "fadexdf": "Лев Грунверг",
    "hamza_sam": "Файсал Сабер",
}


(
    SPECIAL_STORE,
    SPECIAL_DATE,
    SPECIAL_CATEGORY,
    SPECIAL_MODEL,
    SPECIAL_COLOR,
    SPECIAL_SIZE,
    SPECIAL_QUANTITY,
    SPECIAL_PACKED,
    SPECIAL_DEFECTIVE,
    SPECIAL_REWORK,
    SPECIAL_EDIT_VALUE,
    SPECIAL_EDIT_ITEM,
) = range(12)

SPECIAL_REPORT_TYPES = {
    "illiquid": "Неликвид",
    "rejected": "Отбракованный товар",
}


def normalize_username_local(username):
    return str(username or "").strip().lstrip("@").lower()


def employee_name_from_user_id_or_username(user_id=None, username=None, fallback=None):
    user_id = str(user_id or "").strip()
    username = normalize_username_local(username)

    if user_id and user_id in LOCAL_EMPLOYEE_NAMES_BY_USER_ID:
        return LOCAL_EMPLOYEE_NAMES_BY_USER_ID[user_id]

    if username and username in LOCAL_EMPLOYEE_NAMES_BY_USERNAME:
        return LOCAL_EMPLOYEE_NAMES_BY_USERNAME[username]

    return fallback or username or user_id or "Неизвестный сотрудник"


# ============================================================
# ФИО СОТРУДНИКА
# ============================================================

def get_employee_full_name_for_user(user):
    """Возвращает ФИО сотрудника из модуля ЗП по Telegram user_id/username.

    Порядок поиска:
    1. Локальный справочник по user_id/username.
    2. Справочник ЗП «Сотрудники».
    3. payroll_config.py.
    4. Telegram full_name / username.
    """
    local_name = employee_name_from_user_id_or_username(
        user_id=user.id,
        username=user.username,
        fallback=None,
    )
    if local_name and local_name != "Неизвестный сотрудник":
        return local_name

    try:
        from modules.payroll.google_sheets import find_employee_for_telegram_user

        employee = find_employee_for_telegram_user(user)
        if employee and employee.get("full_name"):
            return employee["full_name"]
    except Exception:
        logging.exception("Не удалось получить ФИО сотрудника из справочника ЗП")

    try:
        from modules.payroll.config import PAYROLL_EMPLOYEES, normalize_username

        telegram_user_id = str(user.id)
        username = normalize_username(user.username)

        for employee in PAYROLL_EMPLOYEES:
            if str(employee.get("telegram_user_id", "")).strip() == telegram_user_id:
                return employee.get("full_name") or user.full_name

        if username:
            for employee in PAYROLL_EMPLOYEES:
                if normalize_username(employee.get("telegram_username", "")) == username:
                    return employee.get("full_name") or user.full_name
    except Exception:
        logging.exception("Не удалось получить ФИО сотрудника из payroll_config.py")

    return user.full_name or user.username or str(user.id)


def user_display_name(user):
    return get_employee_full_name_for_user(user)


# ============================================================
# ВЫГРУЗКА ОТЧЕТА ОПРИХОДОВАНИЙ
# ============================================================

async def report_choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "Выберите дату для выгрузки отчета:",
        reply_markup=build_report_date_keyboard(),
    )


def split_long_message(text, limit=3900):
    chunks = []
    current = ""

    for line in text.splitlines():
        candidate = current + ("\n" if current else "") + line

        if len(candidate) > limit:
            if current:
                chunks.append(current)
                current = line
            else:
                chunks.append(line[:limit])
                current = line[limit:]
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def make_export_id(report_date):
    date_part = report_date.replace(".", "")
    time_part = datetime.now().strftime("%H%M%S")
    return f"recv{date_part}{time_part}"


async def send_report_to_topic(context: ContextTypes.DEFAULT_TYPE, report_text):
    if not GROUP_CHAT_ID:
        raise RuntimeError("Тема отчета не настроена: GROUP_CHAT_ID пустой.")

    if not RECEIVING_REPORT_TOPIC_ID:
        raise RuntimeError("Тема отчета не настроена: RECEIVING_REPORT_TOPIC_ID пустой.")

    chat_id = int(GROUP_CHAT_ID)
    message_thread_id = int(RECEIVING_REPORT_TOPIC_ID)

    chunks = split_long_message(report_text)
    message_ids = []

    for chunk in chunks:
        message = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=chunk,
        )
        message_ids.append(message.message_id)

    return {
        "chat_id": chat_id,
        "thread_id": message_thread_id,
        "message_ids": message_ids,
        "status": "Отчет отправлен в тему «Отчет приемки» ✅",
    }


def special_report_title(report_type):
    return SPECIAL_REPORT_TYPES.get(report_type, report_type)


def special_items_from_user_data(context):
    return context.user_data.setdefault("special_items", [])


def build_special_category_keyboard():
    return build_category_keyboard(
        back_callback="specrep:items_menu",
        back_text="⬅️ Назад к списку",
    )


def build_special_models_keyboard(category_id):
    return build_models_keyboard(
        category_id,
        home_callback="specrep:items_menu",
        home_text="⬅️ Назад к списку",
    )


def build_special_product_colors_keyboard(category_id, model_id):
    return build_product_colors_keyboard(
        category_id,
        model_id,
        home_callback="back:categories",
        home_text="⬅️ Назад к группам",
    )


def build_special_sizes_keyboard():
    return build_sizes_keyboard(
        home_callback="back:models",
        home_text="⬅️ Назад к моделям",
    )


def item_total(item):
    if any(key in item for key in ("packed", "defective", "rework")):
        return (
            int(item.get("packed", 0) or 0)
            + int(item.get("defective", 0) or 0)
            + int(item.get("rework", 0) or 0)
        )

    return int(item.get("quantity", 0) or 0)


def format_special_item(item):
    category_id = item["category_id"]
    product_id = item["product_id"]
    product_name = CATEGORIES[category_id]["products"][product_id]
    total = item_total(item)

    if any(key in item for key in ("packed", "defective", "rework")):
        return (
            f"{product_name} | {item['size']} | "
            f"норм: {int(item.get('packed', 0) or 0)}, "
            f"брак: {int(item.get('defective', 0) or 0)}, "
            f"доработка: {int(item.get('rework', 0) or 0)}, "
            f"общее: {total}"
        )

    return f"{product_name} | {item['size']} | {total} шт."


def format_special_items(items):
    if not items:
        return "Товары не выбраны."

    lines = []
    for index, item in enumerate(items, start=1):
        if "product_name" in item:
            total = item_total(item)
            if any(key in item for key in ("packed", "defective", "rework")):
                lines.append(
                    f"{index}. {item['product_name']} | {item['size']} | "
                    f"норм: {int(item.get('packed', 0) or 0)}, "
                    f"брак: {int(item.get('defective', 0) or 0)}, "
                    f"доработка: {int(item.get('rework', 0) or 0)}, "
                    f"общее: {total}"
                )
            else:
                lines.append(f"{index}. {item['product_name']} | {item['size']} | {total} шт.")
        else:
            lines.append(f"{index}. {format_special_item(item)}")

    return "\n".join(lines)


def build_special_action_keyboard(report_type):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Сформировать отчет", callback_data=f"specrep:create:{report_type}")],
            [InlineKeyboardButton("🗑 Удалить отчет", callback_data=f"specrep:delete_choose:{report_type}")],
            [InlineKeyboardButton("✏️ Редактировать отчет", callback_data=f"specrep:edit_choose:{report_type}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")],
        ]
    )


def build_special_items_menu_keyboard(context):
    items = context.user_data.get("special_items") or []
    keyboard = []

    if context.user_data.get("special_product_id"):
        keyboard.append([InlineKeyboardButton("➕ Добавить размер этой модели", callback_data="specrep:add_same_product")])

    keyboard.append([InlineKeyboardButton("➕ Добавить другой товар", callback_data="specrep:add_item")])

    if items:
        keyboard.append([InlineKeyboardButton("✏️ Изменить строку", callback_data="specrep:edit_item_choose")])
        keyboard.append([InlineKeyboardButton("🗑 Удалить строку", callback_data="specrep:delete_item_choose")])
        keyboard.append([InlineKeyboardButton("✅ Завершить", callback_data="specrep:finish")])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")])
    return InlineKeyboardMarkup(keyboard)


def build_special_item_select_keyboard(action, items):
    keyboard = []

    for index, item in enumerate(items):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{index + 1}. {shorten_text(format_special_item(item), 45)}",
                    callback_data=f"specrep:{action}:{index}",
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад к списку", callback_data="specrep:items_menu")])
    return InlineKeyboardMarkup(keyboard)


def special_report_button_text(report):
    date_part = report.get("removal_date") or report.get("created_at", "").split(" ")[0]
    store_part = report.get("store_from") or report.get("created_by") or "-"
    total = sum(item_total(item) for item in report.get("items", []))
    return f"#{report['id']} | {date_part} | {shorten_text(store_part, 18)} | {total} шт."


def build_special_reports_keyboard(action, report_type, reports):
    keyboard = []

    for report in reports:
        keyboard.append(
            [
                InlineKeyboardButton(
                    special_report_button_text(report),
                    callback_data=f"specrep:{action}:{report['id']}",
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"recvtype:{report_type}")])
    return InlineKeyboardMarkup(keyboard)


def build_special_delete_confirm_keyboard(report_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Удалить", callback_data=f"specrep:delete_do:{report_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="section:receiving"),
            ]
        ]
    )


def build_special_edit_params_keyboard(report):
    report_type = report["report_type"]
    keyboard = []

    if report_type == "illiquid":
        keyboard.append([InlineKeyboardButton("🏬 Магазин", callback_data="specrep:edit_param:store")])
        keyboard.append([InlineKeyboardButton("📅 Дата вывоза", callback_data="specrep:edit_param:date")])

    keyboard.append([InlineKeyboardButton("📦 Товары", callback_data="specrep:edit_param:items")])
    keyboard.append([InlineKeyboardButton("✅ Завершить редактирование", callback_data="specrep:edit_finish")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"recvtype:{report_type}")])
    return InlineKeyboardMarkup(keyboard)


def format_special_report_text(report_type, store_from, removal_date, items, created_by=None):
    lines = [special_report_title(report_type)]

    if report_type == "illiquid":
        lines.append(f"Магазин откуда вывезли: {store_from or '-'}")
        lines.append(f"Дата вывоза: {removal_date or '-'}")

    if created_by:
        lines.append(f"Сформировал: {created_by}")

    lines.append("")
    lines.append("Товары:")

    grouped = {}
    total_packed = 0
    total_defective = 0
    total_rework = 0
    total = 0
    for item in items:
        product_name = item.get("product_name")
        if not product_name:
            product_name = CATEGORIES[item["category_id"]]["products"][item["product_id"]]
        grouped.setdefault(product_name, {})
        grouped[product_name].setdefault(item["size"], {"packed": 0, "defective": 0, "rework": 0, "quantity": 0})

        if report_type == "illiquid":
            packed = int(item.get("packed", item.get("quantity", 0)) or 0)
            defective = int(item.get("defective", 0) or 0)
            rework = int(item.get("rework", 0) or 0)
            quantity = packed + defective + rework
        else:
            quantity = item_total(item)
            packed = quantity
            defective = 0
            rework = 0

        grouped[product_name][item["size"]]["packed"] += packed
        grouped[product_name][item["size"]]["defective"] += defective
        grouped[product_name][item["size"]]["rework"] += rework
        grouped[product_name][item["size"]]["quantity"] += quantity
        total_packed += packed
        total_defective += defective
        total_rework += rework
        total += quantity

    for product_name in sorted(grouped.keys()):
        lines.append(product_name)
        for size in sorted(grouped[product_name].keys()):
            values = grouped[product_name][size]
            if report_type == "illiquid":
                lines.append(
                    f"{size}: норм - {values['packed']}, "
                    f"брак - {values['defective']}, "
                    f"доработка - {values['rework']}, "
                    f"общее - {values['quantity']}"
                )
            else:
                lines.append(f"{size}: {values['quantity']} шт.")
        lines.append("")

    if report_type == "illiquid":
        lines.extend(
            [
                f"Общее норм: {total_packed}",
                f"Общее брак: {total_defective}",
                f"Общее доработка: {total_rework}",
            ]
        )

    lines.append(f"Общее количество: {total}")
    return "\n".join(lines).strip()


async def send_special_report_to_topic(context: ContextTypes.DEFAULT_TYPE, report_text):
    if not GROUP_CHAT_ID:
        raise RuntimeError("Тема отчета не настроена: GROUP_CHAT_ID пустой.")

    if not SPECIAL_RECEIVING_REPORT_TOPIC_ID:
        raise RuntimeError("Тема отчета не настроена: SPECIAL_RECEIVING_REPORT_TOPIC_ID пустой.")

    chat_id = int(GROUP_CHAT_ID)
    message_thread_id = int(SPECIAL_RECEIVING_REPORT_TOPIC_ID)
    chunks = split_long_message(report_text)
    message_ids = []

    for chunk in chunks:
        message = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=chunk,
        )
        message_ids.append(message.message_id)

    return {
        "chat_id": chat_id,
        "thread_id": message_thread_id,
        "message_ids": message_ids,
    }


async def delete_special_report_messages(context, report):
    deleted = 0
    errors = []
    chat_id = report.get("chat_id")

    if not chat_id:
        return deleted, errors

    for message_id in report.get("message_ids", []):
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
            deleted += 1
        except Exception as error:
            logging.exception("Не удалось удалить сообщение специального отчета")
            errors.append(f"message_id {message_id}: {error}")

    return deleted, errors


async def special_report_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    report_type = query.data.replace("recvtype:", "")

    if report_type == "new_supply":
        await query.edit_message_text(
            "📦 Новая поставка:",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    await query.edit_message_text(
        f"{special_report_title(report_type)}:",
        reply_markup=build_special_action_keyboard(report_type),
    )


async def special_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()
    report_type = query.data.replace("specrep:create:", "")
    context.user_data["special_report_type"] = report_type
    context.user_data["special_mode"] = "create"
    context.user_data["special_items"] = []

    if report_type == "illiquid":
        await query.edit_message_text("Введите магазин, откуда вывезли неликвид:")
        return SPECIAL_STORE

    await query.edit_message_text(
        "Выберите группу товара:",
        reply_markup=build_special_category_keyboard(),
    )
    return SPECIAL_CATEGORY


async def special_store_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["special_store_from"] = update.message.text.strip()

    await update.message.reply_text(
        "Введите дату вывоза в формате ДД.ММ.ГГГГ:"
    )
    return SPECIAL_DATE


async def special_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()

    try:
        datetime.strptime(value, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text("Введите дату в формате ДД.ММ.ГГГГ:")
        return SPECIAL_DATE

    context.user_data["special_removal_date"] = value

    if context.user_data.get("special_mode") == "edit":
        report = {
            "report_type": context.user_data["special_report_type"],
        }
        await update.message.reply_text(
            "Дата обновлена. Можно изменить еще что-то или завершить редактирование.",
            reply_markup=build_special_edit_params_keyboard(report),
        )
        return SPECIAL_EDIT_VALUE

    await update.message.reply_text(
        "Выберите группу товара:",
        reply_markup=build_special_category_keyboard(),
    )
    return SPECIAL_CATEGORY


async def special_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = query.data.replace("cat:", "")
    if category_id not in CATEGORIES:
        await query.edit_message_text("Такой группы нет. Начните заново.")
        return ConversationHandler.END

    context.user_data["special_category_id"] = category_id
    await query.edit_message_text(
        f"Группа: {CATEGORIES[category_id]['name']}\n\nВыберите модель:",
        reply_markup=build_special_models_keyboard(category_id),
    )
    return SPECIAL_MODEL


async def special_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    model_id = query.data.replace("model:", "")
    category_id = context.user_data.get("special_category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text("Группа не выбрана. Начните заново.")
        return ConversationHandler.END

    models = CATEGORIES[category_id]["models"]
    if model_id not in models:
        await query.edit_message_text("Такой модели нет. Начните заново.")
        return ConversationHandler.END

    context.user_data["special_model_id"] = model_id
    variants = models[model_id]["variants"]

    if len(variants) == 1:
        variant_data = list(variants.values())[0]
        context.user_data["special_product_id"] = variant_data["id"]
        await query.edit_message_text(
            f"Товар: {variant_data['name']}\n\nВыберите размер:",
            reply_markup=build_special_sizes_keyboard(),
        )
        return SPECIAL_SIZE

    await query.edit_message_text(
        f"Модель: {models[model_id]['name']}\n\nВыберите цвет / вариант:",
        reply_markup=build_special_product_colors_keyboard(category_id, model_id),
    )
    return SPECIAL_COLOR


async def special_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.replace("prod:", "")
    category_id = context.user_data.get("special_category_id")

    if not category_id or product_id not in CATEGORIES[category_id]["products"]:
        await query.edit_message_text("Такого товара нет. Начните заново.")
        return ConversationHandler.END

    context.user_data["special_product_id"] = product_id
    await query.edit_message_text(
        f"Товар: {CATEGORIES[category_id]['products'][product_id]}\n\nВыберите размер:",
        reply_markup=build_special_sizes_keyboard(),
    )
    return SPECIAL_SIZE


async def special_size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    size = query.data.replace("size:", "")
    if size not in SIZES:
        await query.edit_message_text("Такого размера нет. Начните заново.")
        return ConversationHandler.END

    context.user_data["special_size"] = size

    if context.user_data.get("special_report_type") == "illiquid":
        await query.edit_message_text(
            "Введите количество в статусе «Норм».\n"
            "Если нет — введите 0."
        )
        return SPECIAL_PACKED

    await query.edit_message_text("Введите количество:")
    return SPECIAL_QUANTITY


async def special_packed_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)
    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return SPECIAL_PACKED

    context.user_data["special_packed"] = value
    await update.message.reply_text(
        "Введите количество в статусе «Брак».\n"
        "Если нет — введите 0."
    )
    return SPECIAL_DEFECTIVE


async def special_defective_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)
    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return SPECIAL_DEFECTIVE

    context.user_data["special_defective"] = value
    await update.message.reply_text(
        "Введите количество в статусе «Доработка».\n"
        "Если нет — введите 0."
    )
    return SPECIAL_REWORK


async def special_rework_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_non_negative_number(update.message.text)
    if value is None:
        await update.message.reply_text("Введите целое число от 0 и выше:")
        return SPECIAL_REWORK

    context.user_data["special_rework"] = value
    return await save_special_item_from_context(update, context)


async def special_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = parse_non_negative_number(update.message.text)
    if quantity is None or quantity <= 0:
        await update.message.reply_text("Введите целое число больше 0:")
        return SPECIAL_QUANTITY

    context.user_data["special_quantity"] = quantity
    return await save_special_item_from_context(update, context)


async def save_special_item_from_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category_id = context.user_data.get("special_category_id")
    product_id = context.user_data.get("special_product_id")
    size = context.user_data.get("special_size")

    if not category_id or not product_id or not size:
        await update.message.reply_text("Данные товара потерялись. Начните заново.")
        return ConversationHandler.END

    report_type = context.user_data.get("special_report_type")
    item = {
        "category_id": category_id,
        "product_id": product_id,
        "size": size,
    }

    if report_type == "illiquid":
        packed = context.user_data.get("special_packed", 0)
        defective = context.user_data.get("special_defective", 0)
        rework = context.user_data.get("special_rework", 0)
        quantity = packed + defective + rework
        if quantity <= 0:
            await update.message.reply_text("В сумме должно быть больше 0. Введите «Норм» заново:")
            return SPECIAL_PACKED
        item.update(
            {
                "packed": packed,
                "defective": defective,
                "rework": rework,
                "quantity": quantity,
            }
        )
    else:
        quantity = context.user_data.get("special_quantity", 0)
        item["quantity"] = quantity

    items = special_items_from_user_data(context)

    edit_index = context.user_data.pop("special_edit_item_index", None)
    if edit_index is not None and 0 <= edit_index < len(items):
        items[edit_index] = item
        action_text = "Строка обновлена ✅"
    else:
        items.append(item)
        action_text = "Товар добавлен ✅"

    for key in (
        "special_size",
        "special_packed",
        "special_defective",
        "special_rework",
        "special_quantity",
    ):
        context.user_data.pop(key, None)

    await update.message.reply_text(
        f"{action_text}\n\n"
        f"{format_special_item(item)}\n\n"
        "Текущий список:\n"
        f"{format_special_items(items)}",
        reply_markup=build_special_items_menu_keyboard(context),
    )
    return SPECIAL_CATEGORY


async def special_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    for key in ("special_category_id", "special_model_id", "special_product_id", "special_size"):
        context.user_data.pop(key, None)

    await query.edit_message_text(
        "Выберите группу товара:",
        reply_markup=build_special_category_keyboard(),
    )
    return SPECIAL_CATEGORY


async def special_add_same_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = context.user_data.get("special_category_id")
    product_id = context.user_data.get("special_product_id")

    if not category_id or not product_id:
        await query.edit_message_text(
            "Текущая модель не найдена. Выберите товар заново:",
            reply_markup=build_special_category_keyboard(),
        )
        return SPECIAL_CATEGORY

    product_name = CATEGORIES[category_id]["products"][product_id]
    await query.edit_message_text(
        f"Товар: {product_name}\n\nВыберите еще один размер этой модели:",
        reply_markup=build_special_sizes_keyboard(),
    )
    return SPECIAL_SIZE


async def special_show_items_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "Текущий список:\n"
        f"{format_special_items(context.user_data.get('special_items') or [])}",
        reply_markup=build_special_items_menu_keyboard(context),
    )
    return SPECIAL_CATEGORY


async def special_edit_item_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = context.user_data.get("special_items") or []
    if not items:
        await query.edit_message_text(
            "Пока нет строк для изменения.",
            reply_markup=build_special_items_menu_keyboard(context),
        )
        return SPECIAL_CATEGORY

    await query.edit_message_text(
        "Выберите строку для изменения:",
        reply_markup=build_special_item_select_keyboard("edit_item", items),
    )
    return SPECIAL_EDIT_ITEM


async def special_delete_item_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = context.user_data.get("special_items") or []
    if not items:
        await query.edit_message_text(
            "Пока нет строк для удаления.",
            reply_markup=build_special_items_menu_keyboard(context),
        )
        return SPECIAL_CATEGORY

    await query.edit_message_text(
        "Выберите строку для удаления:",
        reply_markup=build_special_item_select_keyboard("delete_item", items),
    )
    return SPECIAL_EDIT_ITEM


async def special_edit_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.replace("specrep:edit_item:", ""))
    items = context.user_data.get("special_items") or []

    if index < 0 or index >= len(items):
        await query.edit_message_text("Строка не найдена.", reply_markup=build_special_items_menu_keyboard(context))
        return SPECIAL_CATEGORY

    item = items[index]
    context.user_data["special_edit_item_index"] = index
    context.user_data["special_category_id"] = item["category_id"]
    context.user_data["special_product_id"] = item["product_id"]
    context.user_data["special_size"] = item["size"]

    if context.user_data.get("special_report_type") == "illiquid":
        await query.edit_message_text(
            "Введите новое количество в статусе «Норм».\n"
            "Если нет — введите 0."
        )
        return SPECIAL_PACKED

    await query.edit_message_text("Введите новое количество:")
    return SPECIAL_QUANTITY


async def special_delete_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.replace("specrep:delete_item:", ""))
    items = context.user_data.get("special_items") or []

    if index < 0 or index >= len(items):
        await query.edit_message_text("Строка не найдена.", reply_markup=build_special_items_menu_keyboard(context))
        return SPECIAL_CATEGORY

    deleted_item = items.pop(index)
    await query.edit_message_text(
        "Строка удалена ✅\n\n"
        f"{format_special_item(deleted_item)}\n\n"
        "Текущий список:\n"
        f"{format_special_items(items)}",
        reply_markup=build_special_items_menu_keyboard(context),
    )
    return SPECIAL_CATEGORY


async def special_back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    for key in ("special_category_id", "special_model_id", "special_product_id", "special_size"):
        context.user_data.pop(key, None)

    await query.edit_message_text(
        "Выберите группу товара:",
        reply_markup=build_special_category_keyboard(),
    )
    return SPECIAL_CATEGORY


async def special_back_to_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = context.user_data.get("special_category_id")
    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text("Группа не найдена. Начните заново.")
        return ConversationHandler.END

    context.user_data.pop("special_model_id", None)
    context.user_data.pop("special_product_id", None)
    context.user_data.pop("special_size", None)

    await query.edit_message_text(
        f"Группа: {CATEGORIES[category_id]['name']}\n\nВыберите модель:",
        reply_markup=build_special_models_keyboard(category_id),
    )
    return SPECIAL_MODEL


async def special_back_to_colors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = context.user_data.get("special_category_id")
    model_id = context.user_data.get("special_model_id")

    if not category_id or not model_id:
        return await special_back_to_models(update, context)

    context.user_data.pop("special_product_id", None)
    context.user_data.pop("special_size", None)

    variants = CATEGORIES[category_id]["models"][model_id]["variants"]
    if len(variants) == 1:
        return await special_back_to_models(update, context)

    await query.edit_message_text(
        f"Модель: {CATEGORIES[category_id]['models'][model_id]['name']}\n\nВыберите цвет / вариант:",
        reply_markup=build_special_product_colors_keyboard(category_id, model_id),
    )
    return SPECIAL_COLOR


async def finish_special_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = context.user_data.get("special_items") or []
    if not items:
        await query.edit_message_text(
            "Добавьте хотя бы один товар.",
            reply_markup=build_special_items_menu_keyboard(context),
        )
        return SPECIAL_CATEGORY

    report_type = context.user_data["special_report_type"]
    store_from = context.user_data.get("special_store_from", "")
    removal_date = context.user_data.get("special_removal_date", "")
    created_by = user_display_name(query.from_user)
    report_text = format_special_report_text(report_type, store_from, removal_date, items, created_by)

    try:
        telegram_data = await send_special_report_to_topic(context, report_text)
        if context.user_data.get("special_mode") == "edit":
            old_report = get_special_receiving_report(context.user_data["special_edit_report_id"])
            await delete_special_report_messages(context, old_report)
            update_special_receiving_report(
                report_id=context.user_data["special_edit_report_id"],
                store_from=store_from,
                removal_date=removal_date,
                items=items,
                telegram_data=telegram_data,
            )
            status = "Отчет отредактирован и заново выгружен ✅"
        else:
            report_id = create_special_receiving_report(
                report_type=report_type,
                store_from=store_from,
                removal_date=removal_date,
                created_by=created_by,
                items=items,
                telegram_data=telegram_data,
            )
            status = f"Отчет выгружен ✅\nID отчета: {report_id}"
    except Exception as error:
        logging.exception("Не удалось выгрузить специальный отчет")
        await query.edit_message_text(
            "Не удалось выгрузить отчет ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_report_type_keyboard(),
        )
        return ConversationHandler.END

    context.user_data.clear()
    await query.edit_message_text(
        f"{report_text}\n\n{status}",
        reply_markup=build_receiving_report_type_keyboard(),
    )
    return ConversationHandler.END


async def special_reports_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]
    report_type = parts[2]

    try:
        reports = get_special_receiving_reports(report_type=report_type, limit=10)
    except Exception as error:
        logging.exception("Не удалось получить специальные отчеты")
        await query.edit_message_text(
            "Не удалось получить отчеты ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_report_type_keyboard(),
        )
        return

    if not reports:
        await query.edit_message_text(
            f"Нет отчетов в разделе «{special_report_title(report_type)}».",
            reply_markup=build_special_action_keyboard(report_type),
        )
        return

    callback_action = "delete_confirm" if action == "delete_choose" else "edit_open"
    await query.edit_message_text(
        "Выберите отчет:",
        reply_markup=build_special_reports_keyboard(callback_action, report_type, reports),
    )


async def special_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    report_id = query.data.replace("specrep:delete_confirm:", "")
    report = get_special_receiving_report(report_id)

    if not report:
        await query.edit_message_text("Отчет не найден.", reply_markup=build_receiving_report_type_keyboard())
        return

    text = format_special_report_text(
        report["report_type"],
        report["store_from"],
        report["removal_date"],
        report["items"],
        report["created_by"],
    )
    await query.edit_message_text(
        f"{text}\n\nУдалить этот отчет?",
        reply_markup=build_special_delete_confirm_keyboard(report_id),
    )


async def special_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    report_id = query.data.replace("specrep:delete_do:", "")
    report = get_special_receiving_report(report_id)

    if not report:
        await query.edit_message_text("Отчет не найден.", reply_markup=build_receiving_report_type_keyboard())
        return

    deleted_messages, delete_errors = await delete_special_report_messages(context, report)

    try:
        delete_special_receiving_report(report_id)
    except Exception as error:
        logging.exception("Не удалось удалить специальный отчет из БД")
        await query.edit_message_text(
            "Сообщения в Telegram удалены частично/полностью, но отчет не удалился из БД ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_report_type_keyboard(),
        )
        return

    text = (
        "Отчет удален ✅\n\n"
        f"ID отчета: {report_id}\n"
        f"Удалено сообщений Telegram: {deleted_messages}"
    )
    if delete_errors:
        text += "\n\nНекоторые сообщения удалить не удалось ⚠️\n" + "\n".join(delete_errors[:5])

    await query.edit_message_text(text, reply_markup=build_receiving_report_type_keyboard())


async def special_edit_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    report_id = query.data.replace("specrep:edit_open:", "")
    report = get_special_receiving_report(report_id)

    if not report:
        await query.edit_message_text("Отчет не найден.", reply_markup=build_receiving_report_type_keyboard())
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["special_mode"] = "edit"
    context.user_data["special_edit_report_id"] = report["id"]
    context.user_data["special_report_type"] = report["report_type"]
    context.user_data["special_store_from"] = report["store_from"]
    context.user_data["special_removal_date"] = report["removal_date"]
    context.user_data["special_items"] = []
    for item in report["items"]:
        draft_item = {
            "category_id": item["category_id"],
            "product_id": item["product_id"],
            "size": item["size"],
            "quantity": item["quantity"],
        }
        if report["report_type"] == "illiquid":
            draft_item["packed"] = item.get("packed", item["quantity"])
            draft_item["defective"] = item.get("defective", 0)
            draft_item["rework"] = item.get("rework", 0)
        context.user_data["special_items"].append(draft_item)

    await query.edit_message_text(
        "Что нужно изменить?\n\n"
        f"{format_special_report_text(report['report_type'], report['store_from'], report['removal_date'], report['items'], report['created_by'])}",
        reply_markup=build_special_edit_params_keyboard(report),
    )
    return SPECIAL_EDIT_VALUE


async def special_edit_param_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    param = query.data.replace("specrep:edit_param:", "")
    context.user_data["special_edit_param"] = param

    if param == "store":
        await query.edit_message_text("Введите новый магазин:")
        return SPECIAL_EDIT_VALUE

    if param == "date":
        await query.edit_message_text("Введите новую дату вывоза в формате ДД.ММ.ГГГГ:")
        return SPECIAL_DATE

    if param == "items":
        await query.edit_message_text(
            "Текущий список:\n"
            f"{format_special_items(context.user_data.get('special_items') or [])}",
            reply_markup=build_special_items_menu_keyboard(context),
        )
        return SPECIAL_CATEGORY

    return SPECIAL_EDIT_VALUE


async def special_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    param = context.user_data.get("special_edit_param")

    if param == "store":
        context.user_data["special_store_from"] = update.message.text.strip()

    report = {
        "report_type": context.user_data["special_report_type"],
    }
    await update.message.reply_text(
        "Параметр обновлен. Можно изменить еще что-то или завершить редактирование.",
        reply_markup=build_special_edit_params_keyboard(report),
    )
    return SPECIAL_EDIT_VALUE


async def report_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    report_date = query.data.replace("report:date:", "")
    exported_by = user_display_name(query.from_user)

    try:
        has_records = has_unexported_receiving_records_for_date(report_date)
    except Exception as error:
        logging.exception("Не удалось проверить невыгруженные записи")
        await query.edit_message_text(
            "Не удалось проверить записи в PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not has_records:
        await query.edit_message_text(
            f"Дата: {report_date}\n"
            f"Выгрузил: {exported_by}\n\n"
            "Нет невыгруженных записей за эту дату.\n\n"
            "Если запись уже была выгружена в тему, она не попадает в повторную выгрузку.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    try:
        report_text = build_receiving_report_text(
            report_date=report_date,
            exported_by=exported_by,
            only_unexported=True,
        )
    except Exception as error:
        logging.exception("Не удалось собрать отчет оприходований")
        await query.edit_message_text(
            "Не удалось собрать отчет из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    try:
        export_info = await send_report_to_topic(context, report_text)
        export_id = make_export_id(report_date)

        marked_count = mark_receiving_rows_exported(
            report_date=report_date,
            exported_by=exported_by,
            export_id=export_id,
            chat_id=export_info["chat_id"],
            thread_id=export_info["thread_id"],
            message_ids=export_info["message_ids"],
        )

        send_status = (
            f"{export_info['status']}\n"
            f"ID выгрузки: {export_id}\n"
            f"Выгруженных строк отмечено в таблице: {marked_count}"
        )
    except Exception as error:
        logging.exception("Не удалось отправить отчет в тему или отметить строки")
        send_status = f"Не удалось отправить отчет в тему / отметить строки ⚠️\nОшибка: {error}"

    await query.edit_message_text(
        f"{report_text}\n\n{send_status}",
        reply_markup=build_receiving_menu_keyboard(),
    )


# ============================================================
# УДАЛЕНИЕ НЕВЫГРУЖЕННЫХ ЗАПИСЕЙ ОПРИХОДОВАНИЯ
# ============================================================

def shorten_text(text, max_len=32):
    text = str(text)

    if len(text) <= max_len:
        return text

    return text[: max_len - 1] + "…"


def record_employee_display_name(record):
    return employee_name_from_user_id_or_username(
        user_id=record.get("user_id"),
        username=record.get("username"),
        fallback=record.get("username") or record.get("user_id") or "",
    )


def record_button_text(record):
    return (
        f"{shorten_text(record_employee_display_name(record), 18)} | "
        f"{shorten_text(record['product_name'], 24)} | "
        f"{record['size']} | "
        f"У:{record['packed']} Б:{record['defective']} Д:{record['rework']}"
    )


def format_record_details(record):
    return (
        f"Дата: {record['date']}\n"
        f"Сотрудник: {record_employee_display_name(record)}\n"
        f"Группа: {record['category_name']}\n"
        f"Модель: {record['product_name']}\n"
        f"Размер: {record['size']}\n"
        f"Упаковано: {record['packed']}\n"
        f"Брак: {record['defective']}\n"
        f"Доработка: {record['rework']}"
    )


def build_delete_records_keyboard(records):
    keyboard = []

    for record in records:
        keyboard.append(
            [
                InlineKeyboardButton(
                    record_button_text(record),
                    callback_data=f"recvdel:confirm:{record['row_number']}",
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")])

    return InlineKeyboardMarkup(keyboard)


def build_confirm_delete_keyboard(row_number):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Удалить", callback_data=f"recvdel:do:{row_number}"),
                InlineKeyboardButton("❌ Отмена", callback_data="recvdel:cancel"),
            ]
        ]
    )


async def receiving_delete_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        records = get_unexported_receiving_records(limit=15)
    except Exception as error:
        logging.exception("Не удалось получить невыгруженные записи")
        await query.edit_message_text(
            "Не удалось получить невыгруженные записи из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not records:
        await query.edit_message_text(
            "Нет невыгруженных записей, которые можно удалить.\n\n"
            "Записи, которые уже были выгружены в тему Telegram, не показываются и не удаляются.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    await query.edit_message_text(
        "Выберите запись для удаления.\n\n"
        "Показываются только последние 15 записей, которые еще НЕ были выгружены в тему Telegram:",
        reply_markup=build_delete_records_keyboard(records),
    )


async def receiving_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    row_number = int(query.data.replace("recvdel:confirm:", ""))

    try:
        record = get_receiving_record_by_row(row_number)
    except Exception as error:
        logging.exception("Не удалось прочитать запись для удаления")
        await query.edit_message_text(
            "Не удалось прочитать запись из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not record:
        await query.edit_message_text(
            "Запись не найдена. Возможно, она уже была удалена.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if record["exported"]:
        await query.edit_message_text(
            "Эта запись уже выгружена в отчет, поэтому удалить её через бота нельзя.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    context.user_data["receiving_delete_row_number"] = row_number

    await query.edit_message_text(
        "Проверьте запись перед удалением:\n\n"
        f"{format_record_details(record)}\n\n"
        "Удалить эту запись из PostgreSQL?",
        reply_markup=build_confirm_delete_keyboard(row_number),
    )


async def receiving_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    row_number = int(query.data.replace("recvdel:do:", ""))

    try:
        deleted_record = delete_unexported_receiving_record(row_number)
    except Exception as error:
        logging.exception("Не удалось удалить запись")
        await query.edit_message_text(
            "Не удалось удалить запись ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    context.user_data.pop("receiving_delete_row_number", None)

    await query.edit_message_text(
        "Запись удалена ✅\n\n"
        f"{format_record_details(deleted_record)}",
        reply_markup=build_receiving_menu_keyboard(),
    )


async def receiving_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("receiving_delete_row_number", None)

    await query.edit_message_text(
        "Удаление отменено.",
        reply_markup=build_receiving_menu_keyboard(),
    )


# ============================================================
# УДАЛЕНИЕ ВЫГРУЖЕННОГО ОТЧЕТА ИЗ ТЕМЫ TELEGRAM
# ============================================================

def report_group_button_text(group):
    return (
        f"{group['date']} | "
        f"{shorten_text(group['exported_by'], 16)} | "
        f"строк: {group['row_count']} | "
        f"общее: {group['total']}"
    )


def format_report_group_details(group):
    message_ids = ", ".join(str(message_id) for message_id in group["message_ids"])

    return (
        f"Дата: {group['date']}\n"
        f"Выгрузил: {group['exported_by']}\n"
        f"Дата выгрузки: {group['exported_at']}\n"
        f"ID выгрузки: {group['export_id']}\n"
        f"Количество строк: {group['row_count']}\n"
        f"Упаковано: {group['total_packed']}\n"
        f"Брак: {group['total_defective']}\n"
        f"Доработка: {group['total_rework']}\n"
        f"Общее: {group['total']}\n"
        f"Message IDs: {message_ids}"
    )


def build_report_groups_keyboard(groups):
    keyboard = []

    for group in groups:
        keyboard.append(
            [
                InlineKeyboardButton(
                    report_group_button_text(group),
                    callback_data=f"recvrepdel:confirm:{group['export_id']}",
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")])

    return InlineKeyboardMarkup(keyboard)


def build_confirm_report_delete_keyboard(export_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Удалить отчет", callback_data=f"recvrepdel:do:{export_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="recvrepdel:cancel"),
            ]
        ]
    )


async def receiving_report_delete_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        groups = get_exported_receiving_report_groups(limit=10)
    except Exception as error:
        logging.exception("Не удалось получить выгруженные отчеты")
        await query.edit_message_text(
            "Не удалось получить выгруженные отчеты из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not groups:
        await query.edit_message_text(
            "Нет отчетов, которые можно удалить из темы.\n\n"
            "Важно: удалить можно только отчеты, выгруженные после обновления, "
            "потому что для старых выгрузок бот не знает Telegram message_id.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    await query.edit_message_text(
        "Выберите отчет, который нужно удалить из темы Telegram.\n\n"
        "После удаления записи снова станут невыгруженными, их можно будет исправить и выгрузить заново:",
        reply_markup=build_report_groups_keyboard(groups),
    )


async def receiving_report_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    export_id = query.data.replace("recvrepdel:confirm:", "")

    try:
        group = get_exported_receiving_report_by_export_id(export_id)
    except Exception as error:
        logging.exception("Не удалось получить отчет для удаления")
        await query.edit_message_text(
            "Не удалось получить отчет из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not group:
        await query.edit_message_text(
            "Отчет не найден. Возможно, он уже был удален или выгрузка старая.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    context.user_data["receiving_report_delete_export_id"] = export_id

    await query.edit_message_text(
        "Проверьте отчет перед удалением:\n\n"
        f"{format_report_group_details(group)}\n\n"
        "Что произойдет после подтверждения:\n"
        "1. Бот удалит сообщение/сообщения отчета из темы Telegram.\n"
        "2. Записи в PostgreSQL снова станут невыгруженными.\n"
        "3. Их можно будет исправить и выгрузить заново.\n\n"
        "Удалить этот отчет?",
        reply_markup=build_confirm_report_delete_keyboard(export_id),
    )


async def receiving_report_delete_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    export_id = query.data.replace("recvrepdel:do:", "")

    try:
        group = get_exported_receiving_report_by_export_id(export_id)
    except Exception as error:
        logging.exception("Не удалось получить отчет для удаления")
        await query.edit_message_text(
            "Не удалось получить отчет из PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    if not group:
        await query.edit_message_text(
            "Отчет не найден. Возможно, он уже был удален.",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    deleted_messages = 0
    delete_errors = []

    for message_id in group["message_ids"]:
        try:
            await context.bot.delete_message(
                chat_id=int(group["chat_id"]),
                message_id=int(message_id),
            )
            deleted_messages += 1
        except Exception as error:
            logging.exception("Не удалось удалить сообщение отчета из Telegram")
            delete_errors.append(f"message_id {message_id}: {error}")

    try:
        unmarked_rows = unmark_receiving_rows_by_export_id(export_id)
    except Exception as error:
        logging.exception("Не удалось снять отметку выгрузки с записей")
        await query.edit_message_text(
            "Сообщения в Telegram частично удалены, но не удалось снять отметку выгрузки в PostgreSQL ⚠️\n\n"
            f"Ошибка: {error}",
            reply_markup=build_receiving_menu_keyboard(),
        )
        return

    context.user_data.pop("receiving_report_delete_export_id", None)

    text = (
        "Отчет удален из темы ✅\n\n"
        f"ID выгрузки: {export_id}\n"
        f"Удалено сообщений Telegram: {deleted_messages}\n"
        f"Записей снова сделано невыгруженными: {unmarked_rows}\n\n"
        "Теперь можно удалить/исправить нужные записи и выгрузить отчет заново."
    )

    if delete_errors:
        text += (
            "\n\nНекоторые сообщения удалить не удалось ⚠️\n"
            + "\n".join(delete_errors[:5])
        )

    await query.edit_message_text(
        text,
        reply_markup=build_receiving_menu_keyboard(),
    )


async def receiving_report_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("receiving_report_delete_export_id", None)

    await query.edit_message_text(
        "Удаление отчета отменено.",
        reply_markup=build_receiving_menu_keyboard(),
    )


# ============================================================
# HANDLERS
# ============================================================

def get_report_handlers():
    return [
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(special_create_start, pattern=r"^specrep:create:(illiquid|rejected)$"),
                CallbackQueryHandler(special_edit_open, pattern=r"^specrep:edit_open:\d+$"),
            ],
            states={
                SPECIAL_STORE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_store_received),
                ],
                SPECIAL_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_date_received),
                ],
                SPECIAL_CATEGORY: [
                    CallbackQueryHandler(special_category_selected, pattern=r"^cat:"),
                    CallbackQueryHandler(special_add_item, pattern=r"^specrep:add_item$"),
                    CallbackQueryHandler(special_add_same_product, pattern=r"^specrep:add_same_product$"),
                    CallbackQueryHandler(special_show_items_menu, pattern=r"^specrep:items_menu$"),
                    CallbackQueryHandler(special_edit_item_choose, pattern=r"^specrep:edit_item_choose$"),
                    CallbackQueryHandler(special_delete_item_choose, pattern=r"^specrep:delete_item_choose$"),
                    CallbackQueryHandler(finish_special_report, pattern=r"^specrep:finish$"),
                ],
                SPECIAL_MODEL: [
                    CallbackQueryHandler(special_model_selected, pattern=r"^model:"),
                    CallbackQueryHandler(special_back_to_categories, pattern=r"^back:categories$"),
                    CallbackQueryHandler(special_show_items_menu, pattern=r"^specrep:items_menu$"),
                ],
                SPECIAL_COLOR: [
                    CallbackQueryHandler(special_product_selected, pattern=r"^prod:"),
                    CallbackQueryHandler(special_back_to_models, pattern=r"^back:models$"),
                ],
                SPECIAL_SIZE: [
                    CallbackQueryHandler(special_size_selected, pattern=r"^size:"),
                    CallbackQueryHandler(special_back_to_colors, pattern=r"^back:colors$"),
                ],
                SPECIAL_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_quantity_received),
                ],
                SPECIAL_PACKED: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_packed_received),
                ],
                SPECIAL_DEFECTIVE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_defective_received),
                ],
                SPECIAL_REWORK: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_rework_received),
                ],
                SPECIAL_EDIT_VALUE: [
                    CallbackQueryHandler(special_edit_param_selected, pattern=r"^specrep:edit_param:"),
                    CallbackQueryHandler(finish_special_report, pattern=r"^specrep:edit_finish$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, special_edit_value_received),
                ],
                SPECIAL_EDIT_ITEM: [
                    CallbackQueryHandler(special_edit_item_selected, pattern=r"^specrep:edit_item:\d+$"),
                    CallbackQueryHandler(special_delete_item_selected, pattern=r"^specrep:delete_item:\d+$"),
                    CallbackQueryHandler(special_show_items_menu, pattern=r"^specrep:items_menu$"),
                ],
            },
            fallbacks=[],
        ),
        CallbackQueryHandler(special_report_type_selected, pattern=r"^recvtype:(new_supply|illiquid|rejected)$"),
        CallbackQueryHandler(special_reports_choose, pattern=r"^specrep:(delete_choose|edit_choose):(illiquid|rejected)$"),
        CallbackQueryHandler(special_delete_confirm, pattern=r"^specrep:delete_confirm:\d+$"),
        CallbackQueryHandler(special_delete_do, pattern=r"^specrep:delete_do:\d+$"),
        CallbackQueryHandler(report_choose_date, pattern=r"^report:choose_date$"),
        CallbackQueryHandler(report_date_selected, pattern=r"^report:date:"),
        CallbackQueryHandler(receiving_delete_choose, pattern=r"^recvdel:choose$"),
        CallbackQueryHandler(receiving_delete_confirm, pattern=r"^recvdel:confirm:\d+$"),
        CallbackQueryHandler(receiving_delete_do, pattern=r"^recvdel:do:\d+$"),
        CallbackQueryHandler(receiving_delete_cancel, pattern=r"^recvdel:cancel$"),
        CallbackQueryHandler(receiving_report_delete_choose, pattern=r"^recvrepdel:choose$"),
        CallbackQueryHandler(receiving_report_delete_confirm, pattern=r"^recvrepdel:confirm:"),
        CallbackQueryHandler(receiving_report_delete_do, pattern=r"^recvrepdel:do:"),
        CallbackQueryHandler(receiving_report_delete_cancel, pattern=r"^recvrepdel:cancel$"),
    ]
