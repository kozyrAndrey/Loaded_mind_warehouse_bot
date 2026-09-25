import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler

from core.keyboards import build_main_menu_keyboard
from core.module_control import (
    MODULES,
    get_module_states,
    list_module_audit,
    set_module_enabled,
)
from modules.employees.roles import has_any_role, has_role
from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager


logger = logging.getLogger(__name__)


def _current_employee(update):
    return find_employee_for_telegram_user(update.effective_user)


def _is_admin(update):
    return has_any_role(_current_employee(update), {"admin", "warehouse_manager"})


def module_admin_keyboard(states):
    rows = []
    for module_key, label in MODULES.items():
        enabled = states.get(module_key, True)
        rows.append(
            [
                InlineKeyboardButton(
                    f"{'✅' if enabled else '⛔️'} {label}",
                    callback_data=f"admin:module:{module_key}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton("🕘 Последние изменения", callback_data="admin:audit")],
            [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _deny_admin(update):
    query = update.callback_query
    await query.answer("Панель доступна администратору и руководителю склада.", show_alert=True)
    employee = _current_employee(update)
    await query.edit_message_text(
        "⛔️ Недостаточно прав.",
        reply_markup=build_main_menu_keyboard(
            manager=is_manager(employee),
            admin=has_role(employee, "admin"),
        ),
    )


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(update):
        await _deny_admin(update)
        return ConversationHandler.END

    await query.answer()
    context.user_data.clear()
    try:
        states = get_module_states(force=True)
    except Exception:
        logger.exception("Не удалось открыть управление модулями")
        await query.edit_message_text(
            "⚠️ Не удалось загрузить настройки модулей. Попробуйте ещё раз позже.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")]]
            ),
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "⚙️ Управление модулями\n\n"
        "Нажмите на раздел, чтобы сразу включить или отключить его. "
        "Отключённый раздел исчезнет из меню, его старые кнопки и фоновые задания перестанут работать.",
        reply_markup=module_admin_keyboard(states),
    )
    return ConversationHandler.END


async def toggle_module(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(update):
        await _deny_admin(update)
        return ConversationHandler.END

    module_key = query.data.rsplit(":", 1)[-1]
    if module_key not in MODULES:
        await query.answer("Неизвестный модуль.", show_alert=True)
        return ConversationHandler.END

    try:
        states = get_module_states(force=True)
        enabled = not states.get(module_key, True)
        set_module_enabled(module_key, enabled, update.effective_user)
        states[module_key] = enabled
    except Exception:
        logger.exception("Не удалось изменить состояние модуля %s", module_key)
        await query.answer("Не удалось сохранить настройку.", show_alert=True)
        return ConversationHandler.END

    await query.answer(
        f"{MODULES[module_key]}: {'включён' if enabled else 'отключён'}",
        show_alert=True,
    )
    await query.edit_message_reply_markup(reply_markup=module_admin_keyboard(states))
    return ConversationHandler.END


async def show_module_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(update):
        await _deny_admin(update)
        return ConversationHandler.END

    await query.answer()
    try:
        rows = list_module_audit(limit=10)
    except Exception:
        logger.exception("Не удалось прочитать журнал управления модулями")
        await query.answer("Не удалось загрузить журнал.", show_alert=True)
        return ConversationHandler.END

    lines = ["🕘 Последние изменения модулей"]
    if not rows:
        lines.extend(["", "Изменений пока нет."])
    for row in rows:
        timestamp = row["changed_at"].strftime("%d.%m.%Y %H:%M")
        actor = row["changed_by_name"] or row["changed_by_user_id"] or "—"
        action = "включён" if row["enabled"] else "отключён"
        lines.extend(["", f"{timestamp} · {MODULES.get(row['module_key'], row['module_key'])}", f"{action} · {actor}"])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ К управлению", callback_data="section:admin")]]
        ),
    )
    return ConversationHandler.END


def get_admin_panel_handlers():
    return [
        CallbackQueryHandler(show_admin_panel, pattern=r"^section:admin$"),
        CallbackQueryHandler(toggle_module, pattern=r"^admin:module:[a-z_]+$"),
        CallbackQueryHandler(show_module_audit, pattern=r"^admin:audit$"),
    ]
