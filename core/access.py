import logging

from modules.employees.roles import employee_roles

# ============================================================
# ДОСТУПЫ
# ============================================================
# Основные роли берутся из справочника «Сотрудники» в БД.
# Дополнительно есть fallback на payroll_config.py, чтобы доступ не ломался,
# если справочник временно недоступен.

ROLE_PERMISSIONS = {
    "admin": {"incoming", "returns", "last_records", "service", "payroll", "schedule", "consumables", "marking", "employees", "products", "lamoda"},
    "warehouse_manager": {"incoming", "returns", "last_records", "service", "payroll", "schedule", "consumables", "marking", "employees", "products", "lamoda"},
    "brand_manager": {"incoming", "returns", "last_records", "service", "payroll", "schedule", "consumables", "marking", "employees", "products", "lamoda"},
    "warehouse_employee": {"incoming", "returns", "last_records", "payroll", "schedule", "consumables", "marking", "lamoda"},
    "viewer": {"last_records"},
}

ALLOWED_BOT_ROLES = {"admin", "warehouse_manager", "brand_manager", "warehouse_employee"}

NO_ACCESS_TEXT = (
    "⛔️ У вас нет доступа к боту.\n\n"
    "Вы не являетесь сотрудником склада или руководителем, добавленным в список сотрудников."
)


def normalize_username(value):
    return str(value or "").strip().lstrip("@").lower()


def safe_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "да", "истина"}


def find_employee_in_config(user):
    """Fallback-поиск сотрудника в payroll_config.py без обращения к БД."""
    try:
        from modules.payroll.config import PAYROLL_EMPLOYEES
    except Exception:
        logging.exception("Не удалось загрузить PAYROLL_EMPLOYEES из payroll_config.py")
        return None

    telegram_user_id = str(user.id)
    username = normalize_username(user.username)

    for employee in PAYROLL_EMPLOYEES:
        if str(employee.get("telegram_user_id", "")).strip() == telegram_user_id:
            return employee

    if username:
        for employee in PAYROLL_EMPLOYEES:
            if normalize_username(employee.get("telegram_username", "")) == username:
                return employee

    return None


def find_registered_employee(user):
    """Ищет пользователя в справочнике «Сотрудники», затем в payroll_config.py."""
    if not user:
        return None

    try:
        from modules.payroll.google_sheets import find_employee_for_telegram_user

        employee = find_employee_for_telegram_user(user)
        if employee:
            return employee
    except Exception:
        logging.exception("Не удалось проверить сотрудника через БД, используем fallback payroll_config.py")

    return find_employee_in_config(user)


def is_registered_bot_user(user):
    employee = find_registered_employee(user)
    if not employee:
        return False

    roles = set(employee_roles(employee))
    is_active = safe_bool(employee.get("is_active", True))

    return is_active and bool(roles & ALLOWED_BOT_ROLES)


def is_recruitment_update(update, context):
    return False


async def access_guard(update, context):
    """Глобальная защита бота от пользователей вне списка сотрудников."""
    from telegram.ext import ApplicationHandlerStop

    if is_registered_bot_user(update.effective_user):
        return

    if is_recruitment_update(update, context):
        return

    chat = update.effective_chat
    is_private_chat = bool(chat and chat.type == "private")

    if update.callback_query:
        query = update.callback_query
        await query.answer(
            "У вас нет доступа к функционалу бота.",
            show_alert=True,
        )

        if is_private_chat:
            try:
                await query.edit_message_text(NO_ACCESS_TEXT)
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=NO_ACCESS_TEXT,
                )

        raise ApplicationHandlerStop

    if update.message:
        if is_private_chat:
            await update.message.reply_text(NO_ACCESS_TEXT)

        raise ApplicationHandlerStop


def get_user_role(user_id):
    # Для старых модулей оставляем совместимость.
    # Реальная проверка доступа делается через access_guard().
    return "admin"


def has_permission(user_id, permission):
    role = get_user_role(user_id)
    return permission in ROLE_PERMISSIONS.get(role, set())


async def role_guard(update, context):
    """Проверяет права и для старых inline-кнопок после смены ролей."""
    from telegram.ext import ApplicationHandlerStop
    from modules.employees.roles import has_any_role

    query = update.callback_query
    if not query:
        return
    data = str(query.data or "")
    employee = find_registered_employee(update.effective_user)
    allowed = True
    if data == "section:admin" or data.startswith("admin:"):
        allowed = has_any_role(employee, {"admin", "warehouse_manager"})
    elif data == "section:schedule" or data.startswith(("sch", "schemp:")):
        allowed = has_any_role(employee, {"admin", "warehouse_manager"})
    elif data == "section:employees" or data.startswith("emp"):
        allowed = has_any_role(employee, {"admin", "warehouse_manager", "brand_manager"})
    if not allowed:
        await query.answer("⛔️ Недостаточно прав для этого раздела.", show_alert=True)
        raise ApplicationHandlerStop
