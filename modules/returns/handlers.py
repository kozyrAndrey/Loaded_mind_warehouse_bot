import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import GROUP_CHAT_ID, RETURNS_TOPIC_ID, RETURN_CHZ_CHAT_ID, RETURN_CHZ_TOPIC_ID
from core.keyboards import (
    build_main_menu_keyboard,
    build_return_category_keyboard,
    build_return_models_keyboard,
    build_return_nav_keyboard,
    build_return_product_colors_keyboard,
    build_return_sizes_keyboard,
)
from modules.marking.duplicate_chz import (
    DuplicateChzError,
    extract_short_marking_code,
    normalize_chz_text,
)
from modules.receiving.products import CATEGORIES, SIZES
from modules.returns.storage import (
    create_return_record,
    get_recent_return_records,
    get_return_record,
    mark_return_record_deleted,
    update_return_record,
)


# ============================================================
# НАСТРОЙКИ УПОМИНАНИЙ
# ============================================================
# Замени на реальные Telegram username руководителей.
# Важно: username в Telegram не может содержать пробелы.
# Пример: "@warehouse_manager"

WAREHOUSE_MANAGER_MENTION = "@opulent_shooter"
SUPPORT_MANAGER_MENTION = "@meelxw1"

# ============================================================
# СОСТОЯНИЯ ДИАЛОГА
# ============================================================

(
    RET_PHOTO,
    RET_CHZ_PHOTO,  # legacy state, больше не используется как общий ЧЗ для СДЭК
    RET_COUNTERPARTY,
    RET_TRACK_NUMBER,
    RET_ITEMS_COUNT,
    RET_ITEM_CATEGORY,
    RET_ITEM_MODEL,
    RET_ITEM_PRODUCT,
    RET_ITEM_CHZ_PHOTO,
    RET_ITEM_SIZE,
    RET_ITEM_CONDITION,
    RET_ITEM_EXTRA_PHOTO,
    RET_ITEM_CONDITION_COMMENT,
    RET_ADMIN_SELECT,
    RET_ADMIN_EDIT_FIELD,
    RET_ADMIN_EDIT_VALUE,
    RET_ADMIN_DELETE_CONFIRM,
    RET_ITEM_CHZ_SCAN,
) = range(100, 118)


# ============================================================
# СОСТОЯНИЯ ТОВАРА В ВОЗВРАТЕ
# ============================================================

RETURN_CONDITIONS = {
    "normal": {
        "label": "норм",
        "needs_photo": False,
        "needs_comment": False,
        "mentions": [],
    },
    "invoice_defect": {
        "label": "брак по накладной",
        "needs_photo": True,
        "needs_comment": True,
        "mentions": ["warehouse"],
        "photo_prompt": (
            "Отправьте фото переупакованного возврата по правилам "
            "для статуса «брак по накладной»."
        ),
        "comment_prompt": "Введите комментарий к браку по накладной:",
    },
    "invoice_rework": {
        "label": "доработка по накладной",
        "needs_photo": True,
        "needs_comment": True,
        "mentions": ["warehouse"],
        "photo_prompt": (
            "Отправьте фото переупакованного возврата по правилам "
            "для статуса «доработка по накладной»."
        ),
        "comment_prompt": "Введите комментарий к доработке по накладной:",
    },
    "not_invoice_defect": {
        "label": "брак не по накладной",
        "needs_photo": True,
        "needs_comment": True,
        "mentions": ["warehouse", "support"],
        "photo_prompt": (
            "Отправьте фото переупакованного возврата по правилам "
            "для статуса «брак НЕ по накладной»."
        ),
        "comment_prompt": "Введите комментарий к браку НЕ по накладной:",
    },
}


def build_return_condition_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Норм", callback_data="retcond:normal")],
            [InlineKeyboardButton("📄 Брак по накладной", callback_data="retcond:invoice_defect")],
            [InlineKeyboardButton("🛠 Доработка по накладной", callback_data="retcond:invoice_rework")],
            [InlineKeyboardButton("⚠️ Брак НЕ по накладной", callback_data="retcond:not_invoice_defect")],
            [
                InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
                InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
            ],
        ]
    )


def build_chz_photo_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ЧЗ нет", callback_data="ret:chz_missing")],
            [
                InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
                InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
            ],
        ]
    )


def build_chz_scan_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ЧЗ нет", callback_data="ret:chz_missing")],
            [
                InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
                InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
            ],
        ]
    )


def build_showroom_label_photo_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Этикетки нет", callback_data="ret:label_missing")],
            [
                InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
                InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
            ],
        ]
    )


def build_return_records_keyboard(records, action):
    rows = []
    for record in records:
        label = format_return_record_button(record)
        rows.append([InlineKeyboardButton(label, callback_data=f"retadmin:{action}:{record['id']}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:returns")])
    return InlineKeyboardMarkup(rows)


def build_return_edit_field_keyboard(record):
    rows = [[InlineKeyboardButton("👤 ФИО контрагента", callback_data="retadminfield:counterparty")]]
    if record.get("return_type") == "cdek":
        rows.append([InlineKeyboardButton("🔢 Трек-номер", callback_data="retadminfield:track_number")])
    else:
        rows.append([InlineKeyboardButton("🏷 Статус этикетки", callback_data="retadminfield:label_status")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")])
    return InlineKeyboardMarkup(rows)


def build_return_delete_confirm_keyboard(record_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Удалить", callback_data=f"retadmindel:yes:{record_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")],
        ]
    )


def build_return_admin_record_keyboard(record):
    record_id = record["id"]
    rows = [
        [InlineKeyboardButton("👷 Сотрудник", callback_data="retadminfield:employee_name")],
        [InlineKeyboardButton("👤 ФИО контрагента", callback_data="retadminfield:counterparty")],
        [InlineKeyboardButton("🔁 Тип возврата", callback_data=f"retadmintype:{record_id}")],
    ]
    if record.get("return_type") == "cdek":
        rows.append([InlineKeyboardButton("🔢 Трек-номер", callback_data="retadminfield:track_number")])
    else:
        rows.append([InlineKeyboardButton("🏷 Статус этикетки", callback_data="retadminfield:label_status")])
    rows.extend(
        [
            [InlineKeyboardButton("📄 Фото накладной / этикетки", callback_data=f"retadminbasephoto:{record_id}")],
            [InlineKeyboardButton("🧺 Товары", callback_data=f"retadminitems:{record_id}")],
            [InlineKeyboardButton("📤 Перевыгрузить в тему", callback_data=f"retadminresend:{record_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data="retadmin:edit")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_return_admin_type_keyboard(record_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚚 СДЭК", callback_data=f"retadmintypeset:{record_id}:cdek")],
            [InlineKeyboardButton("🏬 Шоу-рум", callback_data=f"retadmintypeset:{record_id}:showroom")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"retadmin:edit:{record_id}")],
        ]
    )


def build_return_admin_items_keyboard(record):
    record_id = record["id"]
    rows = []
    for index, item in enumerate(record.get("items") or []):
        product_name = CATEGORIES[item["category_id"]]["products"][item["product_id"]]
        rows.append(
            [InlineKeyboardButton(f"{index + 1}. {product_name}, {item['size']}", callback_data=f"retadminitem:{record_id}:{index}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton("➕ Добавить товар", callback_data=f"retadminitemadd:{record_id}")],
            [InlineKeyboardButton("⬅️ Назад к записи", callback_data=f"retadmin:edit:{record_id}")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_return_admin_item_keyboard(record_id, item_index):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧥 Товар / модель / цвет", callback_data=f"retadminitemfield:{record_id}:{item_index}:product")],
            [InlineKeyboardButton("📏 Размер", callback_data=f"retadminitemfield:{record_id}:{item_index}:size")],
            [InlineKeyboardButton("📌 Состояние", callback_data=f"retadminitemfield:{record_id}:{item_index}:condition")],
            [InlineKeyboardButton("💬 Комментарий", callback_data=f"retadminitemfield:{record_id}:{item_index}:comment")],
            [InlineKeyboardButton("🔖 Честный знак", callback_data=f"retadminitemfield:{record_id}:{item_index}:chz")],
            [InlineKeyboardButton("📷 Фото переупаковки", callback_data=f"retadminitemfield:{record_id}:{item_index}:photo")],
            [InlineKeyboardButton("🗑 Удалить товар", callback_data=f"retadminitemdelete:{record_id}:{item_index}")],
            [InlineKeyboardButton("⬅️ К товарам", callback_data=f"retadminitems:{record_id}")],
        ]
    )


def build_admin_category_keyboard(record_id, item_index):
    rows = [
        [InlineKeyboardButton(category_data["name"], callback_data=f"retadmincat:{record_id}:{item_index}:{category_id}")]
        for category_id, category_data in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")])
    return InlineKeyboardMarkup(rows)


def build_admin_models_keyboard(record_id, item_index, category_id):
    rows = [
        [InlineKeyboardButton(model_data["name"], callback_data=f"retadminmodel:{record_id}:{item_index}:{model_id}")]
        for model_id, model_data in CATEGORIES[category_id]["models"].items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")])
    return InlineKeyboardMarkup(rows)


def build_admin_products_keyboard(record_id, item_index, category_id, model_id):
    rows = []
    for variant_data in CATEGORIES[category_id]["models"][model_id]["variants"].values():
        color = variant_data["color"]
        text = "Выбрать" if color == "ONE COLOR" else color
        rows.append(
            [InlineKeyboardButton(text, callback_data=f"retadminprod:{record_id}:{item_index}:{variant_data['id']}")]
        )
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")])
    return InlineKeyboardMarkup(rows)


def build_admin_sizes_keyboard(record_id, item_index):
    rows = []
    row = []
    for size in SIZES:
        row.append(InlineKeyboardButton(size, callback_data=f"retadminsizeset:{record_id}:{item_index}:{size}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")])
    return InlineKeyboardMarkup(rows)


def build_admin_conditions_keyboard(record_id, item_index):
    rows = [
        [InlineKeyboardButton(data["label"], callback_data=f"retadmincondset:{record_id}:{item_index}:{condition_key}")]
        for condition_key, data in RETURN_CONDITIONS.items()
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")])
    return InlineKeyboardMarkup(rows)


def build_admin_chz_keyboard(record_id, item_index):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Фото отправлено", callback_data=f"retadminchzset:{record_id}:{item_index}:Фото отправлено")],
            [InlineKeyboardButton("ЧЗ нет", callback_data=f"retadminchzset:{record_id}:{item_index}:ЧЗ нет")],
            [InlineKeyboardButton("Очистить", callback_data=f"retadminchzset:{record_id}:{item_index}:")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")],
        ]
    )


def build_admin_add_chz_keyboard(record_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ЧЗ нет", callback_data=f"retadminaddchzmissing:{record_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")],
        ]
    )


def build_admin_item_delete_keyboard(record_id, item_index):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Удалить товар", callback_data=f"retadminitemdeleteyes:{record_id}:{item_index}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"retadminitem:{record_id}:{item_index}")],
        ]
    )


def get_return_type(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("return_type", "cdek")


def is_cdek_return(context: ContextTypes.DEFAULT_TYPE):
    return get_return_type(context) == "cdek"


def get_return_type_label(context: ContextTypes.DEFAULT_TYPE):
    return "СДЭК" if is_cdek_return(context) else "Шоу-рум"


def get_return_type_label_from_value(return_type):
    return "СДЭК" if return_type == "cdek" else "Шоу-рум"


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================


def get_employee_full_name_for_user(user):
    """Возвращает ФИО сотрудника из модуля ЗП по Telegram user_id/username.

    Если сотрудник не найден или справочник временно недоступен,
    используем Telegram full_name как безопасный fallback.
    """
    try:
        from modules.payroll.google_sheets import find_employee_for_telegram_user

        employee = find_employee_for_telegram_user(user)
        if employee and employee.get("full_name"):
            return employee["full_name"]
    except Exception:
        logging.exception("Не удалось получить ФИО сотрудника из справочника ЗП")

    # Fallback по локальному payroll_config.py, чтобы не зависеть полностью от БД.
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


async def send_chz_photo_to_topic(context: ContextTypes.DEFAULT_TYPE, user, photo_file_id):
    if not RETURN_CHZ_CHAT_ID:
        return "RETURN_CHZ_CHAT_ID не настроен."

    common_kwargs = {
        "chat_id": int(RETURN_CHZ_CHAT_ID),
        "photo": photo_file_id,
        "caption": (
            "Фото маркировки «Честный знак» по возврату\n"
            f"Сотрудник: {get_employee_full_name_for_user(user)}"
        ),
    }

    if RETURN_CHZ_TOPIC_ID:
        common_kwargs["message_thread_id"] = int(RETURN_CHZ_TOPIC_ID)

    await context.bot.send_photo(**common_kwargs)
    return "Фото ЧЗ отправлено в отдельную тему ✅"


async def send_item_chz_photo_to_topic(context: ContextTypes.DEFAULT_TYPE, user, photo_file_id):
    if not RETURN_CHZ_CHAT_ID:
        return "RETURN_CHZ_CHAT_ID не настроен."

    product_name = get_current_item_product_name(context)
    current_item = get_current_item(context)
    size = current_item.get("size", "")
    condition_key = current_item.get("condition_key", "")
    condition_label = RETURN_CONDITIONS.get(condition_key, {}).get("label", "")
    counterparty = context.user_data.get("return_counterparty", "")
    track_number = context.user_data.get("return_track_number", "")

    caption_lines = [
        "Фото маркировки «Честный знак» по товару в возврате",
        f"Тип возврата: {get_return_type_label(context)}",
        f"Сотрудник: {get_employee_full_name_for_user(user)}",
        f"ФИО контрагента: {counterparty}",
    ]

    if is_cdek_return(context) and track_number:
        caption_lines.append(f"Трек-номер: {track_number}")

    caption_lines.append(f"Товар: {product_name}")
    if size:
        caption_lines.append(f"Размер: {size}")
    if condition_label:
        caption_lines.append(f"Состояние: {condition_label}")
    if current_item.get("chz_code_short"):
        caption_lines.append(f"Короткий код ЧЗ: {current_item['chz_code_short']}")

    common_kwargs = {
        "chat_id": int(RETURN_CHZ_CHAT_ID),
        "photo": photo_file_id,
        "caption": "\n".join(caption_lines),
    }

    if RETURN_CHZ_TOPIC_ID:
        common_kwargs["message_thread_id"] = int(RETURN_CHZ_TOPIC_ID)

    await context.bot.send_photo(**common_kwargs)
    return "Фото ЧЗ по товару отправлено в отдельную тему ✅"


def parse_positive_number(text):
    text = text.strip()

    if not text.isdigit():
        return None

    value = int(text)

    if value <= 0:
        return None

    return value


def get_return_items(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.setdefault("return_items", [])


def get_current_item(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.setdefault("return_current_item", {})


def clear_current_item(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["return_current_item"] = {}


def clear_current_item_chz(current_item):
    for field in ("chz_photo_file_id", "chz_status", "chz_code_full", "chz_code_short"):
        current_item.pop(field, None)


def current_item_number(context: ContextTypes.DEFAULT_TYPE):
    return len(get_return_items(context)) + 1


def total_items_count(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("return_items_count", 0)


def get_current_item_product_name(context: ContextTypes.DEFAULT_TYPE):
    current_item = get_current_item(context)

    category_id = current_item.get("category_id")
    product_id = current_item.get("product_id")

    if not category_id or not product_id:
        return ""

    return CATEGORIES[category_id]["products"][product_id]


def append_current_item(context: ContextTypes.DEFAULT_TYPE):
    current_item = get_current_item(context)

    category_id = current_item.get("category_id")
    model_id = current_item.get("model_id")
    product_id = current_item.get("product_id")
    size = current_item.get("size")
    condition_key = current_item.get("condition_key")
    extra_photo_file_id = current_item.get("extra_photo_file_id")
    condition_comment = current_item.get("condition_comment", "")
    chz_photo_file_id = current_item.get("chz_photo_file_id", "")
    chz_status = current_item.get("chz_status", "")
    chz_code_full = current_item.get("chz_code_full", "")
    chz_code_short = current_item.get("chz_code_short", "")

    if not category_id or not model_id or not product_id or not size or not condition_key:
        raise RuntimeError("Не все данные товара заполнены.")

    condition = RETURN_CONDITIONS[condition_key]

    if condition_key == "normal" and not chz_status:
        raise RuntimeError("Не указана информация по Честному знаку товара.")

    if condition_key != "normal":
        chz_status = ""
        chz_photo_file_id = ""
        chz_code_full = ""
        chz_code_short = ""

    if condition.get("needs_photo") and not extra_photo_file_id:
        raise RuntimeError("Для выбранного состояния нужно фото переупакованного возврата.")

    if condition.get("needs_comment") and not condition_comment:
        raise RuntimeError("Для выбранного состояния нужен комментарий.")

    items = get_return_items(context)
    items.append(
        {
            "category_id": category_id,
            "model_id": model_id,
            "product_id": product_id,
            "size": size,
            "chz_status": chz_status,
            "chz_photo_file_id": chz_photo_file_id,
            "chz_code_full": chz_code_full,
            "chz_code_short": chz_code_short,
            "condition_key": condition_key,
            "condition_label": condition["label"],
            "condition_comment": condition_comment,
            "extra_photo_file_id": extra_photo_file_id,
        }
    )

    clear_current_item(context)


async def send_or_edit_text(target, text, reply_markup=None):
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=reply_markup)
    else:
        await target.reply_text(text, reply_markup=reply_markup)


async def ask_next_item_or_finish(target, context: ContextTypes.DEFAULT_TYPE, user):
    items = get_return_items(context)
    total = total_items_count(context)

    if len(items) < total:
        next_item_no = len(items) + 1

        step_text = "Шаг 5/6" if is_cdek_return(context) else "Шаг 4/5"

        await send_or_edit_text(
            target,
            (
                f"{step_text}. Товар {next_item_no} из {total}.\n\n"
                "Выберите группу товара:"
            ),
            reply_markup=build_return_category_keyboard(),
        )

        return RET_ITEM_CATEGORY

    summary_text = format_return_summary(context, user)

    try:
        send_status, telegram_data = await send_return_to_topic(
            context=context,
            caption=summary_text,
        )
        if telegram_data:
            record_id = create_return_record(
                {
                    "return_type": get_return_type(context),
                    "employee_name": get_employee_full_name_for_user(user),
                    "employee_user_id": user.id,
                    "counterparty": context.user_data.get("return_counterparty", ""),
                    "track_number": context.user_data.get("return_track_number", ""),
                    "label_status": context.user_data.get("return_label_status", ""),
                    "items": list(get_return_items(context)),
                    "photo_ids": telegram_data.get("photo_ids", []),
                    "chat_id": telegram_data.get("chat_id", ""),
                    "thread_id": telegram_data.get("thread_id", ""),
                    "message_ids": telegram_data.get("message_ids", []),
                }
            )
            send_status += f"\nЗапись возврата сохранена: #{record_id}"
    except Exception as error:
        logging.exception("Не удалось отправить возврат в тему чата")
        send_status = f"Не удалось отправить сообщение в тему чата ⚠️\nОшибка: {error}"

    await send_or_edit_text(
        target,
        (
            "Возврат оформлен ✅\n\n"
            f"{summary_text}\n\n"
            f"{send_status}\n\n"
            "Выберите следующее действие:"
        ),
        reply_markup=build_main_menu_keyboard(),
    )

    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ОТМЕНА
# ============================================================

async def return_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    text = "Оформление возврата отменено. Данные не сохранены и не отправлены."

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            text + "\n\nГлавное меню:",
            reply_markup=build_main_menu_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=build_main_menu_keyboard(),
        )

    return ConversationHandler.END


# ============================================================
# СТАРТ ВОЗВРАТА
# ============================================================

async def return_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    if query.data.endswith(":showroom"):
        context.user_data["return_type"] = "showroom"
        await query.edit_message_text(
            "↩️ Возврат из шоу-рума\n\n"
            "Шаг 1/5. Отправьте фото этикетки с информацией о возврате "
            "или нажмите кнопку «Этикетки нет».",
            reply_markup=build_showroom_label_photo_keyboard(),
        )
        return RET_PHOTO

    context.user_data["return_type"] = "cdek"

    await query.edit_message_text(
        "↩️ Возврат СДЭК\n\n"
        "Шаг 1/6. Отправьте фото накладной.",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_PHOTO


async def return_back_from_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "Главное меню:",
        reply_markup=build_main_menu_keyboard(),
    )

    return ConversationHandler.END


async def invoice_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_cdek_return(context):
        if not update.message.photo:
            await update.message.reply_text(
                "Пожалуйста, отправьте именно фото этикетки "
                "или нажмите кнопку «Этикетки нет».",
                reply_markup=build_showroom_label_photo_keyboard(),
            )
            return RET_PHOTO

        context.user_data["return_label_photo_file_id"] = update.message.photo[-1].file_id
        context.user_data["return_label_status"] = "Фото этикетки приложено"

        await update.message.reply_text(
            "Шаг 2/5. Введите ФИО контрагента:",
            reply_markup=build_return_nav_keyboard(),
        )

        return RET_COUNTERPARTY

    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, отправьте именно фото накладной.",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_PHOTO

    context.user_data["return_invoice_photo_file_id"] = update.message.photo[-1].file_id

    await update.message.reply_text(
        "Шаг 2/6. Введите ФИО контрагента:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


async def showroom_label_missing_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["return_label_status"] = "Этикетки нет"
    context.user_data.pop("return_label_photo_file_id", None)

    await query.edit_message_text(
        "Этикетка отмечена как отсутствующая.\n\n"
        "Шаг 2/5. Введите ФИО контрагента:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


async def back_to_showroom_label_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_label_photo_file_id", None)
    context.user_data.pop("return_label_status", None)

    await query.edit_message_text(
        "Шаг 1/5. Отправьте фото этикетки с информацией о возврате "
        "или нажмите кнопку «Этикетки нет»:",
        reply_markup=build_showroom_label_photo_keyboard(),
    )

    return RET_PHOTO


async def back_to_invoice_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_invoice_photo_file_id", None)
    context.user_data.pop("return_chz_photo_file_id", None)
    context.user_data.pop("return_chz_status", None)

    await query.edit_message_text(
        "Шаг 1/6. Отправьте фото накладной заново:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_PHOTO


async def chz_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, отправьте именно фото маркировки «Честный знак» "
            "или нажмите кнопку «ЧЗ нет».",
            reply_markup=build_chz_photo_keyboard(),
        )
        return RET_CHZ_PHOTO

    chz_photo_file_id = update.message.photo[-1].file_id
    context.user_data["return_chz_photo_file_id"] = chz_photo_file_id
    context.user_data["return_chz_status"] = "Фото отправлено"

    try:
        chz_status = await send_chz_photo_to_topic(
            context=context,
            user=update.effective_user,
            photo_file_id=chz_photo_file_id,
        )
    except Exception as error:
        logging.exception("Не удалось отправить фото ЧЗ в отдельную тему")
        chz_status = f"Фото ЧЗ сохранено, но не отправлено в отдельную тему ⚠️\nОшибка: {error}"

    await update.message.reply_text(
        f"{chz_status}\n\nШаг 3/7. Введите ФИО контрагента:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


async def chz_missing_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["return_chz_status"] = "ЧЗ нет"
    context.user_data.pop("return_chz_photo_file_id", None)

    await query.edit_message_text(
        "ЧЗ отмечен как отсутствующий.\n\n"
        "Шаг 3/7. Введите ФИО контрагента:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


async def back_to_chz_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_chz_photo_file_id", None)
    context.user_data.pop("return_chz_status", None)

    await query.edit_message_text(
        "Шаг 2/7. Отправьте фото маркировки «Честный знак» "
        "или нажмите кнопку «ЧЗ нет»:",
        reply_markup=build_chz_photo_keyboard(),
    )

    return RET_CHZ_PHOTO


async def back_from_counterparty_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_cdek_return(context):
        return await back_to_invoice_photo(update, context)

    return await back_to_showroom_label_photo(update, context)


# ============================================================
# ФИО
# ============================================================

async def counterparty_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counterparty = update.message.text.strip()

    if not counterparty:
        await update.message.reply_text(
            "ФИО не должно быть пустым. Введите ФИО контрагента:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_COUNTERPARTY

    context.user_data["return_counterparty"] = counterparty

    if is_cdek_return(context):
        await update.message.reply_text(
            "Шаг 3/6. Введите трек-номер числом:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_TRACK_NUMBER

    await update.message.reply_text(
        "Шаг 3/5. Введите количество вещей в возврате:",
        reply_markup=build_return_nav_keyboard(),
    )
    return RET_ITEMS_COUNT


async def back_to_counterparty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_counterparty", None)

    await query.edit_message_text(
        "Шаг 2/6. Введите ФИО контрагента заново:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


# ============================================================
# ТРЕК-НОМЕР
# ============================================================

async def track_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_number = update.message.text.strip()

    if not track_number.isdigit():
        await update.message.reply_text(
            "Трек-номер должен содержать только цифры. Введите ещё раз:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_TRACK_NUMBER

    context.user_data["return_track_number"] = track_number

    await update.message.reply_text(
        "Шаг 4/6. Введите количество товаров в возврате:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_ITEMS_COUNT


async def back_to_track_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_track_number", None)

    await query.edit_message_text(
        "Шаг 3/6. Введите трек-номер заново:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_TRACK_NUMBER


async def back_from_items_count_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_cdek_return(context):
        return await back_to_track_number(update, context)

    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_items_count", None)
    context.user_data.pop("return_items", None)
    clear_current_item(context)

    await query.edit_message_text(
        "Шаг 2/5. Введите ФИО контрагента заново:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_COUNTERPARTY


# ============================================================
# КОЛИЧЕСТВО ТОВАРОВ
# ============================================================

async def items_count_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = parse_positive_number(update.message.text)

    if value is None:
        await update.message.reply_text(
            "Количество товаров должно быть целым числом больше 0. Введите ещё раз:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_ITEMS_COUNT

    if value > 30:
        await update.message.reply_text(
            "Для теста максимум 30 товаров в одном возврате. Введите число от 1 до 30:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_ITEMS_COUNT

    context.user_data["return_items_count"] = value
    context.user_data["return_items"] = []
    clear_current_item(context)

    step_text = "Шаг 5/6" if is_cdek_return(context) else "Шаг 4/5"

    await update.message.reply_text(
        f"{step_text}. Товар 1 из {value}.\n\n"
        "Выберите группу товара:",
        reply_markup=build_return_category_keyboard(),
    )

    return RET_ITEM_CATEGORY


async def back_to_items_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop("return_items_count", None)
    context.user_data.pop("return_items", None)
    clear_current_item(context)

    await query.edit_message_text(
        "Шаг 4/6. Введите количество товаров в возврате заново:"
        if is_cdek_return(context)
        else "Шаг 3/5. Введите количество товаров в возврате заново:",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_ITEMS_COUNT


# ============================================================
# ВЫБОР ТОВАРОВ
# ============================================================

async def return_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    category_id = query.data.replace("retcat:", "")

    if category_id not in CATEGORIES:
        await query.edit_message_text(
            "Такой группы нет. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    current_item = get_current_item(context)
    current_item.clear()
    current_item["category_id"] = category_id

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"Группа: {CATEGORIES[category_id]['name']}\n\n"
        "Выберите модель:",
        reply_markup=build_return_models_keyboard(category_id),
    )

    return RET_ITEM_MODEL


async def back_from_item_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    items = get_return_items(context)

    if not items:
        context.user_data.pop("return_items_count", None)
        clear_current_item(context)

        await query.edit_message_text(
            "Шаг 4/6. Введите количество товаров в возврате заново:",
            reply_markup=build_return_nav_keyboard(),
        )

        return RET_ITEMS_COUNT

    items.pop()
    clear_current_item(context)

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"{'Шаг 5/6' if is_cdek_return(context) else 'Шаг 4/5'}. "
        f"Товар {item_no} из {total}.\n\n"
        "Выберите группу товара заново:",
        reply_markup=build_return_category_keyboard(),
    )

    return RET_ITEM_CATEGORY


async def return_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    model_id = query.data.replace("retmodel:", "")

    current_item = get_current_item(context)
    category_id = current_item.get("category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text(
            "Группа не выбрана. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    models = CATEGORIES[category_id]["models"]

    if model_id not in models:
        await query.edit_message_text(
            "Такой модели нет. Выберите модель:",
            reply_markup=build_return_models_keyboard(category_id),
        )
        return RET_ITEM_MODEL

    current_item["model_id"] = model_id

    variants = models[model_id]["variants"]

    if len(variants) == 1:
        variant_data = list(variants.values())[0]
        product_id = variant_data["id"]

        current_item["product_id"] = product_id

        item_no = current_item_number(context)
        total = total_items_count(context)

        await query.edit_message_text(
            f"Товар {item_no} из {total}\n"
            f"Товар: {variant_data['name']}\n\n"
            "Выберите размер:",
            reply_markup=build_return_sizes_keyboard(),
        )

        return RET_ITEM_SIZE

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"Модель: {models[model_id]['name']}\n\n"
        "Выберите цвет / вариант:",
        reply_markup=build_return_product_colors_keyboard(category_id, model_id),
    )

    return RET_ITEM_PRODUCT


async def back_to_return_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    clear_current_item(context)

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}.\n\n"
        "Выберите группу товара заново:",
        reply_markup=build_return_category_keyboard(),
    )

    return RET_ITEM_CATEGORY


async def return_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.replace("retprod:", "")

    current_item = get_current_item(context)
    category_id = current_item.get("category_id")
    model_id = current_item.get("model_id")

    if not category_id or not model_id:
        await query.edit_message_text(
            "Модель не выбрана. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    products = CATEGORIES[category_id]["products"]

    if product_id not in products:
        await query.edit_message_text(
            "Такого товара нет. Выберите цвет / вариант:",
            reply_markup=build_return_product_colors_keyboard(category_id, model_id),
        )
        return RET_ITEM_PRODUCT

    current_item["product_id"] = product_id
    clear_current_item_chz(current_item)

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"Товар: {products[product_id]}\n\n"
        "Выберите размер:",
        reply_markup=build_return_sizes_keyboard(),
    )

    return RET_ITEM_SIZE


async def back_to_return_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    category_id = current_item.get("category_id")

    if not category_id or category_id not in CATEGORIES:
        await query.edit_message_text(
            "Группа не выбрана. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    current_item.pop("model_id", None)
    current_item.pop("product_id", None)

    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"Группа: {CATEGORIES[category_id]['name']}\n\n"
        "Выберите модель заново:",
        reply_markup=build_return_models_keyboard(category_id),
    )

    return RET_ITEM_MODEL


async def item_chz_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, отправьте именно фото маркировки «Честный знак» "
            "или нажмите кнопку «ЧЗ нет».",
            reply_markup=build_chz_photo_keyboard(),
        )
        return RET_ITEM_CHZ_PHOTO

    current_item = get_current_item(context)
    chz_photo_file_id = update.message.photo[-1].file_id
    current_item["chz_photo_file_id"] = chz_photo_file_id
    current_item["chz_status"] = "Фото сохранено, ожидается сканирование"
    current_item.pop("chz_code_full", None)
    current_item.pop("chz_code_short", None)

    await update.message.reply_text(
        "Фото ЧЗ сохранено ✅\n\n"
        "Теперь отсканируйте DataMatrix. Бот сохранит полный код и отправит "
        "в тему его короткую 31-символьную версию.",
        reply_markup=build_chz_scan_keyboard(),
    )
    return RET_ITEM_CHZ_SCAN


async def item_chz_scan_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_code = normalize_chz_text(update.message.text).replace(" ", "")
    try:
        short_code = extract_short_marking_code(raw_code)
    except DuplicateChzError as error:
        await update.message.reply_text(
            f"Не удалось прочитать код ЧЗ: {error}\n\n"
            "Повторно отсканируйте DataMatrix или нажмите «ЧЗ нет».",
            reply_markup=build_chz_scan_keyboard(),
        )
        return RET_ITEM_CHZ_SCAN

    current_item = get_current_item(context)
    chz_photo_file_id = current_item.get("chz_photo_file_id")
    if not chz_photo_file_id:
        await update.message.reply_text(
            "Фото маркировки не сохранилось. Сначала отправьте фото ЧЗ.",
            reply_markup=build_chz_photo_keyboard(),
        )
        return RET_ITEM_CHZ_PHOTO

    current_item["chz_code_full"] = raw_code
    current_item["chz_code_short"] = short_code
    current_item["chz_status"] = f"Код отсканирован: {short_code}"

    try:
        chz_status = await send_item_chz_photo_to_topic(
            context=context,
            user=update.effective_user,
            photo_file_id=chz_photo_file_id,
        )
    except Exception as error:
        logging.exception("Не удалось отправить фото ЧЗ по товару в отдельную тему")
        chz_status = f"Фото ЧЗ сохранено, но не отправлено в отдельную тему ⚠️\nОшибка: {error}"

    try:
        append_current_item(context)
    except Exception as error:
        logging.exception("Не удалось добавить товар в возврат после ЧЗ")
        await update.message.reply_text(
            f"Не удалось добавить товар ⚠️\nОшибка: {error}",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    await update.message.reply_text(
        f"DataMatrix принят ✅\nКороткий код: {short_code}\n\n{chz_status}"
    )
    return await ask_next_item_or_finish(update.message, context, update.effective_user)


async def back_to_item_chz_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    clear_current_item_chz(current_item)
    await query.edit_message_text(
        "Отправьте фото маркировки «Честный знак» по этому товару "
        "или нажмите кнопку «ЧЗ нет»: так сотрудник сможет проверить код визуально.",
        reply_markup=build_chz_photo_keyboard(),
    )
    return RET_ITEM_CHZ_PHOTO


async def item_chz_missing_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    current_item["chz_status"] = "ЧЗ нет"
    current_item.pop("chz_photo_file_id", None)
    current_item.pop("chz_code_full", None)
    current_item.pop("chz_code_short", None)

    try:
        append_current_item(context)
    except Exception as error:
        logging.exception("Не удалось добавить товар в возврат после отметки ЧЗ нет")
        await query.edit_message_text(
            f"Не удалось добавить товар ⚠️\nОшибка: {error}",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    return await ask_next_item_or_finish(query, context, query.from_user)


async def return_size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    size = query.data.replace("retsize:", "")

    if size not in SIZES:
        await query.edit_message_text(
            "Такого размера нет. Выберите размер:",
            reply_markup=build_return_sizes_keyboard(),
        )
        return RET_ITEM_SIZE

    current_item = get_current_item(context)
    category_id = current_item.get("category_id")
    model_id = current_item.get("model_id")
    product_id = current_item.get("product_id")

    if not category_id or not model_id or not product_id:
        await query.edit_message_text(
            "Данные товара потерялись. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    current_item["size"] = size

    product_name = CATEGORIES[category_id]["products"][product_id]
    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"{product_name} — размер {size}\n\n"
        "Выберите состояние товара:",
        reply_markup=build_return_condition_keyboard(),
    )

    return RET_ITEM_CONDITION


async def back_to_return_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    category_id = current_item.get("category_id")
    model_id = current_item.get("model_id")

    if not category_id or not model_id:
        await query.edit_message_text(
            "Модель не выбрана. Выберите группу:",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    current_item.pop("product_id", None)
    clear_current_item_chz(current_item)

    variants = CATEGORIES[category_id]["models"][model_id]["variants"]

    item_no = current_item_number(context)
    total = total_items_count(context)

    if len(variants) == 1:
        current_item.pop("model_id", None)

        await query.edit_message_text(
            f"Товар {item_no} из {total}\n"
            f"Группа: {CATEGORIES[category_id]['name']}\n\n"
            "Выберите модель заново:",
            reply_markup=build_return_models_keyboard(category_id),
        )

        return RET_ITEM_MODEL

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n\n"
        "Выберите цвет / вариант заново:",
        reply_markup=build_return_product_colors_keyboard(category_id, model_id),
    )

    return RET_ITEM_PRODUCT


# ============================================================
# СОСТОЯНИЕ, ДОП. ФОТО И КОММЕНТАРИЙ К ТОВАРУ
# ============================================================

async def return_condition_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    condition_key = query.data.replace("retcond:", "")

    if condition_key not in RETURN_CONDITIONS:
        await query.edit_message_text(
            "Такого состояния нет. Выберите состояние товара:",
            reply_markup=build_return_condition_keyboard(),
        )
        return RET_ITEM_CONDITION

    current_item = get_current_item(context)
    current_item["condition_key"] = condition_key
    clear_current_item_chz(current_item)

    condition = RETURN_CONDITIONS[condition_key]

    product_name = get_current_item_product_name(context)
    size = current_item.get("size", "")
    item_no = current_item_number(context)
    total = total_items_count(context)

    # ЧЗ запрашиваем только для товаров со статусом «норм».
    if condition_key == "normal":
        await query.edit_message_text(
            f"Товар {item_no} из {total}\n"
            f"{product_name} — размер {size}\n"
            f"Состояние: {condition['label']}\n\n"
            "Отправьте фото маркировки «Честный знак» по этому товару "
            "или нажмите кнопку «ЧЗ нет». После фото бот попросит отсканировать DataMatrix:",
            reply_markup=build_chz_photo_keyboard(),
        )
        return RET_ITEM_CHZ_PHOTO

    if not condition["needs_photo"]:
        try:
            append_current_item(context)
        except Exception as error:
            logging.exception("Не удалось добавить товар в возврат")
            await query.edit_message_text(
                f"Не удалось добавить товар ⚠️\nОшибка: {error}",
                reply_markup=build_return_category_keyboard(),
            )
            return RET_ITEM_CATEGORY

        return await ask_next_item_or_finish(query, context, query.from_user)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"{product_name} — размер {size}\n"
        f"Состояние: {condition['label']}\n\n"
        f"{condition['photo_prompt']}",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_ITEM_EXTRA_PHOTO


async def back_to_return_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    current_item.pop("size", None)
    current_item.pop("condition_key", None)
    current_item.pop("extra_photo_file_id", None)
    current_item.pop("condition_comment", None)
    clear_current_item_chz(current_item)

    product_name = get_current_item_product_name(context)
    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"Товар: {product_name}\n\n"
        "Выберите размер заново:",
        reply_markup=build_return_sizes_keyboard(),
    )

    return RET_ITEM_SIZE


async def extra_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, отправьте именно фото переупакованного возврата.",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_ITEM_EXTRA_PHOTO

    current_item = get_current_item(context)
    current_item["extra_photo_file_id"] = update.message.photo[-1].file_id

    condition_key = current_item.get("condition_key")
    condition = RETURN_CONDITIONS.get(condition_key, {})

    product_name = get_current_item_product_name(context)
    size = current_item.get("size", "")
    item_no = current_item_number(context)
    total = total_items_count(context)

    await update.message.reply_text(
        f"Товар {item_no} из {total}\n"
        f"{product_name} — размер {size}\n"
        f"Состояние: {condition.get('label', '')}\n\n"
        f"{condition.get('comment_prompt', 'Введите комментарий к товару:')}",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_ITEM_CONDITION_COMMENT


async def back_to_return_condition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    current_item.pop("condition_key", None)
    current_item.pop("extra_photo_file_id", None)
    current_item.pop("condition_comment", None)
    clear_current_item_chz(current_item)

    product_name = get_current_item_product_name(context)
    size = current_item.get("size", "")
    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"{product_name} — размер {size}\n\n"
        "Выберите состояние товара заново:",
        reply_markup=build_return_condition_keyboard(),
    )

    return RET_ITEM_CONDITION


async def condition_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()

    if not comment:
        await update.message.reply_text(
            "Комментарий не должен быть пустым. Введите комментарий:",
            reply_markup=build_return_nav_keyboard(),
        )
        return RET_ITEM_CONDITION_COMMENT

    current_item = get_current_item(context)
    current_item["condition_comment"] = comment

    try:
        append_current_item(context)
    except Exception as error:
        logging.exception("Не удалось добавить товар в возврат")
        await update.message.reply_text(
            f"Не удалось добавить товар ⚠️\nОшибка: {error}",
            reply_markup=build_return_category_keyboard(),
        )
        return RET_ITEM_CATEGORY

    return await ask_next_item_or_finish(update.message, context, update.effective_user)


async def back_to_extra_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_item = get_current_item(context)
    current_item.pop("extra_photo_file_id", None)
    current_item.pop("condition_comment", None)

    condition_key = current_item.get("condition_key")
    condition = RETURN_CONDITIONS.get(condition_key, {})

    product_name = get_current_item_product_name(context)
    size = current_item.get("size", "")
    item_no = current_item_number(context)
    total = total_items_count(context)

    await query.edit_message_text(
        f"Товар {item_no} из {total}\n"
        f"{product_name} — размер {size}\n"
        f"Состояние: {condition.get('label', '')}\n\n"
        f"{condition.get('photo_prompt', 'Отправьте фото переупакованного возврата.')}",
        reply_markup=build_return_nav_keyboard(),
    )

    return RET_ITEM_EXTRA_PHOTO


# ============================================================
# ФИНАЛЬНЫЙ ТЕКСТ И ОТПРАВКА В ТЕМУ
# ============================================================

def get_required_mentions(items):
    mentions = []

    for item in items:
        condition_key = item.get("condition_key")
        condition = RETURN_CONDITIONS.get(condition_key, {})

        for mention_type in condition.get("mentions", []):
            if mention_type == "warehouse" and WAREHOUSE_MANAGER_MENTION not in mentions:
                mentions.append(WAREHOUSE_MANAGER_MENTION)

            if mention_type == "support" and SUPPORT_MANAGER_MENTION not in mentions:
                mentions.append(SUPPORT_MANAGER_MENTION)

    return mentions


def format_return_summary(context: ContextTypes.DEFAULT_TYPE, user):
    counterparty = context.user_data.get("return_counterparty", "")
    track_number = context.user_data.get("return_track_number", "")
    items = get_return_items(context)

    employee_full_name = get_employee_full_name_for_user(user)
    return_type_label = get_return_type_label(context)

    lines = [
        f"Тип возврата: {return_type_label}",
        f"Сотрудник: {employee_full_name}",
        f"ФИО контрагента: {counterparty}",
    ]

    if is_cdek_return(context):
        lines.append(f"Трек-номер: {track_number}")
    else:
        lines.append(f"Этикетка возврата: {context.user_data.get('return_label_status', 'не указано')}")

    lines.extend(
        [
            f"Количество товаров: {len(items)}",
            "",
            "Товары:",
        ]
    )

    for index, item in enumerate(items, start=1):
        category_id = item["category_id"]
        product_id = item["product_id"]
        size = item["size"]
        condition_label = item["condition_label"]
        condition_comment = item.get("condition_comment", "")
        chz_status = item.get("chz_status", "")

        product_name = CATEGORIES[category_id]["products"][product_id]

        if item.get("condition_key") == "normal":
            item_line = f"{index}. {product_name} — размер {size}, ЧЗ: {chz_status}, {condition_label}"
        else:
            item_line = f"{index}. {product_name} — размер {size}, {condition_label}"

        if condition_comment and condition_comment != "-":
            item_line += f", комментарий: {condition_comment}"

        lines.append(item_line)

    mentions = get_required_mentions(items)

    if mentions:
        lines.append("")
        lines.extend(mentions)

    return "\n".join(lines)


def format_return_record_button(record):
    counterparty = record.get("counterparty") or "без ФИО"
    if len(counterparty) > 28:
        counterparty = counterparty[:25] + "..."
    return f"#{record['id']} · {get_return_type_label_from_value(record.get('return_type'))} · {counterparty}"


def format_return_record_summary(record):
    items = record.get("items") or []
    lines = [
        f"Тип возврата: {get_return_type_label_from_value(record.get('return_type'))}",
        f"Сотрудник: {record.get('employee_name', '')}",
        f"ФИО контрагента: {record.get('counterparty', '')}",
    ]

    if record.get("return_type") == "cdek":
        lines.append(f"Трек-номер: {record.get('track_number', '')}")
    else:
        lines.append(f"Этикетка возврата: {record.get('label_status', 'не указано')}")

    lines.extend(["", f"Количество товаров: {len(items)}", "", "Товары:"])

    for index, item in enumerate(items, start=1):
        category_id = item["category_id"]
        product_id = item["product_id"]
        size = item["size"]
        condition_label = item["condition_label"]
        condition_comment = item.get("condition_comment", "")
        chz_status = item.get("chz_status", "")
        product_name = CATEGORIES[category_id]["products"][product_id]

        if item.get("condition_key") == "normal":
            item_line = f"{index}. {product_name} — размер {size}, ЧЗ: {chz_status}, {condition_label}"
        else:
            item_line = f"{index}. {product_name} — размер {size}, {condition_label}"

        if condition_comment and condition_comment != "-":
            item_line += f", комментарий: {condition_comment}"

        lines.append(item_line)

    mentions = get_required_mentions(items)
    if mentions:
        lines.append("")
        lines.extend(mentions)

    return "\n".join(lines)


def get_return_photo_file_ids(context: ContextTypes.DEFAULT_TYPE):
    photo_file_ids = []

    invoice_photo_file_id = context.user_data.get("return_invoice_photo_file_id")
    label_photo_file_id = context.user_data.get("return_label_photo_file_id")

    if invoice_photo_file_id:
        photo_file_ids.append(invoice_photo_file_id)

    if label_photo_file_id:
        photo_file_ids.append(label_photo_file_id)

    for item in get_return_items(context):
        extra_photo_file_id = item.get("extra_photo_file_id")

        if extra_photo_file_id:
            photo_file_ids.append(extra_photo_file_id)

    return photo_file_ids


def split_into_chunks(items, chunk_size):
    for index in range(0, len(items), chunk_size):
        yield items[index:index + chunk_size]


async def send_return_to_topic(context: ContextTypes.DEFAULT_TYPE, caption):
    if not GROUP_CHAT_ID:
        return "Тема чата не настроена: GROUP_CHAT_ID пустой.", {}

    chat_id = int(GROUP_CHAT_ID)
    message_thread_id = int(RETURNS_TOPIC_ID) if RETURNS_TOPIC_ID else None

    photo_file_ids = get_return_photo_file_ids(context)

    final_caption = caption

    if not photo_file_ids:
        message = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=final_caption,
        )
        return "Сообщение отправлено в тему чата ✅", {
            "chat_id": chat_id,
            "thread_id": message_thread_id or "",
            "message_ids": [message.message_id],
            "photo_ids": photo_file_ids,
        }

    if len(final_caption) > 1000:
        final_caption = final_caption[:950] + "\n\n...текст обрезан, товаров слишком много."

    common_kwargs = {
        "chat_id": chat_id,
    }

    if message_thread_id is not None:
        common_kwargs["message_thread_id"] = message_thread_id

    if len(photo_file_ids) == 1:
        message = await context.bot.send_photo(
            **common_kwargs,
            photo=photo_file_ids[0],
            caption=final_caption,
        )
        return "Сообщение отправлено в тему чата ✅", {
            "chat_id": chat_id,
            "thread_id": message_thread_id or "",
            "message_ids": [message.message_id],
            "photo_ids": photo_file_ids,
        }

    first_chunk = True
    message_ids = []

    for chunk in split_into_chunks(photo_file_ids, 10):
        media = []

        for index, photo_file_id in enumerate(chunk):
            if first_chunk and index == 0:
                media.append(InputMediaPhoto(media=photo_file_id, caption=final_caption))
            else:
                media.append(InputMediaPhoto(media=photo_file_id))

        messages = await context.bot.send_media_group(
            **common_kwargs,
            media=media,
        )
        message_ids.extend(message.message_id for message in messages)

        first_chunk = False

    return "Сообщение отправлено в тему чата ✅", {
        "chat_id": chat_id,
        "thread_id": message_thread_id or "",
        "message_ids": message_ids,
        "photo_ids": photo_file_ids,
    }


async def update_return_topic_message(context: ContextTypes.DEFAULT_TYPE, record):
    chat_id = record.get("chat_id")
    message_ids = record.get("message_ids") or []
    if not chat_id or not message_ids:
        return "сообщение в теме не найдено"

    text = format_return_record_summary(record)
    if record.get("photo_ids"):
        caption = text if len(text) <= 1000 else text[:950] + "\n\n...текст обрезан, товаров слишком много."
        await context.bot.edit_message_caption(
            chat_id=int(chat_id),
            message_id=int(message_ids[0]),
            caption=caption,
        )
    else:
        await context.bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_ids[0]),
            text=text,
        )
    return "сообщение в теме обновлено"


async def delete_return_topic_messages(context: ContextTypes.DEFAULT_TYPE, record):
    chat_id = record.get("chat_id")
    if not chat_id:
        return "сообщение в теме не найдено"

    deleted = 0
    for message_id in record.get("message_ids") or []:
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
            deleted += 1
        except Exception:
            logging.exception("Не удалось удалить сообщение возврата из темы")

    return f"удалено сообщений из темы: {deleted}"


def get_record_base_photo_ids(record):
    item_photo_ids = {
        item.get("extra_photo_file_id")
        for item in record.get("items") or []
        if item.get("extra_photo_file_id")
    }
    return [
        photo_id
        for photo_id in record.get("photo_ids") or []
        if photo_id and photo_id not in item_photo_ids
    ]


def rebuild_record_photo_ids(record):
    photo_ids = get_record_base_photo_ids(record)
    for item in record.get("items") or []:
        extra_photo_file_id = item.get("extra_photo_file_id")
        if extra_photo_file_id:
            photo_ids.append(extra_photo_file_id)
    return photo_ids


def make_default_return_item():
    category_id = next(iter(CATEGORIES))
    model_id = next(iter(CATEGORIES[category_id]["models"]))
    variant_data = next(iter(CATEGORIES[category_id]["models"][model_id]["variants"].values()))
    return {
        "category_id": category_id,
        "model_id": model_id,
        "product_id": variant_data["id"],
        "size": "ONE SIZE",
        "chz_status": "ЧЗ нет",
        "chz_photo_file_id": "",
        "condition_key": "normal",
        "condition_label": RETURN_CONDITIONS["normal"]["label"],
        "condition_comment": "",
        "extra_photo_file_id": "",
    }


def find_product_location(product_id):
    for category_id, category_data in CATEGORIES.items():
        for model_id, model_data in category_data["models"].items():
            for variant_data in model_data["variants"].values():
                if variant_data["id"] == product_id:
                    return category_id, model_id
    raise RuntimeError("Товар не найден в каталоге.")


async def finalize_admin_pending_item(context: ContextTypes.DEFAULT_TYPE, record_id):
    record = get_return_record(record_id)
    if not record:
        raise RuntimeError("Запись возврата не найдена.")

    pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
    if not pending_item:
        raise RuntimeError("Новая позиция не заполнена.")

    condition_key = pending_item.get("condition_key")
    condition = RETURN_CONDITIONS.get(condition_key, {})
    if condition_key == "normal" and not pending_item.get("chz_status"):
        raise RuntimeError("Не указана информация по Честному знаку.")
    if condition.get("needs_photo") and not pending_item.get("extra_photo_file_id"):
        raise RuntimeError("Для выбранного состояния нужно фото переупаковки.")
    if condition.get("needs_comment") and not pending_item.get("condition_comment"):
        raise RuntimeError("Для выбранного состояния нужен комментарий.")

    items = record.get("items") or []
    items.append(pending_item)
    record["items"] = items
    return await save_return_record_and_repost(context, record)


async def send_return_record_to_topic(context: ContextTypes.DEFAULT_TYPE, record):
    if not GROUP_CHAT_ID:
        raise RuntimeError("GROUP_CHAT_ID не настроен.")

    chat_id = int(GROUP_CHAT_ID)
    message_thread_id = int(RETURNS_TOPIC_ID) if RETURNS_TOPIC_ID else None
    photo_file_ids = rebuild_record_photo_ids(record)
    text = format_return_record_summary(record)

    common_kwargs = {"chat_id": chat_id}
    if message_thread_id is not None:
        common_kwargs["message_thread_id"] = message_thread_id

    if not photo_file_ids:
        message = await context.bot.send_message(**common_kwargs, text=text)
        return {
            "chat_id": chat_id,
            "thread_id": message_thread_id or "",
            "message_ids": [message.message_id],
            "photo_ids": photo_file_ids,
        }

    caption = text if len(text) <= 1000 else text[:950] + "\n\n...текст обрезан, товаров слишком много."

    if len(photo_file_ids) == 1:
        message = await context.bot.send_photo(**common_kwargs, photo=photo_file_ids[0], caption=caption)
        return {
            "chat_id": chat_id,
            "thread_id": message_thread_id or "",
            "message_ids": [message.message_id],
            "photo_ids": photo_file_ids,
        }

    message_ids = []
    first_chunk = True
    for chunk in split_into_chunks(photo_file_ids, 10):
        media = []
        for index, photo_file_id in enumerate(chunk):
            if first_chunk and index == 0:
                media.append(InputMediaPhoto(media=photo_file_id, caption=caption))
            else:
                media.append(InputMediaPhoto(media=photo_file_id))
        messages = await context.bot.send_media_group(**common_kwargs, media=media)
        message_ids.extend(message.message_id for message in messages)
        first_chunk = False

    return {
        "chat_id": chat_id,
        "thread_id": message_thread_id or "",
        "message_ids": message_ids,
        "photo_ids": photo_file_ids,
    }


async def save_return_record_and_repost(context: ContextTypes.DEFAULT_TYPE, record):
    await delete_return_topic_messages(context, record)
    telegram_data = await send_return_record_to_topic(context, record)
    return update_return_record(
        record["id"],
        return_type=record.get("return_type", ""),
        employee_name=record.get("employee_name", ""),
        counterparty=record.get("counterparty", ""),
        track_number=record.get("track_number", ""),
        label_status=record.get("label_status", ""),
        items=record.get("items") or [],
        photo_ids=telegram_data.get("photo_ids", []),
        chat_id=telegram_data.get("chat_id", ""),
        thread_id=telegram_data.get("thread_id", ""),
        message_ids=telegram_data.get("message_ids", []),
    )


# ============================================================
# УПРАВЛЕНИЕ СОХРАНЕННЫМИ ВОЗВРАТАМИ
# ============================================================

async def return_admin_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    try:
        records = get_recent_return_records(limit=10)
    except Exception as error:
        logging.exception("Не удалось получить список возвратов для изменения")
        await query.edit_message_text(
            f"Не удалось открыть список возвратов ⚠️\nОшибка: {error}",
            reply_markup=build_return_records_keyboard([], "edit"),
        )
        return ConversationHandler.END

    if not records:
        await query.edit_message_text(
            "Пока нет сохраненных возвратов.",
            reply_markup=build_return_records_keyboard([], "edit"),
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "Выберите возврат для изменения:",
        reply_markup=build_return_records_keyboard(records, "edit"),
    )
    return RET_ADMIN_SELECT


async def return_admin_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    try:
        records = get_recent_return_records(limit=10)
    except Exception as error:
        logging.exception("Не удалось получить список возвратов для удаления")
        await query.edit_message_text(
            f"Не удалось открыть список возвратов ⚠️\nОшибка: {error}",
            reply_markup=build_return_records_keyboard([], "delete"),
        )
        return ConversationHandler.END

    if not records:
        await query.edit_message_text(
            "Пока нет сохраненных возвратов.",
            reply_markup=build_return_records_keyboard([], "delete"),
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "Выберите возврат для удаления:",
        reply_markup=build_return_records_keyboard(records, "delete"),
    )
    return RET_ADMIN_SELECT


async def return_admin_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    record_id = query.data.rsplit(":", 1)[-1]
    record = get_return_record(record_id)
    if not record or record.get("status") == "deleted":
        await query.edit_message_text("Запись возврата не найдена.", reply_markup=build_return_records_keyboard([], "edit"))
        return ConversationHandler.END

    context.user_data["return_admin_record_id"] = record_id
    await query.edit_message_text(
        format_return_record_summary(record) + "\n\nЧто изменить?",
        reply_markup=build_return_admin_record_keyboard(record),
    )
    return RET_ADMIN_EDIT_FIELD


async def return_admin_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    record_id = query.data.rsplit(":", 1)[-1]
    record = get_return_record(record_id)
    if not record or record.get("status") == "deleted":
        await query.edit_message_text("Запись возврата не найдена.", reply_markup=build_return_records_keyboard([], "delete"))
        return ConversationHandler.END

    context.user_data["return_admin_record_id"] = record_id
    await query.edit_message_text(
        format_return_record_summary(record) + "\n\nУдалить эту запись и сообщения из темы?",
        reply_markup=build_return_delete_confirm_keyboard(record_id),
    )
    return RET_ADMIN_DELETE_CONFIRM


async def return_admin_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field = query.data.replace("retadminfield:", "")
    field_titles = {
        "employee_name": "сотрудник",
        "counterparty": "ФИО контрагента",
        "track_number": "трек-номер",
        "label_status": "статус этикетки",
    }
    if field not in field_titles:
        await query.answer("Неизвестное поле", show_alert=True)
        return RET_ADMIN_EDIT_FIELD

    context.user_data["return_admin_field"] = field
    context.user_data["return_admin_waiting_value"] = True
    await query.edit_message_text(
        f"Введите новое значение для поля «{field_titles[field]}»:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
    )
    return RET_ADMIN_EDIT_VALUE


async def return_admin_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("return_admin_waiting_value"):
        return

    value = update.message.text.strip()
    record_id = context.user_data.get("return_admin_record_id")
    field = context.user_data.get("return_admin_field")
    item_index = context.user_data.get("return_admin_item_index")

    if not value:
        await update.message.reply_text("Значение не должно быть пустым. Введите еще раз:")
        return RET_ADMIN_EDIT_VALUE

    try:
        record = get_return_record(record_id)
        if not record:
            raise RuntimeError("Запись возврата не найдена.")

        was_adding_item = bool(context.user_data.get("return_admin_adding_item"))
        if context.user_data.get("return_admin_adding_item") and field == "condition_comment":
            pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
            pending_item["condition_comment"] = value
            context.user_data["return_admin_pending_item"] = pending_item
            record = await finalize_admin_pending_item(context, record_id)
        elif field == "condition_comment":
            items = record.get("items") or []
            item_index = int(item_index)
            items[item_index]["condition_comment"] = value
            record["items"] = items
        else:
            record[field] = value

        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить запись возврата")
        await update.message.reply_text(
            f"Не удалось изменить запись ⚠️\nОшибка: {error}",
            reply_markup=build_return_records_keyboard(get_recent_return_records(limit=10), "edit"),
        )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        (
            f"Товар добавлен в возврат #{record_id} ✅\n"
            if was_adding_item
            else f"Запись возврата #{record_id} обновлена ✅\n"
        )
        + "Старое сообщение удалено, новая версия отправлена в тему.",
        reply_markup=build_return_admin_record_keyboard(record),
    )
    return ConversationHandler.END


async def return_admin_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (
        context.user_data.get("return_admin_waiting_photo")
        or context.user_data.get("return_admin_waiting_base_photo")
        or context.user_data.get("return_admin_waiting_add_chz_photo")
        or context.user_data.get("return_admin_waiting_add_extra_photo")
    ):
        return

    record_id = context.user_data.get("return_admin_record_id")

    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте именно фото.")
        return

    try:
        record = get_return_record(record_id)
        if not record:
            raise RuntimeError("Запись возврата не найдена.")

        was_adding_item = bool(context.user_data.get("return_admin_adding_item"))
        if context.user_data.get("return_admin_waiting_add_chz_photo"):
            pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
            pending_item["chz_status"] = "Фото отправлено"
            pending_item["chz_photo_file_id"] = update.message.photo[-1].file_id
            context.user_data["return_admin_pending_item"] = pending_item
            record = await finalize_admin_pending_item(context, record_id)
        elif context.user_data.get("return_admin_waiting_add_extra_photo"):
            pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
            pending_item["extra_photo_file_id"] = update.message.photo[-1].file_id
            context.user_data["return_admin_pending_item"] = pending_item
            condition = RETURN_CONDITIONS.get(pending_item.get("condition_key"), {})
            if condition.get("needs_comment"):
                context.user_data["return_admin_field"] = "condition_comment"
                context.user_data["return_admin_waiting_value"] = True
                context.user_data.pop("return_admin_waiting_add_extra_photo", None)
                await update.message.reply_text(
                    condition.get("comment_prompt", "Введите комментарий к товару:"),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
                )
                return
            record = await finalize_admin_pending_item(context, record_id)
        elif context.user_data.get("return_admin_waiting_base_photo"):
            item_photo_ids = [
                item.get("extra_photo_file_id")
                for item in record.get("items") or []
                if item.get("extra_photo_file_id")
            ]
            record["photo_ids"] = [update.message.photo[-1].file_id] + item_photo_ids
        else:
            item_index = int(context.user_data.get("return_admin_item_index"))
            items = record.get("items") or []
            items[item_index]["extra_photo_file_id"] = update.message.photo[-1].file_id
            record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить фото товара в возврате")
        await update.message.reply_text(f"Не удалось изменить фото ⚠️\nОшибка: {error}")
        return

    context.user_data.clear()
    await update.message.reply_text(
        (
            f"Товар добавлен в возврат #{record_id} ✅\n"
            if was_adding_item
            else f"Фото в возврате #{record_id} обновлено ✅\n"
        )
        + "Старое сообщение удалено, новая версия отправлена в тему.",
        reply_markup=build_return_admin_record_keyboard(record),
    )


async def return_admin_base_photo_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadminbasephoto:", "")
    context.user_data["return_admin_record_id"] = record_id
    context.user_data["return_admin_waiting_base_photo"] = True
    context.user_data.pop("return_admin_waiting_photo", None)
    await query.edit_message_text(
        "Отправьте новое фото накладной / этикетки возврата:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
    )


async def return_admin_items_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadminitems:", "")
    record = get_return_record(record_id)
    if not record:
        await query.edit_message_text("Запись возврата не найдена.", reply_markup=build_return_records_keyboard([], "edit"))
        return
    await query.edit_message_text("Выберите товар:", reply_markup=build_return_admin_items_keyboard(record))


async def return_admin_item_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index = query.data.split(":", 2)
    item_index = int(item_index)
    record = get_return_record(record_id)
    items = record.get("items") if record else []
    if not record or item_index >= len(items):
        await query.edit_message_text("Товар не найден.", reply_markup=build_return_records_keyboard([], "edit"))
        return

    item = items[item_index]
    product_name = CATEGORIES[item["category_id"]]["products"][item["product_id"]]
    text = (
        f"Возврат #{record_id}. Товар {item_index + 1}\n\n"
        f"Товар: {product_name}\n"
        f"Размер: {item.get('size', '')}\n"
        f"Состояние: {item.get('condition_label', '')}\n"
        f"ЧЗ: {item.get('chz_status', '') or '-'}\n"
        f"Комментарий: {item.get('condition_comment', '') or '-'}\n"
        f"Фото переупаковки: {'есть' if item.get('extra_photo_file_id') else 'нет'}"
    )
    await query.edit_message_text(text, reply_markup=build_return_admin_item_keyboard(record_id, item_index))


async def return_admin_item_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, field = query.data.split(":", 3)
    item_index = int(item_index)

    context.user_data["return_admin_record_id"] = record_id
    context.user_data["return_admin_item_index"] = item_index
    context.user_data.pop("return_admin_waiting_value", None)
    context.user_data.pop("return_admin_waiting_photo", None)

    if field == "product":
        await query.edit_message_text("Выберите группу товара:", reply_markup=build_admin_category_keyboard(record_id, item_index))
        return
    if field == "size":
        await query.edit_message_text("Выберите размер:", reply_markup=build_admin_sizes_keyboard(record_id, item_index))
        return
    if field == "condition":
        await query.edit_message_text("Выберите состояние:", reply_markup=build_admin_conditions_keyboard(record_id, item_index))
        return
    if field == "comment":
        context.user_data["return_admin_field"] = "condition_comment"
        context.user_data["return_admin_waiting_value"] = True
        await query.edit_message_text(
            "Введите новый комментарий к товару:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
        )
        return
    if field == "chz":
        await query.edit_message_text("Выберите статус ЧЗ:", reply_markup=build_admin_chz_keyboard(record_id, item_index))
        return
    if field == "photo":
        context.user_data["return_admin_waiting_photo"] = True
        await query.edit_message_text(
            "Отправьте новое фото переупакованного возврата:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
        )
        return

    await query.answer("Неизвестное поле", show_alert=True)


async def return_admin_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, category_id = query.data.split(":", 3)
    context.user_data["return_admin_category_id"] = category_id
    await query.edit_message_text(
        "Выберите модель:",
        reply_markup=build_admin_models_keyboard(record_id, int(item_index), category_id),
    )


async def return_admin_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, model_id = query.data.split(":", 3)
    category_id = context.user_data.get("return_admin_category_id")
    if not category_id or model_id not in CATEGORIES.get(category_id, {}).get("models", {}):
        for candidate_category_id, category_data in CATEGORIES.items():
            if model_id in category_data["models"]:
                category_id = candidate_category_id
                break
    if not category_id:
        await query.edit_message_text("Группа товара потерялась. Выберите товар заново.")
        return
    await query.edit_message_text(
        "Выберите цвет / вариант:",
        reply_markup=build_admin_products_keyboard(record_id, int(item_index), category_id, model_id),
    )


async def return_admin_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, product_id = query.data.split(":", 3)
    item_index = int(item_index)

    try:
        category_id, model_id = find_product_location(product_id)
        record = get_return_record(record_id)
        items = record.get("items") or []
        if item_index == len(items):
            pending_item = make_default_return_item()
            pending_item["category_id"] = category_id
            pending_item["model_id"] = model_id
            pending_item["product_id"] = product_id
            context.user_data["return_admin_record_id"] = record_id
            context.user_data["return_admin_item_index"] = item_index
            context.user_data["return_admin_adding_item"] = True
            context.user_data["return_admin_pending_item"] = pending_item
            await query.edit_message_text(
                "Выберите размер нового товара:",
                reply_markup=build_admin_sizes_keyboard(record_id, item_index),
            )
            return
        if item_index > len(items):
            raise RuntimeError("Товар не найден в возврате.")
        items[item_index]["category_id"] = category_id
        items[item_index]["model_id"] = model_id
        items[item_index]["product_id"] = product_id
        record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить товар в возврате")
        await query.edit_message_text(f"Не удалось изменить товар ⚠️\nОшибка: {error}")
        return

    await query.edit_message_text(
        "Товар обновлен ✅\nСтарое сообщение удалено, новая версия отправлена в тему.",
        reply_markup=build_return_admin_item_keyboard(record_id, item_index),
    )


async def return_admin_size_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, size = query.data.split(":", 3)
    item_index = int(item_index)
    if context.user_data.get("return_admin_adding_item") and item_index == int(context.user_data.get("return_admin_item_index", -1)):
        pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
        pending_item["size"] = size
        context.user_data["return_admin_pending_item"] = pending_item
        await query.edit_message_text(
            "Выберите состояние нового товара:",
            reply_markup=build_admin_conditions_keyboard(record_id, item_index),
        )
        return

    try:
        record = get_return_record(record_id)
        items = record.get("items") or []
        items[item_index]["size"] = size
        record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить размер в возврате")
        await query.edit_message_text(f"Не удалось изменить размер ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("Размер обновлен ✅", reply_markup=build_return_admin_item_keyboard(record_id, item_index))


async def return_admin_condition_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, condition_key = query.data.split(":", 3)
    item_index = int(item_index)
    if context.user_data.get("return_admin_adding_item") and item_index == int(context.user_data.get("return_admin_item_index", -1)):
        condition = RETURN_CONDITIONS[condition_key]
        pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
        pending_item["condition_key"] = condition_key
        pending_item["condition_label"] = condition["label"]
        pending_item["condition_comment"] = ""
        pending_item["extra_photo_file_id"] = ""

        if condition_key == "normal":
            pending_item["chz_status"] = ""
            pending_item["chz_photo_file_id"] = ""
            context.user_data["return_admin_pending_item"] = pending_item
            context.user_data["return_admin_waiting_add_chz_photo"] = True
            await query.edit_message_text(
                "Отправьте фото маркировки «Честный знак» по новому товару или нажмите «ЧЗ нет»:",
                reply_markup=build_admin_add_chz_keyboard(record_id),
            )
            return

        pending_item["chz_status"] = ""
        pending_item["chz_photo_file_id"] = ""
        context.user_data["return_admin_pending_item"] = pending_item
        if condition.get("needs_photo"):
            context.user_data["return_admin_waiting_add_extra_photo"] = True
            await query.edit_message_text(
                condition.get("photo_prompt", "Отправьте фото переупакованного возврата."),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="retadmin:cancel")]]),
            )
            return

        try:
            record = await finalize_admin_pending_item(context, record_id)
        except Exception as error:
            logging.exception("Не удалось добавить товар в возврат")
            await query.edit_message_text(f"Не удалось добавить товар ⚠️\nОшибка: {error}")
            return
        context.user_data.clear()
        await query.edit_message_text("Товар добавлен ✅", reply_markup=build_return_admin_items_keyboard(record))
        return

    try:
        record = get_return_record(record_id)
        items = record.get("items") or []
        condition = RETURN_CONDITIONS[condition_key]
        items[item_index]["condition_key"] = condition_key
        items[item_index]["condition_label"] = condition["label"]
        if condition_key != "normal":
            items[item_index]["chz_status"] = ""
            items[item_index]["chz_photo_file_id"] = ""
        record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить состояние в возврате")
        await query.edit_message_text(f"Не удалось изменить состояние ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("Состояние обновлено ✅", reply_markup=build_return_admin_item_keyboard(record_id, item_index))


async def return_admin_chz_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index, chz_status = query.data.split(":", 3)
    item_index = int(item_index)
    try:
        record = get_return_record(record_id)
        items = record.get("items") or []
        items[item_index]["chz_status"] = chz_status
        record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить ЧЗ в возврате")
        await query.edit_message_text(f"Не удалось изменить ЧЗ ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("ЧЗ обновлен ✅", reply_markup=build_return_admin_item_keyboard(record_id, item_index))


async def return_admin_add_chz_missing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadminaddchzmissing:", "")
    try:
        pending_item = dict(context.user_data.get("return_admin_pending_item") or {})
        pending_item["chz_status"] = "ЧЗ нет"
        pending_item["chz_photo_file_id"] = ""
        context.user_data["return_admin_pending_item"] = pending_item
        record = await finalize_admin_pending_item(context, record_id)
    except Exception as error:
        logging.exception("Не удалось добавить товар с отметкой ЧЗ нет")
        await query.edit_message_text(f"Не удалось добавить товар ⚠️\nОшибка: {error}")
        return

    context.user_data.clear()
    await query.edit_message_text("Товар добавлен ✅", reply_markup=build_return_admin_items_keyboard(record))


async def return_admin_item_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadminitemadd:", "")
    try:
        record = get_return_record(record_id)
        items = record.get("items") or []
        item_index = len(items)
    except Exception as error:
        logging.exception("Не удалось начать добавление товара в возврат")
        await query.edit_message_text(f"Не удалось начать добавление товара ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text(
        "Выберите группу нового товара:",
        reply_markup=build_admin_category_keyboard(record_id, item_index),
    )


async def return_admin_item_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index = query.data.split(":", 2)
    await query.edit_message_text(
        "Удалить этот товар из возврата?",
        reply_markup=build_admin_item_delete_keyboard(record_id, int(item_index)),
    )


async def return_admin_item_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, item_index = query.data.split(":", 2)
    item_index = int(item_index)
    try:
        record = get_return_record(record_id)
        items = record.get("items") or []
        if len(items) <= 1:
            raise RuntimeError("Нельзя удалить единственный товар. Удалите всю запись возврата.")
        items.pop(item_index)
        record["items"] = items
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось удалить товар из возврата")
        await query.edit_message_text(f"Не удалось удалить товар ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("Товар удален ✅", reply_markup=build_return_admin_items_keyboard(record))


async def return_admin_type_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadmintype:", "")
    await query.edit_message_text("Выберите тип возврата:", reply_markup=build_return_admin_type_keyboard(record_id))


async def return_admin_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, record_id, return_type = query.data.split(":", 2)
    try:
        record = get_return_record(record_id)
        record["return_type"] = return_type
        if return_type == "showroom":
            record["track_number"] = ""
            record["label_status"] = record.get("label_status") or "не указано"
        else:
            record["label_status"] = ""
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось изменить тип возврата")
        await query.edit_message_text(f"Не удалось изменить тип возврата ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("Тип возврата обновлен ✅", reply_markup=build_return_admin_record_keyboard(record))


async def return_admin_resend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    record_id = query.data.replace("retadminresend:", "")
    try:
        record = get_return_record(record_id)
        record = await save_return_record_and_repost(context, record)
    except Exception as error:
        logging.exception("Не удалось перевыгрузить возврат")
        await query.edit_message_text(f"Не удалось перевыгрузить возврат ⚠️\nОшибка: {error}")
        return
    await query.edit_message_text("Возврат перевыгружен ✅", reply_markup=build_return_admin_record_keyboard(record))


async def return_admin_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    record_id = query.data.rsplit(":", 1)[-1]
    try:
        record = mark_return_record_deleted(record_id)
        topic_status = await delete_return_topic_messages(context, record)
    except Exception as error:
        logging.exception("Не удалось удалить запись возврата")
        await query.edit_message_text(
            f"Не удалось удалить запись ⚠️\nОшибка: {error}",
            reply_markup=build_return_records_keyboard(get_recent_return_records(limit=10), "delete"),
        )
        return ConversationHandler.END

    context.user_data.clear()
    await query.edit_message_text(
        f"Запись возврата #{record_id} удалена ✅\n{topic_status}",
        reply_markup=build_return_records_keyboard(get_recent_return_records(limit=10), "delete"),
    )
    return ConversationHandler.END


async def return_admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Действие отменено.", reply_markup=build_return_records_keyboard(get_recent_return_records(limit=10), "edit"))
    return ConversationHandler.END


async def return_admin_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = str(query.data or "").strip()

    if data == "retadmin:edit":
        return await return_admin_edit_start(update, context)

    if data == "retadmin:delete":
        return await return_admin_delete_start(update, context)

    if data.startswith("retadmin:edit:"):
        query.data = data
        return await return_admin_edit_selected(update, context)

    if data.startswith("retadmin:delete:"):
        query.data = data
        return await return_admin_delete_selected(update, context)

    if data.startswith("retadminfield:"):
        return await return_admin_field_selected(update, context)

    if data.startswith("retadmindel:yes:"):
        return await return_admin_delete_confirmed(update, context)

    if data.startswith("retadminitems:"):
        return await return_admin_items_open(update, context)

    if data.startswith("retadminitemfield:"):
        return await return_admin_item_field_selected(update, context)

    if data.startswith("retadminitemdeleteyes:"):
        return await return_admin_item_delete_confirmed(update, context)

    if data.startswith("retadminitemdelete:"):
        return await return_admin_item_delete_selected(update, context)

    if data.startswith("retadminitemadd:"):
        return await return_admin_item_add(update, context)

    if data.startswith("retadminitem:"):
        return await return_admin_item_open(update, context)

    if data.startswith("retadmincat:"):
        return await return_admin_category_selected(update, context)

    if data.startswith("retadminmodel:"):
        return await return_admin_model_selected(update, context)

    if data.startswith("retadminprod:"):
        return await return_admin_product_selected(update, context)

    if data.startswith("retadminsizeset:"):
        return await return_admin_size_selected(update, context)

    if data.startswith("retadmincondset:"):
        return await return_admin_condition_selected(update, context)

    if data.startswith("retadminchzset:"):
        return await return_admin_chz_selected(update, context)

    if data.startswith("retadminaddchzmissing:"):
        return await return_admin_add_chz_missing(update, context)

    if data.startswith("retadmintypeset:"):
        return await return_admin_type_selected(update, context)

    if data.startswith("retadmintype:"):
        return await return_admin_type_open(update, context)

    if data.startswith("retadminresend:"):
        return await return_admin_resend(update, context)

    if data.startswith("retadminbasephoto:"):
        return await return_admin_base_photo_selected(update, context)

    await query.answer(f"Неизвестное действие по возврату: {data}", show_alert=True)
    logging.warning("Неизвестный callback возвратов: %s", data)


# ============================================================
# CONVERSATION HANDLER
# ============================================================

def get_returns_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(return_start, pattern=r"^menu:return(:cdek|:showroom)?$"),
        ],
        states={
            RET_PHOTO: [
                MessageHandler(filters.PHOTO, invoice_photo_received),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invoice_photo_received),
                CallbackQueryHandler(showroom_label_missing_selected, pattern=r"^ret:label_missing$"),
                CallbackQueryHandler(return_back_from_photo, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_COUNTERPARTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, counterparty_received),
                CallbackQueryHandler(back_from_counterparty_step, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_TRACK_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, track_number_received),
                CallbackQueryHandler(back_to_counterparty, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEMS_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, items_count_received),
                CallbackQueryHandler(back_from_items_count_step, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_CATEGORY: [
                CallbackQueryHandler(return_category_selected, pattern=r"^retcat:"),
                CallbackQueryHandler(back_from_item_category, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_MODEL: [
                CallbackQueryHandler(return_model_selected, pattern=r"^retmodel:"),
                CallbackQueryHandler(back_to_return_category, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_PRODUCT: [
                CallbackQueryHandler(return_product_selected, pattern=r"^retprod:"),
                CallbackQueryHandler(back_to_return_model, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_CHZ_PHOTO: [
                MessageHandler(filters.PHOTO, item_chz_photo_received),
                CallbackQueryHandler(item_chz_missing_selected, pattern=r"^ret:chz_missing$"),
                CallbackQueryHandler(back_to_return_condition, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_CHZ_SCAN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_chz_scan_received),
                CallbackQueryHandler(item_chz_missing_selected, pattern=r"^ret:chz_missing$"),
                CallbackQueryHandler(back_to_item_chz_photo, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_SIZE: [
                CallbackQueryHandler(return_size_selected, pattern=r"^retsize:"),
                CallbackQueryHandler(back_to_return_product, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_CONDITION: [
                CallbackQueryHandler(return_condition_selected, pattern=r"^retcond:"),
                CallbackQueryHandler(back_to_return_size, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_EXTRA_PHOTO: [
                MessageHandler(filters.PHOTO, extra_photo_received),
                CallbackQueryHandler(back_to_return_condition, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
            RET_ITEM_CONDITION_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, condition_comment_received),
                CallbackQueryHandler(back_to_extra_photo, pattern=r"^ret:back$"),
                CallbackQueryHandler(return_cancel, pattern=r"^ret:cancel$"),
            ],
        },
        fallbacks=[],
        allow_reentry=True,
    )


def get_returns_admin_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(return_admin_edit_start, pattern=r"^retadmin:edit$"),
            CallbackQueryHandler(return_admin_delete_start, pattern=r"^retadmin:delete$"),
        ],
        states={
            RET_ADMIN_SELECT: [
                CallbackQueryHandler(return_admin_edit_selected, pattern=r"^retadmin:edit:\\d+$"),
                CallbackQueryHandler(return_admin_delete_selected, pattern=r"^retadmin:delete:\\d+$"),
                CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$"),
            ],
            RET_ADMIN_EDIT_FIELD: [
                CallbackQueryHandler(return_admin_field_selected, pattern=r"^retadminfield:"),
                CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$"),
            ],
            RET_ADMIN_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, return_admin_value_received),
                CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$"),
            ],
            RET_ADMIN_DELETE_CONFIRM: [
                CallbackQueryHandler(return_admin_delete_confirmed, pattern=r"^retadmindel:yes:\\d+$"),
                CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$")],
        allow_reentry=True,
    )


def get_returns_admin_handlers():
    return [
        CallbackQueryHandler(return_admin_edit_start, pattern=r"^retadmin:edit$"),
        CallbackQueryHandler(return_admin_delete_start, pattern=r"^retadmin:delete$"),
        CallbackQueryHandler(return_admin_edit_selected, pattern=r"^retadmin:edit:\d+$"),
        CallbackQueryHandler(return_admin_delete_selected, pattern=r"^retadmin:delete:\d+$"),
        CallbackQueryHandler(return_admin_field_selected, pattern=r"^retadminfield:"),
        CallbackQueryHandler(return_admin_delete_confirmed, pattern=r"^retadmindel:yes:\d+$"),
        CallbackQueryHandler(return_admin_items_open, pattern=r"^retadminitems:\d+$"),
        CallbackQueryHandler(return_admin_item_open, pattern=r"^retadminitem:\d+:\d+$"),
        CallbackQueryHandler(return_admin_item_field_selected, pattern=r"^retadminitemfield:\d+:\d+:"),
        CallbackQueryHandler(return_admin_category_selected, pattern=r"^retadmincat:\d+:\d+:"),
        CallbackQueryHandler(return_admin_model_selected, pattern=r"^retadminmodel:\d+:\d+:"),
        CallbackQueryHandler(return_admin_product_selected, pattern=r"^retadminprod:\d+:\d+:"),
        CallbackQueryHandler(return_admin_size_selected, pattern=r"^retadminsizeset:\d+:\d+:"),
        CallbackQueryHandler(return_admin_condition_selected, pattern=r"^retadmincondset:\d+:\d+:"),
        CallbackQueryHandler(return_admin_chz_selected, pattern=r"^retadminchzset:\d+:\d+:"),
        CallbackQueryHandler(return_admin_add_chz_missing, pattern=r"^retadminaddchzmissing:\d+$"),
        CallbackQueryHandler(return_admin_item_add, pattern=r"^retadminitemadd:\d+$"),
        CallbackQueryHandler(return_admin_item_delete_selected, pattern=r"^retadminitemdelete:\d+:\d+$"),
        CallbackQueryHandler(return_admin_item_delete_confirmed, pattern=r"^retadminitemdeleteyes:\d+:\d+$"),
        CallbackQueryHandler(return_admin_type_open, pattern=r"^retadmintype:\d+$"),
        CallbackQueryHandler(return_admin_type_selected, pattern=r"^retadmintypeset:\d+:"),
        CallbackQueryHandler(return_admin_resend, pattern=r"^retadminresend:\d+$"),
        CallbackQueryHandler(return_admin_base_photo_selected, pattern=r"^retadminbasephoto:\d+$"),
        CallbackQueryHandler(return_admin_cancel, pattern=r"^retadmin:cancel$"),
        CallbackQueryHandler(return_admin_unknown, pattern=r"^retadmin"),
    ]


def get_returns_admin_message_handler():
    return MessageHandler(filters.TEXT & ~filters.COMMAND, return_admin_value_received)


def get_returns_admin_photo_handler():
    return MessageHandler(filters.PHOTO, return_admin_photo_received)
