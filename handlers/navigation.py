from telegram.ext import ConversationHandler

from core.keyboards import (
    REPLY_MENU,
    build_consumables_menu_keyboard,
    build_employees_menu_keyboard,
    build_marking_menu_keyboard,
    build_receiving_report_type_keyboard,
    build_returns_menu_keyboard,
)
from core.module_control import is_module_enabled
from modules.employees.roles import has_any_role
from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager


async def open_reply_section(update, context):
    label = (update.effective_message.text or "").strip()
    module_key = REPLY_MENU.get(label)
    if not module_key:
        return ConversationHandler.END

    employee = find_employee_for_telegram_user(update.effective_user)
    manager = has_any_role(employee, {"warehouse_manager", "admin"})
    if not employee or not employee.get("is_active"):
        await update.message.reply_text("⛔️ Доступ к боту не предоставлен. Пришлите администратору ваш /whoami.")
        return ConversationHandler.END
    if module_key in {"schedule", "admin"} and not manager:
        await update.message.reply_text("⛔️ Этот раздел доступен руководителю склада и администратору.")
        return ConversationHandler.END
    if module_key == "employees" and not is_manager(employee):
        await update.message.reply_text("⛔️ Этот раздел доступен руководителям.")
        return ConversationHandler.END
    if module_key != "admin" and not is_module_enabled(module_key):
        await update.message.reply_text("⛔️ Раздел временно отключён.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["_active_module"] = module_key
    message = update.message
    if module_key == "receiving":
        await message.reply_text("📦 Выберите тип приёмки:", reply_markup=build_receiving_report_type_keyboard())
    elif module_key == "returns":
        await message.reply_text("↩️ Возвраты:", reply_markup=build_returns_menu_keyboard())
    elif module_key == "consumables":
        await message.reply_text("🧾 Расходники:", reply_markup=build_consumables_menu_keyboard(manager=manager))
    elif module_key == "marking":
        await message.reply_text("🏷 Маркировка / ЧЗ:", reply_markup=build_marking_menu_keyboard(manager=manager))
    elif module_key == "employees":
        await message.reply_text("👥 Сотрудники:", reply_markup=build_employees_menu_keyboard())
    elif module_key == "schedule":
        from modules.schedule.handlers import send_schedule_menu
        await send_schedule_menu(message, employee)
    elif module_key == "payroll":
        from modules.payroll.handlers import show_payroll_menu_message
        await show_payroll_menu_message(message, employee)
    elif module_key == "admin":
        from core.module_control import get_module_states
        from modules.admin_panel.handlers import module_admin_keyboard
        await message.reply_text("⚙️ Управление модулями:", reply_markup=module_admin_keyboard(get_module_states(force=True)))
    return ConversationHandler.END
