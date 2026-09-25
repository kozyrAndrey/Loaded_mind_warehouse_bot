import logging
import math
import re
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from modules.passes.pdf import CAPACITY_LABELS, create_visitor_pass_pdf
from modules.passes.storage import (
    count_pass_templates,
    create_pass_template,
    delete_pass_template,
    get_pass_template,
    list_pass_templates,
    update_pass_template,
)


(
    PASS_MENU,
    PASS_DATE,
    PASS_CUSTOM_DATE,
    PASS_SURNAME,
    PASS_FIRST_NAME,
    PASS_PATRONYMIC,
    PASS_HAS_VEHICLE,
    PASS_VEHICLE_MAKE,
    PASS_VEHICLE_TYPE,
    PASS_CAPACITY,
    PASS_LICENSE_PLATE,
    PASS_CONFIRM,
    PASS_SAVE_OFFER,
    PASS_TEMPLATE_NAME,
    PASS_TEMPLATE_LIST,
    PASS_TEMPLATE_DETAIL,
    PASS_EDIT_MENU,
    PASS_EDIT_TEXT,
    PASS_EDIT_PATRONYMIC,
    PASS_EDIT_HAS_VEHICLE,
    PASS_EDIT_VEHICLE_MAKE,
    PASS_EDIT_VEHICLE_TYPE,
    PASS_EDIT_CAPACITY,
    PASS_EDIT_LICENSE_PLATE,
    PASS_DELETE_CONFIRM,
) = range(1550, 1575)


TEMPLATE_PAGE_SIZE = 10
PASS_DATA_KEY = "visitor_pass_data"
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def today_moscow():
    return datetime.now(MOSCOW_TZ).date()


def _keyboard(rows):
    return InlineKeyboardMarkup(rows)


def passes_menu_keyboard():
    return _keyboard([
        [InlineKeyboardButton("➕ Создать новый пропуск", callback_data="pass:new")],
        [InlineKeyboardButton("📋 Сохранённые шаблоны", callback_data="pass:templates")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ])


def cancel_keyboard(back_callback=None):
    rows = []
    if back_callback:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")])
    return _keyboard(rows)


def date_keyboard():
    return _keyboard([
        [InlineKeyboardButton(f"Сегодня — {today_moscow():%d.%m.%Y}", callback_data="pass:date:today")],
        [InlineKeyboardButton("📅 Другая дата", callback_data="pass:date:custom")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")],
    ])


def patronymic_keyboard(back_callback=None):
    rows = [[InlineKeyboardButton("Нет отчества", callback_data="pass:no_patronymic")]]
    if back_callback:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_callback)])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")])
    return _keyboard(rows)


def has_vehicle_keyboard(prefix="pass:vehicle"):
    return _keyboard([
        [
            InlineKeyboardButton("🚗 Да", callback_data=f"{prefix}:yes"),
            InlineKeyboardButton("🚶 Нет", callback_data=f"{prefix}:no"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")],
    ])


def vehicle_type_keyboard(prefix="pass:type"):
    return _keyboard([
        [InlineKeyboardButton("🚙 Легковой", callback_data=f"{prefix}:passenger")],
        [InlineKeyboardButton("🚚 Грузовой", callback_data=f"{prefix}:cargo")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")],
    ])


def capacity_keyboard(prefix="pass:capacity"):
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{code}")]
        for code, label in CAPACITY_LABELS.items()
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")])
    return _keyboard(rows)


def confirm_keyboard():
    return _keyboard([
        [InlineKeyboardButton("📄 Сформировать PDF", callback_data="pass:generate")],
        [InlineKeyboardButton("🔄 Заполнить заново", callback_data="pass:new")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pass:cancel")],
    ])


def save_offer_keyboard():
    return _keyboard([
        [InlineKeyboardButton("💾 Сохранить шаблон", callback_data="pass:save:yes")],
        [InlineKeyboardButton("Без сохранения", callback_data="pass:save:no")],
    ])


def template_detail_keyboard(template_id):
    return _keyboard([
        [InlineKeyboardButton("📄 Сформировать пропуск", callback_data=f"pass:use:{template_id}")],
        [InlineKeyboardButton("✏️ Изменить", callback_data=f"pass:edit:{template_id}")],
        [InlineKeyboardButton("📝 Переименовать", callback_data=f"pass:rename:{template_id}")],
        [InlineKeyboardButton("🗑 Удалить", callback_data=f"pass:delete:{template_id}")],
        [InlineKeyboardButton("⬅️ К шаблонам", callback_data="pass:templates")],
    ])


def edit_template_keyboard(template_id):
    return _keyboard([
        [InlineKeyboardButton("Фамилия", callback_data="pass:editfield:surname")],
        [InlineKeyboardButton("Имя", callback_data="pass:editfield:first_name")],
        [InlineKeyboardButton("Отчество", callback_data="pass:editfield:patronymic")],
        [InlineKeyboardButton("Автомобильные данные", callback_data="pass:editfield:vehicle")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"pass:template:{template_id}")],
    ])


def normalize_text(value, max_length=120):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def valid_name_part(value):
    value = normalize_text(value)
    word = r"[A-Za-zА-Яа-яЁё'’-]+"
    return bool(1 < len(value) <= 120 and re.fullmatch(rf"{word}(?:\s+{word})*", value))


def valid_vehicle_text(value):
    value = normalize_text(value)
    return bool(1 < len(value) <= 120 and re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9 ./'’()\-]+", value))


def normalize_license_plate(value):
    return re.sub(r"\s+", "", normalize_text(value, 30)).upper()


def parse_pass_date(value):
    try:
        return datetime.strptime(normalize_text(value, 20), "%d.%m.%Y").date()
    except ValueError:
        return None


def template_to_pass_data(template):
    return {
        "surname": template["surname"],
        "first_name": template["first_name"],
        "patronymic": template.get("patronymic") or "-",
        "has_vehicle": bool(template.get("has_vehicle")),
        "vehicle_make": template.get("vehicle_make") or "",
        "vehicle_type": template.get("vehicle_type") or "",
        "capacity_code": template.get("capacity_code") or "",
        "license_plate": template.get("license_plate") or "",
        "source_template_id": template["id"],
    }


def format_pass_summary(data):
    lines = [
        "Проверьте данные пропуска:",
        "",
        f"Дата: {data['date']:%d.%m.%Y}",
        f"ФИО: {data['surname']} {data['first_name']} {data.get('patronymic') or '-'}",
    ]
    if data.get("has_vehicle"):
        type_label = "легковой" if data.get("vehicle_type") == "passenger" else "грузовой"
        lines.extend([
            "Автомобиль: да",
            f"Марка: {data.get('vehicle_make') or '-'}",
            f"Тип: {type_label}",
        ])
        if data.get("vehicle_type") == "cargo":
            lines.append(f"Грузоподъёмность: {CAPACITY_LABELS.get(data.get('capacity_code'), '-')}")
        lines.append(f"Госномер: {data.get('license_plate') or '-'}")
    else:
        lines.append("Автомобиль: нет")
    return "\n".join(lines)


def format_template(template):
    data = template_to_pass_data(template)
    lines = [
        f"📋 {template['name']}",
        "",
        f"ФИО: {data['surname']} {data['first_name']} {data['patronymic']}",
    ]
    if data["has_vehicle"]:
        type_label = "легковой" if data["vehicle_type"] == "passenger" else "грузовой"
        lines.extend([
            f"Автомобиль: {data['vehicle_make']}",
            f"Тип: {type_label}",
            f"Госномер: {data['license_plate']}",
        ])
        if data["vehicle_type"] == "cargo":
            lines.insert(-1, f"Грузоподъёмность: {CAPACITY_LABELS.get(data['capacity_code'], '-')}")
    else:
        lines.append("Автомобиль: нет")
    return "\n".join(lines)


async def show_passes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🚪 Разовые пропуска:", reply_markup=passes_menu_keyboard())
    return PASS_MENU


async def cancel_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🚪 Разовые пропуска:", reply_markup=passes_menu_keyboard())
    return PASS_MENU


async def new_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data[PASS_DATA_KEY] = {}
    await query.edit_message_text("Выберите дату пропуска:", reply_markup=date_keyboard())
    return PASS_DATE


async def use_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = query.data.rsplit(":", 1)[-1]
    template = get_pass_template(template_id)
    if not template:
        await query.edit_message_text("Шаблон не найден.", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    context.user_data.clear()
    context.user_data[PASS_DATA_KEY] = template_to_pass_data(template)
    await query.edit_message_text(
        f"Шаблон: {template['name']}\n\nВыберите дату нового пропуска:",
        reply_markup=date_keyboard(),
    )
    return PASS_DATE


async def date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "pass:date:custom":
        await query.edit_message_text("Введите дату в формате ДД.ММ.ГГГГ:", reply_markup=cancel_keyboard())
        return PASS_CUSTOM_DATE
    data = context.user_data.setdefault(PASS_DATA_KEY, {})
    data["date"] = today_moscow()
    if data.get("source_template_id"):
        await query.edit_message_text(format_pass_summary(data), reply_markup=confirm_keyboard())
        return PASS_CONFIRM
    await query.edit_message_text(
        "Введите фамилию посетителя (она может состоять из нескольких слов):",
        reply_markup=cancel_keyboard(),
    )
    return PASS_SURNAME


async def custom_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    day = parse_pass_date(update.message.text)
    if not day:
        await update.message.reply_text("Некорректная дата. Введите её в формате ДД.ММ.ГГГГ:", reply_markup=cancel_keyboard())
        return PASS_CUSTOM_DATE
    data = context.user_data.setdefault(PASS_DATA_KEY, {})
    data["date"] = day
    if data.get("source_template_id"):
        await update.message.reply_text(format_pass_summary(data), reply_markup=confirm_keyboard())
        return PASS_CONFIRM
    await update.message.reply_text(
        "Введите фамилию посетителя (она может состоять из нескольких слов):",
        reply_markup=cancel_keyboard(),
    )
    return PASS_SURNAME


async def surname_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_name_part(value):
        await update.message.reply_text("Введите фамилию буквами (допустимы дефис и апостроф):", reply_markup=cancel_keyboard())
        return PASS_SURNAME
    context.user_data[PASS_DATA_KEY]["surname"] = value
    await update.message.reply_text(
        "Введите имя посетителя (оно может состоять из нескольких слов):",
        reply_markup=cancel_keyboard(),
    )
    return PASS_FIRST_NAME


async def first_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_name_part(value):
        await update.message.reply_text("Введите имя буквами (допустимы дефис и апостроф):", reply_markup=cancel_keyboard())
        return PASS_FIRST_NAME
    context.user_data[PASS_DATA_KEY]["first_name"] = value
    await update.message.reply_text(
        "Введите отчество (оно может состоять из нескольких слов) или нажмите «Нет отчества»:",
        reply_markup=patronymic_keyboard(),
    )
    return PASS_PATRONYMIC


async def patronymic_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_name_part(value):
        await update.message.reply_text("Введите отчество буквами или нажмите «Нет отчества»:", reply_markup=patronymic_keyboard())
        return PASS_PATRONYMIC
    context.user_data[PASS_DATA_KEY]["patronymic"] = value
    await update.message.reply_text("Посетитель будет на автомобиле?", reply_markup=has_vehicle_keyboard())
    return PASS_HAS_VEHICLE


async def no_patronymic_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data[PASS_DATA_KEY]["patronymic"] = "-"
    await query.edit_message_text("Посетитель будет на автомобиле?", reply_markup=has_vehicle_keyboard())
    return PASS_HAS_VEHICLE


async def has_vehicle_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data[PASS_DATA_KEY]
    if query.data.endswith(":no"):
        data.update({
            "has_vehicle": False,
            "vehicle_make": "",
            "vehicle_type": "",
            "capacity_code": "",
            "license_plate": "",
        })
        await query.edit_message_text(format_pass_summary(data), reply_markup=confirm_keyboard())
        return PASS_CONFIRM
    data["has_vehicle"] = True
    await query.edit_message_text("Введите марку автомобиля:", reply_markup=cancel_keyboard())
    return PASS_VEHICLE_MAKE


async def vehicle_make_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_vehicle_text(value):
        await update.message.reply_text("Введите корректную марку автомобиля:", reply_markup=cancel_keyboard())
        return PASS_VEHICLE_MAKE
    context.user_data[PASS_DATA_KEY]["vehicle_make"] = value
    await update.message.reply_text("Выберите тип автомобиля:", reply_markup=vehicle_type_keyboard())
    return PASS_VEHICLE_TYPE


async def vehicle_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vehicle_type = query.data.rsplit(":", 1)[-1]
    data = context.user_data[PASS_DATA_KEY]
    data["vehicle_type"] = vehicle_type
    data["capacity_code"] = ""
    if vehicle_type == "cargo":
        await query.edit_message_text("Выберите грузоподъёмность:", reply_markup=capacity_keyboard())
        return PASS_CAPACITY
    await query.edit_message_text("Введите государственный номер автомобиля:", reply_markup=cancel_keyboard())
    return PASS_LICENSE_PLATE


async def capacity_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.rsplit(":", 1)[-1]
    if code not in CAPACITY_LABELS:
        await query.edit_message_text("Выберите грузоподъёмность:", reply_markup=capacity_keyboard())
        return PASS_CAPACITY
    context.user_data[PASS_DATA_KEY]["capacity_code"] = code
    await query.edit_message_text("Введите государственный номер автомобиля:", reply_markup=cancel_keyboard())
    return PASS_LICENSE_PLATE


async def license_plate_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_license_plate(update.message.text)
    if not (3 <= len(value) <= 15) or not re.fullmatch(r"[A-ZА-ЯЁ0-9-]+", value):
        await update.message.reply_text("Введите корректный госномер без лишних символов:", reply_markup=cancel_keyboard())
        return PASS_LICENSE_PLATE
    data = context.user_data[PASS_DATA_KEY]
    data["license_plate"] = value
    await update.message.reply_text(format_pass_summary(data), reply_markup=confirm_keyboard())
    return PASS_CONFIRM


async def generate_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get(PASS_DATA_KEY, {})
    try:
        content = create_visitor_pass_pdf(data)
        safe_name = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_-]+", "_", data.get("surname", "посетитель")).strip("_")
        filename = f"Разовый_пропуск_{safe_name}_{data['date']:%d-%m-%Y}.pdf"
        await query.message.reply_document(
            document=InputFile(BytesIO(content), filename=filename),
            caption="Разовый пропуск готов для печати ✅",
        )
    except Exception:
        logging.exception("Не удалось сформировать разовый пропуск")
        await query.edit_message_text("Не удалось сформировать PDF. Попробуйте ещё раз.", reply_markup=confirm_keyboard())
        return PASS_CONFIRM

    if data.get("source_template_id"):
        context.user_data.clear()
        await query.edit_message_text("PDF сформирован ✅", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    await query.edit_message_text("PDF сформирован ✅\n\nСохранить введённые данные как общий шаблон?", reply_markup=save_offer_keyboard())
    return PASS_SAVE_OFFER


async def save_offer_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.endswith(":no"):
        context.user_data.clear()
        await query.edit_message_text("Готово.", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    data = context.user_data[PASS_DATA_KEY]
    suggested = data["surname"]
    if data.get("license_plate"):
        suggested += f" — {data['license_plate']}"
    await query.edit_message_text(
        f"Введите название шаблона. Например:\n{suggested}",
        reply_markup=cancel_keyboard(),
    )
    return PASS_TEMPLATE_NAME


async def template_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = normalize_text(update.message.text, 255)
    if len(name) < 2:
        await update.message.reply_text("Название должно содержать минимум два символа:", reply_markup=cancel_keyboard())
        return PASS_TEMPLATE_NAME
    data = dict(context.user_data[PASS_DATA_KEY])
    data["name"] = name
    create_pass_template(data, update.effective_user)
    context.user_data.clear()
    await update.message.reply_text("Шаблон сохранён и доступен всем сотрудникам ✅", reply_markup=passes_menu_keyboard())
    return PASS_MENU


def templates_keyboard(templates, page, total):
    rows = [
        [InlineKeyboardButton(template["name"][:55], callback_data=f"pass:template:{template['id']}")]
        for template in templates
    ]
    pages = max(1, math.ceil(total / TEMPLATE_PAGE_SIZE))
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️", callback_data=f"pass:templates:{page - 1}"))
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton("➡️", callback_data=f"pass:templates:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("⬅️ В раздел пропусков", callback_data="pass:cancel")])
    return _keyboard(rows)


async def show_templates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = 0
    if query.data.count(":") == 2:
        try:
            page = max(0, int(query.data.rsplit(":", 1)[-1]))
        except ValueError:
            page = 0
    total = count_pass_templates()
    pages = max(1, math.ceil(total / TEMPLATE_PAGE_SIZE))
    page = min(page, pages - 1)
    templates = list_pass_templates(TEMPLATE_PAGE_SIZE, page * TEMPLATE_PAGE_SIZE)
    text = "Сохранённых шаблонов пока нет." if not templates else f"Общие шаблоны пропусков — страница {page + 1}/{pages}:"
    await query.edit_message_text(text, reply_markup=templates_keyboard(templates, page, total))
    return PASS_TEMPLATE_LIST


async def show_template_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = query.data.rsplit(":", 1)[-1]
    template = get_pass_template(template_id)
    if not template:
        await query.edit_message_text("Шаблон не найден.", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    context.user_data["pass_template_id"] = template["id"]
    await query.edit_message_text(format_template(template), reply_markup=template_detail_keyboard(template["id"]))
    return PASS_TEMPLATE_DETAIL


async def show_template_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = int(query.data.rsplit(":", 1)[-1])
    template = get_pass_template(template_id)
    if not template:
        await query.edit_message_text("Шаблон не найден.", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    context.user_data["pass_template_id"] = template_id
    await query.edit_message_text("Что изменить?", reply_markup=edit_template_keyboard(template_id))
    return PASS_EDIT_MENU


async def rename_template_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["pass_template_id"] = int(query.data.rsplit(":", 1)[-1])
    context.user_data["pass_edit_field"] = "name"
    await query.edit_message_text("Введите новое название шаблона:", reply_markup=cancel_keyboard("pass:templates"))
    return PASS_EDIT_TEXT


async def edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.rsplit(":", 1)[-1]
    context.user_data["pass_edit_field"] = field
    if field == "patronymic":
        await query.edit_message_text("Введите отчество или нажмите «Нет отчества»:", reply_markup=patronymic_keyboard("pass:templates"))
        return PASS_EDIT_PATRONYMIC
    if field == "vehicle":
        await query.edit_message_text("Посетитель будет на автомобиле?", reply_markup=has_vehicle_keyboard("pass:editvehicle"))
        return PASS_EDIT_HAS_VEHICLE
    prompt = "Введите новую фамилию:" if field == "surname" else "Введите новое имя:"
    await query.edit_message_text(prompt, reply_markup=cancel_keyboard("pass:templates"))
    return PASS_EDIT_TEXT


async def edit_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("pass_edit_field")
    value = normalize_text(update.message.text, 255 if field == "name" else 120)
    valid = len(value) >= 2 if field == "name" else valid_name_part(value)
    if not valid:
        await update.message.reply_text("Некорректное значение. Введите ещё раз:", reply_markup=cancel_keyboard("pass:templates"))
        return PASS_EDIT_TEXT
    template = update_pass_template(context.user_data["pass_template_id"], **{field: value})
    await update.message.reply_text("Шаблон обновлён ✅\n\n" + format_template(template), reply_markup=template_detail_keyboard(template["id"]))
    return PASS_TEMPLATE_DETAIL


async def edit_patronymic_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_name_part(value):
        await update.message.reply_text("Введите отчество буквами или нажмите «Нет отчества»:", reply_markup=patronymic_keyboard("pass:templates"))
        return PASS_EDIT_PATRONYMIC
    return await _save_edited_patronymic(update, context, value)


async def edit_no_patronymic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template = update_pass_template(context.user_data["pass_template_id"], patronymic="-")
    await query.edit_message_text("Шаблон обновлён ✅\n\n" + format_template(template), reply_markup=template_detail_keyboard(template["id"]))
    return PASS_TEMPLATE_DETAIL


async def _save_edited_patronymic(update, context, value):
    template = update_pass_template(context.user_data["pass_template_id"], patronymic=value)
    await update.message.reply_text("Шаблон обновлён ✅\n\n" + format_template(template), reply_markup=template_detail_keyboard(template["id"]))
    return PASS_TEMPLATE_DETAIL


async def edit_has_vehicle_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.endswith(":no"):
        template = update_pass_template(
            context.user_data["pass_template_id"],
            has_vehicle=False,
            vehicle_make=None,
            vehicle_type=None,
            capacity_code=None,
            license_plate=None,
        )
        await query.edit_message_text("Шаблон обновлён ✅\n\n" + format_template(template), reply_markup=template_detail_keyboard(template["id"]))
        return PASS_TEMPLATE_DETAIL
    context.user_data["pass_edit_vehicle"] = {"has_vehicle": True}
    await query.edit_message_text("Введите новую марку автомобиля:", reply_markup=cancel_keyboard("pass:templates"))
    return PASS_EDIT_VEHICLE_MAKE


async def edit_vehicle_make_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_text(update.message.text)
    if not valid_vehicle_text(value):
        await update.message.reply_text("Введите корректную марку автомобиля:", reply_markup=cancel_keyboard("pass:templates"))
        return PASS_EDIT_VEHICLE_MAKE
    context.user_data["pass_edit_vehicle"]["vehicle_make"] = value
    await update.message.reply_text("Выберите тип автомобиля:", reply_markup=vehicle_type_keyboard("pass:edittype"))
    return PASS_EDIT_VEHICLE_TYPE


async def edit_vehicle_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vehicle_type = query.data.rsplit(":", 1)[-1]
    context.user_data["pass_edit_vehicle"]["vehicle_type"] = vehicle_type
    context.user_data["pass_edit_vehicle"]["capacity_code"] = None
    if vehicle_type == "cargo":
        await query.edit_message_text("Выберите грузоподъёмность:", reply_markup=capacity_keyboard("pass:editcapacity"))
        return PASS_EDIT_CAPACITY
    await query.edit_message_text("Введите новый государственный номер:", reply_markup=cancel_keyboard("pass:templates"))
    return PASS_EDIT_LICENSE_PLATE


async def edit_capacity_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.rsplit(":", 1)[-1]
    if code not in CAPACITY_LABELS:
        return PASS_EDIT_CAPACITY
    context.user_data["pass_edit_vehicle"]["capacity_code"] = code
    await query.edit_message_text("Введите новый государственный номер:", reply_markup=cancel_keyboard("pass:templates"))
    return PASS_EDIT_LICENSE_PLATE


async def edit_license_plate_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = normalize_license_plate(update.message.text)
    if not (3 <= len(value) <= 15) or not re.fullmatch(r"[A-ZА-ЯЁ0-9-]+", value):
        await update.message.reply_text("Введите корректный госномер без лишних символов:", reply_markup=cancel_keyboard("pass:templates"))
        return PASS_EDIT_LICENSE_PLATE
    fields = context.user_data["pass_edit_vehicle"]
    fields["license_plate"] = value
    template = update_pass_template(context.user_data["pass_template_id"], **fields)
    await update.message.reply_text("Шаблон обновлён ✅\n\n" + format_template(template), reply_markup=template_detail_keyboard(template["id"]))
    return PASS_TEMPLATE_DETAIL


async def delete_template_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = int(query.data.rsplit(":", 1)[-1])
    template = get_pass_template(template_id)
    if not template:
        await query.edit_message_text("Шаблон не найден.", reply_markup=passes_menu_keyboard())
        return PASS_MENU
    context.user_data["pass_template_id"] = template_id
    await query.edit_message_text(
        f"Удалить общий шаблон «{template['name']}»?",
        reply_markup=_keyboard([
            [InlineKeyboardButton("🗑 Да, удалить", callback_data="pass:delete:confirm")],
            [InlineKeyboardButton("⬅️ Нет", callback_data=f"pass:template:{template_id}")],
        ]),
    )
    return PASS_DELETE_CONFIRM


async def delete_template_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delete_pass_template(context.user_data.get("pass_template_id"))
    await query.edit_message_text("Шаблон удалён.", reply_markup=passes_menu_keyboard())
    return PASS_MENU


def _common_callback_handlers():
    return [
        CallbackQueryHandler(cancel_pass, pattern=r"^pass:cancel$"),
        CallbackQueryHandler(new_pass, pattern=r"^pass:new$"),
    ]


def get_pass_handlers():
    common = _common_callback_handlers()
    return [
        ConversationHandler(
            entry_points=[CallbackQueryHandler(show_passes_menu, pattern=r"^section:passes$")],
            states={
                PASS_MENU: common + [
                    CallbackQueryHandler(show_templates, pattern=r"^pass:templates(?::\d+)?$"),
                ],
                PASS_DATE: common + [CallbackQueryHandler(date_selected, pattern=r"^pass:date:(today|custom)$")],
                PASS_CUSTOM_DATE: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_date_received)],
                PASS_SURNAME: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, surname_received)],
                PASS_FIRST_NAME: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, first_name_received)],
                PASS_PATRONYMIC: common + [
                    CallbackQueryHandler(no_patronymic_selected, pattern=r"^pass:no_patronymic$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, patronymic_received),
                ],
                PASS_HAS_VEHICLE: common + [CallbackQueryHandler(has_vehicle_selected, pattern=r"^pass:vehicle:(yes|no)$")],
                PASS_VEHICLE_MAKE: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_make_received)],
                PASS_VEHICLE_TYPE: common + [CallbackQueryHandler(vehicle_type_selected, pattern=r"^pass:type:(passenger|cargo)$")],
                PASS_CAPACITY: common + [CallbackQueryHandler(capacity_selected, pattern=r"^pass:capacity:[a-z0-9_]+$")],
                PASS_LICENSE_PLATE: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, license_plate_received)],
                PASS_CONFIRM: common + [CallbackQueryHandler(generate_pass, pattern=r"^pass:generate$")],
                PASS_SAVE_OFFER: common + [CallbackQueryHandler(save_offer_selected, pattern=r"^pass:save:(yes|no)$")],
                PASS_TEMPLATE_NAME: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, template_name_received)],
                PASS_TEMPLATE_LIST: common + [
                    CallbackQueryHandler(show_templates, pattern=r"^pass:templates(?::\d+)?$"),
                    CallbackQueryHandler(show_template_detail, pattern=r"^pass:template:\d+$"),
                ],
                PASS_TEMPLATE_DETAIL: common + [
                    CallbackQueryHandler(use_template, pattern=r"^pass:use:\d+$"),
                    CallbackQueryHandler(show_template_edit_menu, pattern=r"^pass:edit:\d+$"),
                    CallbackQueryHandler(rename_template_start, pattern=r"^pass:rename:\d+$"),
                    CallbackQueryHandler(delete_template_start, pattern=r"^pass:delete:\d+$"),
                    CallbackQueryHandler(show_templates, pattern=r"^pass:templates(?::\d+)?$"),
                ],
                PASS_EDIT_MENU: common + [
                    CallbackQueryHandler(edit_field_selected, pattern=r"^pass:editfield:(surname|first_name|patronymic|vehicle)$"),
                    CallbackQueryHandler(show_template_detail, pattern=r"^pass:template:\d+$"),
                ],
                PASS_EDIT_TEXT: common + [
                    CallbackQueryHandler(show_templates, pattern=r"^pass:templates$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_text_received),
                ],
                PASS_EDIT_PATRONYMIC: common + [
                    CallbackQueryHandler(edit_no_patronymic, pattern=r"^pass:no_patronymic$"),
                    CallbackQueryHandler(show_templates, pattern=r"^pass:templates$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_patronymic_received),
                ],
                PASS_EDIT_HAS_VEHICLE: common + [CallbackQueryHandler(edit_has_vehicle_selected, pattern=r"^pass:editvehicle:(yes|no)$")],
                PASS_EDIT_VEHICLE_MAKE: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_vehicle_make_received)],
                PASS_EDIT_VEHICLE_TYPE: common + [CallbackQueryHandler(edit_vehicle_type_selected, pattern=r"^pass:edittype:(passenger|cargo)$")],
                PASS_EDIT_CAPACITY: common + [CallbackQueryHandler(edit_capacity_selected, pattern=r"^pass:editcapacity:[a-z0-9_]+$")],
                PASS_EDIT_LICENSE_PLATE: common + [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_license_plate_received)],
                PASS_DELETE_CONFIRM: common + [
                    CallbackQueryHandler(delete_template_confirmed, pattern=r"^pass:delete:confirm$"),
                    CallbackQueryHandler(show_template_detail, pattern=r"^pass:template:\d+$"),
                ],
            },
            fallbacks=common,
            name="visitor_passes",
        )
    ]
