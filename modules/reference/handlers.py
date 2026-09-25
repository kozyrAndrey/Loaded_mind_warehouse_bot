from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes


BASE_DIR = Path(__file__).resolve().parents[2]
INSTRUCTIONS_DIR = BASE_DIR / "resources" / "instructions"
INFO_DIR = BASE_DIR / "resources" / "info"

INFO_FILES = {
    "wifi_printers": ("Wi-Fi / принтеры", INFO_DIR / "wifi_printers.txt"),
    "phones": ("Телефоны сотрудников", INFO_DIR / "employee_phones.txt"),
    "guards": ("Охранники", INFO_DIR / "guards.txt"),
    "license_plates": ("Номерные знаки", INFO_DIR / "license_plates.txt"),
    "warehouse_duties": ("Обязанности сотрудника склада", INFO_DIR / "warehouse_duties.txt"),
}

POWER_OF_ATTORNEY_GLOB = "power_of_attorney.*"

ROLE_LABELS = {
    "warehouse_employee": "Сотрудник склада",
    "operations": "Операционщик",
    "warehouse_manager": "Руководитель склада",
    "brand_manager": "Руководитель бренда",
    "admin": "Администратор",
}


def instruction_files():
    if not INSTRUCTIONS_DIR.exists():
        return []
    return sorted(INSTRUCTIONS_DIR.glob("*.pdf"), key=lambda path: path.name.lower())


def instructions_keyboard():
    rows = []
    for index, path in enumerate(instruction_files()):
        title = path.stem.replace("_", " ")
        rows.append([InlineKeyboardButton(title, callback_data=f"ref:instruction:{index}")])
    return InlineKeyboardMarkup(rows)


def info_keyboard():
    rows = [
        [InlineKeyboardButton(label, callback_data=f"ref:info:{key}")]
        for key, (label, _path) in INFO_FILES.items()
    ]
    rows.append([InlineKeyboardButton("Доверенность", callback_data="ref:info:power_of_attorney")])
    return InlineKeyboardMarkup(rows)


async def instructions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    files = instruction_files()
    if not files:
        await update.message.reply_text(
            f"Инструкции пока не найдены. Добавьте PDF-файлы в папку {INSTRUCTIONS_DIR}."
        )
        return

    await update.message.reply_text("Выберите инструкцию:", reply_markup=instructions_keyboard())


async def instruction_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.rsplit(":", 1)[-1])
        path = instruction_files()[index]
    except (ValueError, IndexError):
        await query.edit_message_text("Инструкция не найдена.")
        return

    with open(path, "rb") as file:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file,
            filename=path.name,
            caption=path.stem.replace("_", " "),
        )


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите раздел:", reply_markup=info_keyboard())


async def info_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key = query.data.rsplit(":", 1)[-1]
    if key == "phones":
        await query.edit_message_text(build_employee_phones_text())
        return

    if key == "power_of_attorney":
        await send_power_of_attorney(update, context)
        return

    label, path = INFO_FILES.get(key, ("Информация", None))
    if not path or not path.exists():
        await query.edit_message_text(f"Раздел «{label}» пока не заполнен.")
        return

    text = path.read_text(encoding="utf-8").strip()
    await query.edit_message_text(text or f"Раздел «{label}» пока пуст.")


def power_of_attorney_file():
    files = sorted(INFO_DIR.glob(POWER_OF_ATTORNEY_GLOB), key=lambda path: path.name.lower())
    return files[0] if files else None


async def send_power_of_attorney(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    path = power_of_attorney_file()
    if not path:
        await query.edit_message_text(
            "Файл доверенности пока не найден.\n\n"
            f"Добавьте его в папку {INFO_DIR} с именем power_of_attorney.pdf или power_of_attorney.docx."
        )
        return

    await query.edit_message_text("Отправляю доверенность...")
    with open(path, "rb") as file:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file,
            filename=path.name,
            caption="Доверенность",
        )


def build_employee_phones_text():
    from modules.payroll.google_sheets import get_employees
    from modules.employees.roles import format_role_labels

    lines = ["Телефоны сотрудников", ""]
    for employee in get_employees(include_inactive=False):
        username = str(employee.get("telegram_username", "")).strip()
        username_text = f"@{username}" if username else "—"
        role = format_role_labels(employee)
        phone = str(employee.get("phone", "")).strip() or "—"
        lines.append(f"{employee['full_name']}, {role}, {phone}, {username_text}")

    extra_path = INFO_FILES["phones"][1]
    extra_text = extra_path.read_text(encoding="utf-8").strip() if extra_path.exists() else ""
    if extra_text:
        lines.extend(["", "Дополнительные контакты:", extra_text])

    return "\n".join(lines).strip()


def get_reference_handlers():
    return [
        CommandHandler(["instructions", "instruction"], instructions_command),
        CommandHandler(["info"], info_command),
        CallbackQueryHandler(instruction_selected, pattern=r"^ref:instruction:"),
        CallbackQueryHandler(info_selected, pattern=r"^ref:info:"),
    ]
