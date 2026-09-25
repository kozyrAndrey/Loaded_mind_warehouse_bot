"""Telegram-бот склада Loaded Mind."""

import logging
import re

from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, filters,
)

from config import BOT_TOKEN
from core.access import access_guard, role_guard
from core.keyboards import REPLY_MENU
from core.module_control import init_module_control_storage, module_access_guard
from handlers.common import (
    db_status, show_main_menu, show_receiving_menu, show_returns_menu,
    show_marking_menu, show_employees_menu, start, whereami,
)
from handlers.navigation import open_reply_section
from modules.admin_panel.handlers import get_admin_panel_handlers
from modules.consumables.handlers import get_consumables_handlers
from modules.consumables.storage import init_consumables_storage
from modules.employees.handlers import get_employee_handlers
from modules.marking.handlers import get_marking_handlers
from modules.marking.search_handler import get_marking_search_handler
from modules.marking.storage import init_marking_storage
from modules.payroll.daily_summary import init_daily_summary_storage
from modules.payroll.google_sheets import init_payroll_sheet
from modules.payroll.handlers import get_payroll_handlers
from modules.payroll.vacations import init_vacation_storage
from modules.receiving.loaded_mind import get_loaded_mind_receiving_handler
from modules.receiving.report_handler import get_loaded_mind_report_handlers
from modules.receiving.postgres_storage import init_receiving_storage
from modules.returns.loaded_mind import get_loaded_mind_returns_handler
from modules.returns.admin import get_loaded_mind_returns_admin_handler
from modules.returns.storage import init_returns_storage
from modules.schedule.google_sheets import init_schedule_sheet
from modules.schedule.handlers import get_schedule_handlers
from modules.tasks.storage import init_tasks_storage


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


async def error_handler(update, context):
    logging.error("Ошибка при обработке update", exc_info=context.error)


async def reset_conversations_on_navigation(update, context):
    if update.effective_message and update.effective_message.text in REPLY_MENU:
        context.user_data.pop("_active_module", None)
    for handlers in context.application.handlers.values():
        for handler in handlers:
            if not isinstance(handler, ConversationHandler):
                continue
            try:
                key = handler._get_key(update)
                handler._conversations.pop(key, None)
            except Exception:
                logging.exception("Не удалось сбросить диалог при переходе в другой раздел")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN.")

    init_receiving_storage()
    init_returns_storage()
    init_consumables_storage()
    init_marking_storage()
    init_payroll_sheet()
    init_vacation_storage()
    init_daily_summary_storage()
    init_schedule_sheet()
    init_tasks_storage()  # Источник задач для полной логики расчёта ЗП.
    init_module_control_storage()

    request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30, pool_timeout=30)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).get_updates_request(request).build()
    app.add_error_handler(error_handler)

    # Клавиатура в панели ввода работает из любого незавершённого диалога.
    menu_pattern = "^(?:" + "|".join(re.escape(label) for label in REPLY_MENU) + ")$"
    app.add_handler(CallbackQueryHandler(reset_conversations_on_navigation, pattern=r"^(section:|menu:start$)"), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(menu_pattern), reset_conversations_on_navigation), group=-4)
    app.add_handler(CommandHandler("start", reset_conversations_on_navigation), group=-4)

    app.add_handler(CallbackQueryHandler(access_guard), group=-3)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND
                                   & ~filters.Regex(r"^/(start|whoami|whereami|wehereami)(\s|$)"), access_guard), group=-3)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, access_guard), group=-3)
    app.add_handler(CallbackQueryHandler(role_guard), group=-2)
    app.add_handler(CallbackQueryHandler(module_access_guard), group=-1)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, module_access_guard), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["whereami", "wehereami"], whereami))
    app.add_handler(CommandHandler("db_status", db_status))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & filters.Regex(menu_pattern), open_reply_section))

    for factory in (
        get_admin_panel_handlers, get_employee_handlers, get_payroll_handlers,
        get_schedule_handlers, get_consumables_handlers, get_marking_handlers,
    ):
        for handler in factory():
            app.add_handler(handler)
    app.add_handler(get_marking_search_handler())
    app.add_handler(get_loaded_mind_receiving_handler())
    for handler in get_loaded_mind_report_handlers():
        app.add_handler(handler)
    app.add_handler(get_loaded_mind_returns_handler())
    app.add_handler(get_loaded_mind_returns_admin_handler())

    app.add_handler(CallbackQueryHandler(show_main_menu, pattern=r"^menu:start$"))
    app.add_handler(CallbackQueryHandler(show_receiving_menu, pattern=r"^section:receiving$"))
    app.add_handler(CallbackQueryHandler(show_returns_menu, pattern=r"^section:returns$"))
    app.add_handler(CallbackQueryHandler(show_marking_menu, pattern=r"^section:marking$"))
    app.add_handler(CallbackQueryHandler(show_employees_menu, pattern=r"^section:employees$"))

    logging.info("Loaded Mind bot started")
    app.run_polling(bootstrap_retries=10)


if __name__ == "__main__":
    main()
