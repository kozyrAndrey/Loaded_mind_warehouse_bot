import logging
import tempfile
from datetime import datetime, time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
)

from config import GROUP_CHAT_ID, SCHEDULE_EXPORT_TOPIC_ID, SCHEDULE_REMINDER_TOPIC_ID
from core.keyboards import build_main_menu_keyboard
from core.module_control import is_module_enabled
from modules.payroll.google_sheets import find_employee_for_telegram_user, get_employee_by_id, get_employees
from modules.employees.roles import has_role
from modules.schedule.config import (
    MSK_TZ,
    SHIFT_TIMES,
    can_employee_submit_schedule,
    current_week_start,
    date_to_str,
    day_label,
    format_week_range,
    format_week_range_full,
    get_reminder_mentions,
    get_schedule_employee_by_id,
    get_schedule_employees,
    is_schedule_manager,
    next_week_start,
    parse_date,
    today_msk,
    week_dates,
)
from modules.schedule.excel import create_schedule_xlsx
from modules.schedule.google_sheets import (
    append_schedule_export,
    get_active_schedule_reminder,
    get_latest_schedule_export,
    get_missing_schedule_employees,
    get_next_export_version,
    mark_schedule_export_status,
    mark_schedule_reminder_status,
    rebuild_current_schedule_sheet,
    schedule_has_submission,
    get_schedule_matrix,
    set_duty_for_day,
    set_week_duties,
    suggest_week_duties,
    upsert_employee_week_schedule,
    upsert_schedule_day,
    upsert_schedule_reminder,
)

(
    SCHEDULE_SELECT_DAYS,
    SCHEDULE_SELECT_TIME,
    SCHEDULE_CONFIRM,
    SCHEDULE_EDIT_EMPLOYEE,
    SCHEDULE_EDIT_DAY,
    SCHEDULE_EDIT_ACTION,
    SCHEDULE_EDIT_TIME,
    SCHEDULE_DUTY_CONFIRM,
    SCHEDULE_EDIT_NEXT,
) = range(900, 909)

SCHEDULE_MANAGER_WEEK = 909
SCHEDULE_DUTY_DAY = 910
SCHEDULE_DUTY_EMPLOYEE = 911


# ============================================================
# КЛАВИАТУРЫ
# ============================================================


def schedule_menu_keyboard(employee):
    manager = is_schedule_manager(employee)
    rows = []

    if manager:
        rows.extend(
            [
                [InlineKeyboardButton("📅 Составить расписание сотрудникам", callback_data="sch:edit")],
                [InlineKeyboardButton("👀 Посмотреть расписание", callback_data="sch:view_all")],
                [InlineKeyboardButton("📤 Выгрузить расписание в тему", callback_data="sch:export_topic")],
                [InlineKeyboardButton("🧹 Назначить дежурных", callback_data="sch:duties")],
            ]
        )

    rows.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")])
    return InlineKeyboardMarkup(rows)


def schedule_nav_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")]]
    )


def day_selection_keyboard(selected_dates, week_start):
    selected_dates = set(selected_dates or [])
    rows = []
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        mark = "✅" if date_str in selected_dates else "⬜️"
        rows.append([InlineKeyboardButton(f"{mark} {day_label(day)}", callback_data=f"schday:{date_str}")])

    rows.append([InlineKeyboardButton("✅ Завершить выбор дней", callback_data="sch:finish_days")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")])
    return InlineKeyboardMarkup(rows)


def shift_time_keyboard(date_str, back_target="time"):
    rows = []
    for time_value in SHIFT_TIMES:
        compact = time_value.replace(":", "")
        rows.append([InlineKeyboardButton(time_value, callback_data=f"schtime:{date_str}:{compact}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"sch:back:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Подтвердить", callback_data="sch:confirm")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="sch:back:confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")],
        ]
    )


def employees_keyboard(prefix="schemp", back_target=None):
    from modules.schedule.config import get_schedule_employees

    rows = []
    for employee in get_schedule_employees():
        rows.append([InlineKeyboardButton(employee["full_name"], callback_data=f"{prefix}:{employee['employee_id']}")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"sch:back:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")])
    return InlineKeyboardMarkup(rows)


def edit_day_keyboard(week_start):
    rows = []
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        rows.append([InlineKeyboardButton(day_label(day), callback_data=f"scheditday:{date_str}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="sch:back:edit_employee")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")])
    return InlineKeyboardMarkup(rows)


def bulk_edit_keyboard(week_start, shifts, preferred_times=None):
    preferred_times = preferred_times or {}
    rows = []
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        shift_time = str((shifts or {}).get(date_str) or "").strip()
        preferred_time = preferred_times.get(date_str) or shift_time or SHIFT_TIMES[0]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{'✅' if shift_time else '⬜️'} {day_label(day)}",
                    callback_data=f"scheditbulk:toggle:{date_str}",
                ),
                InlineKeyboardButton(
                    shift_time or preferred_time,
                    callback_data=f"scheditbulk:time:{date_str}",
                ),
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton("✅ Сохранить все изменения", callback_data="scheditbulk:save")],
            [InlineKeyboardButton("⬅️ Назад к сотрудникам", callback_data="sch:back:edit_employee")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def bulk_edit_text(employee, week_start):
    return (
        f"Сотрудник: {employee['full_name']}\n"
        f"Неделя: {format_week_range_full(week_start)}\n\n"
        "Нажимайте на день, чтобы добавить или убрать смену. "
        "Кнопка времени справа переключает время выхода 10:00/15:00.\n\n"
        "Все изменения применятся одновременно после нажатия «Сохранить все изменения»."
    )


def edit_action_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🕒 Изменить время", callback_data="schedit:set")],
            [InlineKeyboardButton("🗑 Убрать смену", callback_data="schedit:clear")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="sch:back:edit_day")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")],
        ]
    )


def edit_next_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Внести еще изменение", callback_data="schedit:again")],
            [InlineKeyboardButton("✅ Завершить и обновить выгрузку", callback_data="schedit:export_final")],
            [InlineKeyboardButton("📅 В модуль расписания", callback_data="sch:menu")],
        ]
    )


def manager_week_keyboard(action):
    current_start = current_week_start()
    next_start = next_week_start()

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"📅 Текущая: {format_week_range(current_start)}",
                    callback_data=f"schweek:{action}:current",
                )
            ],
            [
                InlineKeyboardButton(
                    f"📅 Следующая: {format_week_range(next_start)}",
                    callback_data=f"schweek:{action}:next",
                )
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")],
        ]
    )


def duty_overview_text(week_start, pending_duties):
    employees_by_id = {
        employee["employee_id"]: employee for employee in get_schedule_employees()
    }
    lines = [
        f"Дежурные на {format_week_range(week_start)}:",
        "",
        "Нажмите день, чтобы назначить или заменить дежурного.",
        "",
    ]
    for day in week_dates(week_start):
        employee_id = pending_duties.get(date_to_str(day), "")
        employee = employees_by_id.get(employee_id)
        lines.append(
            f"{day_label(day)} — {employee['full_name'] if employee else 'без дежурного'}"
        )
    return "\n".join(lines)


def duty_days_keyboard(week_start, pending_duties):
    employees_by_id = {
        employee["employee_id"]: employee for employee in get_schedule_employees()
    }
    rows = []
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        employee = employees_by_id.get(pending_duties.get(date_str, ""))
        label = f"{day_label(day)} — {employee['full_name'] if employee else 'нет'}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"schduty:day:{date_str}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton("🔄 Предложить автоматически", callback_data="schduty:auto")],
            [InlineKeyboardButton("✅ Завершить и обновить выгрузку", callback_data="schduty:confirm")],
            [InlineKeyboardButton("⬅️ Назад к выбору недели", callback_data="sch:back:manager_week")],
            [InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def duty_employee_keyboard(week_start, date_str):
    employees, dates, schedule, current_duties = get_schedule_matrix(week_start)
    working = [
        employee
        for employee in employees
        if schedule.get(employee["employee_id"], {}).get(date_str)
    ]

    rows = []
    for employee in working:
        rows.append(
            [
                InlineKeyboardButton(
                    employee["full_name"],
                    callback_data=f"schduty:set:{date_str}:{employee['employee_id']}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("Без дежурного", callback_data=f"schduty:set:{date_str}:none")]
    )
    rows.append([InlineKeyboardButton("⬅️ Назад к дням", callback_data="schduty:back")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="sch:cancel")])
    return InlineKeyboardMarkup(rows), working


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================


def current_employee(update):
    return find_employee_for_telegram_user(update.effective_user)


async def send_schedule_menu(target, employee):
    if not employee:
        text = "Ваш Telegram ID не найден в справочнике сотрудников. Обратитесь к руководителю."
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=build_main_menu_keyboard())
        else:
            await target.reply_text(text, reply_markup=build_main_menu_keyboard())
        return

    text = "📅 Расписание\n\nВыберите действие:"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=schedule_menu_keyboard(employee))
    else:
        await target.reply_text(text, reply_markup=schedule_menu_keyboard(employee))


def build_schedule_summary(employee, week_start, shifts):
    lines = [f"Проверьте расписание на {format_week_range(week_start)}:", "", employee["full_name"]]
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        lines.append(f"{day_label(day)} — {shifts.get(date_str) or 'выходной'}")
    return "\n".join(lines)


async def export_schedule_excel_to_user(context, chat_id, week_start):
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = create_schedule_xlsx(week_start, tmp.name)

    with open(path, "rb") as file:
        await context.bot.send_document(
            chat_id=chat_id,
            document=file,
            filename=f"schedule_{format_week_range(week_start)}.xlsx",
            caption=f"📅 Расписание на {format_week_range_full(week_start)}",
        )


async def export_schedule_excel_to_topic(context, week_start, sent_by_employee, note=""):
    if not GROUP_CHAT_ID or not SCHEDULE_EXPORT_TOPIC_ID:
        return "GROUP_CHAT_ID или SCHEDULE_EXPORT_TOPIC_ID не настроены."

    previous_export = get_latest_schedule_export(week_start)
    version = get_next_export_version(week_start)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = create_schedule_xlsx(week_start, tmp.name)

    caption_lines = [
        f"📅 Расписание на {format_week_range_full(week_start)}",
        f"Версия: {version}",
        f"Выгрузил: {sent_by_employee['full_name'] if sent_by_employee else '—'}",
    ]
    if note:
        caption_lines.append(note)
    caption = "\n".join(caption_lines)

    filename = f"schedule_{format_week_range(week_start)}_v{version}.xlsx"
    with open(path, "rb") as file:
        message = await context.bot.send_document(
            chat_id=int(GROUP_CHAT_ID),
            message_thread_id=int(SCHEDULE_EXPORT_TOPIC_ID),
            document=file,
            filename=filename,
            caption=caption,
        )

    append_schedule_export(
        week_start=week_start,
        version=version,
        chat_id=int(GROUP_CHAT_ID),
        thread_id=int(SCHEDULE_EXPORT_TOPIC_ID),
        message_id=message.message_id,
        filename=filename,
        sent_by=sent_by_employee["full_name"] if sent_by_employee else "",
    )

    replacement_note = ""
    if previous_export and previous_export.get("message_id"):
        try:
            await context.bot.delete_message(
                chat_id=int(previous_export["chat_id"]),
                message_id=int(previous_export["message_id"]),
            )
            mark_schedule_export_status(previous_export["row_index"], "replaced")
        except Exception:
            logging.exception("Не удалось удалить предыдущую выгрузку расписания")
            replacement_note = (
                "\nПредыдущий файл не удалось удалить автоматически ⚠️"
            )

    return (
        f"Расписание отправлено в тему Telegram ✅\n"
        f"Версия: {version}{replacement_note}"
    )


# ============================================================
# МЕНЮ
# ============================================================


async def schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    employee = current_employee(update)
    await send_schedule_menu(query, employee)
    return ConversationHandler.END


async def schedule_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    employee = current_employee(update)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await send_schedule_menu(query, employee)
    else:
        await send_schedule_menu(update.message, employee)

    return ConversationHandler.END


# ============================================================
# СОЗДАТЬ РАСПИСАНИЕ
# ============================================================


async def schedule_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee = current_employee(update)
    if not can_employee_submit_schedule(employee):
        await query.edit_message_text(
            "Вы не участвуете в составлении расписания.",
            reply_markup=schedule_menu_keyboard(employee) if employee else build_main_menu_keyboard(),
        )
        return ConversationHandler.END

    week_start = next_week_start()

    if schedule_has_submission(employee["employee_id"], week_start) and not is_schedule_manager(employee):
        await query.edit_message_text(
            "Вы уже отправили расписание на следующую неделю. Для изменения обратитесь к руководителю.",
            reply_markup=schedule_menu_keyboard(employee),
        )
        return ConversationHandler.END

    context.user_data["schedule_employee_id"] = employee["employee_id"]
    context.user_data["schedule_week_start"] = date_to_str(week_start)
    context.user_data["schedule_selected_dates"] = []
    context.user_data["schedule_shifts"] = {}

    await query.edit_message_text(
        f"Составить расписание на {format_week_range(week_start)}.\n\n"
        "Выберите рабочие дни. Невыбранные дни будут считаться выходными.",
        reply_markup=day_selection_keyboard([], week_start),
    )
    return SCHEDULE_SELECT_DAYS


async def schedule_day_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date_str = query.data.replace("schday:", "")
    selected = set(context.user_data.get("schedule_selected_dates", []))

    if date_str in selected:
        selected.remove(date_str)
    else:
        selected.add(date_str)

    selected = sorted(selected, key=lambda value: week_dates(context.user_data["schedule_week_start"]).index(parse_date(value)))
    context.user_data["schedule_selected_dates"] = selected
    week_start = parse_date(context.user_data["schedule_week_start"])

    await query.edit_message_text(
        f"Составить расписание на {format_week_range(week_start)}.\n\n"
        "Выберите рабочие дни. Невыбранные дни будут считаться выходными.",
        reply_markup=day_selection_keyboard(selected, week_start),
    )
    return SCHEDULE_SELECT_DAYS


async def schedule_days_finished(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_dates = context.user_data.get("schedule_selected_dates", [])
    context.user_data["schedule_selected_dates"] = selected_dates
    context.user_data["schedule_current_time_index"] = 0

    if not selected_dates:
        return await schedule_show_confirm(query, context)

    first_date = selected_dates[0]
    await query.edit_message_text(
        f"Выберите время выхода на смену для {day_label(first_date)}:",
        reply_markup=shift_time_keyboard(first_date),
    )
    return SCHEDULE_SELECT_TIME


async def schedule_time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, date_str, compact_time = query.data.split(":")
    time_value = f"{compact_time[:2]}:{compact_time[2:]}"
    shifts = context.user_data.setdefault("schedule_shifts", {})
    shifts[date_str] = time_value

    selected_dates = context.user_data.get("schedule_selected_dates", [])
    index = int(context.user_data.get("schedule_current_time_index", 0)) + 1
    context.user_data["schedule_current_time_index"] = index

    if index >= len(selected_dates):
        return await schedule_show_confirm(query, context)

    next_date = selected_dates[index]
    await query.edit_message_text(
        f"Выберите время выхода на смену для {day_label(next_date)}:",
        reply_markup=shift_time_keyboard(next_date),
    )
    return SCHEDULE_SELECT_TIME


async def schedule_show_confirm(target, context):
    employee = get_employee_by_id(context.user_data["schedule_employee_id"])
    week_start = parse_date(context.user_data["schedule_week_start"])
    shifts = context.user_data.get("schedule_shifts", {})
    text = build_schedule_summary(employee, week_start, shifts)
    await target.edit_message_text(text, reply_markup=confirm_keyboard())
    return SCHEDULE_CONFIRM


async def schedule_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee = get_employee_by_id(context.user_data["schedule_employee_id"])
    week_start = parse_date(context.user_data["schedule_week_start"])
    shifts = context.user_data.get("schedule_shifts", {})
    updated_by = employee["full_name"] if employee else str(update.effective_user.id)

    upsert_employee_week_schedule(employee, week_start, shifts, updated_by)
    context.user_data.clear()

    await query.edit_message_text(
        "Расписание сохранено ✅",
        reply_markup=schedule_menu_keyboard(employee),
    )
    return ConversationHandler.END


# ============================================================
# ПОСМОТРЕТЬ РАСПИСАНИЕ
# ============================================================


async def schedule_view_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee = current_employee(update)

    if not is_schedule_manager(employee):
        await query.edit_message_text("Недостаточно прав.", reply_markup=schedule_menu_keyboard(employee))
        return ConversationHandler.END

    context.user_data["schedule_manager_action"] = "view"
    await query.edit_message_text(
        "За какую неделю посмотреть расписание?",
        reply_markup=manager_week_keyboard("view"),
    )
    return SCHEDULE_MANAGER_WEEK


async def schedule_export_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee = current_employee(update)

    if not is_schedule_manager(employee):
        await query.edit_message_text("Недостаточно прав.", reply_markup=schedule_menu_keyboard(employee))
        return ConversationHandler.END

    context.user_data["schedule_manager_action"] = "export"
    await query.edit_message_text(
        "За какую неделю выгрузить расписание?",
        reply_markup=manager_week_keyboard("export"),
    )
    return SCHEDULE_MANAGER_WEEK


async def schedule_manager_week_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    manager_employee = current_employee(update)

    if not is_schedule_manager(manager_employee):
        await query.edit_message_text("Недостаточно прав.", reply_markup=schedule_menu_keyboard(manager_employee))
        return ConversationHandler.END

    _, action, selected_week = query.data.split(":")
    week_start = current_week_start() if selected_week == "current" else next_week_start()
    context.user_data["schedule_week_start"] = date_to_str(week_start)
    context.user_data["schedule_manager_action"] = action

    if action == "view":
        rebuild_current_schedule_sheet(week_start)
        await export_schedule_excel_to_user(context, query.from_user.id, week_start)
        context.user_data.clear()
        await query.edit_message_text(
            "Файл расписания отправлен в личный чат ✅",
            reply_markup=schedule_menu_keyboard(manager_employee),
        )
        return ConversationHandler.END

    if action == "export":
        rebuild_current_schedule_sheet(week_start)
        status = await export_schedule_excel_to_topic(
            context,
            week_start,
            manager_employee,
            note="Актуальная версия расписания",
        )
        context.user_data.clear()
        await query.edit_message_text(status, reply_markup=schedule_menu_keyboard(manager_employee))
        return ConversationHandler.END

    if action == "edit":
        await query.edit_message_text(
            f"Изменение расписания на {format_week_range_full(week_start)}\n\n"
            "Выберите сотрудника:",
            reply_markup=employees_keyboard("schemp", "manager_week"),
        )
        return SCHEDULE_EDIT_EMPLOYEE

    if action == "duties":
        return await schedule_start_duty_editing(query, context, week_start)

    await query.edit_message_text("Неизвестное действие.", reply_markup=schedule_menu_keyboard(manager_employee))
    return ConversationHandler.END


# ============================================================
# ИЗМЕНИТЬ РАСПИСАНИЕ
# ============================================================


async def schedule_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee = current_employee(update)

    if not is_schedule_manager(employee):
        await query.edit_message_text("Недостаточно прав.", reply_markup=schedule_menu_keyboard(employee))
        return ConversationHandler.END

    context.user_data["schedule_manager_action"] = "edit"
    await query.edit_message_text(
        "Какую неделю нужно изменить?",
        reply_markup=manager_week_keyboard("edit"),
    )
    return SCHEDULE_MANAGER_WEEK


async def schedule_edit_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("schemp:", "")
    employee = get_schedule_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=employees_keyboard("schemp"))
        return SCHEDULE_EDIT_EMPLOYEE

    context.user_data["edit_employee_id"] = employee_id
    week_start = parse_date(context.user_data["schedule_week_start"])
    _, dates, schedule, _ = get_schedule_matrix(week_start)
    current_shifts = {
        date_to_str(day): schedule.get(employee_id, {}).get(date_to_str(day), "")
        for day in dates
    }
    context.user_data["edit_original_shifts"] = dict(current_shifts)
    context.user_data["edit_pending_shifts"] = dict(current_shifts)
    context.user_data["edit_preferred_times"] = {
        date_str: shift_time or SHIFT_TIMES[0]
        for date_str, shift_time in current_shifts.items()
    }
    await query.edit_message_text(
        bulk_edit_text(employee, week_start),
        reply_markup=bulk_edit_keyboard(
            week_start,
            current_shifts,
            context.user_data["edit_preferred_times"],
        ),
    )
    return SCHEDULE_EDIT_DAY


async def schedule_bulk_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("scheditbulk:", "", 1)
    employee = get_schedule_employee_by_id(context.user_data.get("edit_employee_id"))
    week_start = parse_date(context.user_data["schedule_week_start"])
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=employees_keyboard("schemp"))
        return SCHEDULE_EDIT_EMPLOYEE

    pending = context.user_data.setdefault("edit_pending_shifts", {})
    preferred = context.user_data.setdefault("edit_preferred_times", {})

    if action.startswith("toggle:"):
        date_str = action.replace("toggle:", "", 1)
        pending[date_str] = "" if pending.get(date_str) else preferred.get(date_str, SHIFT_TIMES[0])
    elif action.startswith("time:"):
        date_str = action.replace("time:", "", 1)
        current = preferred.get(date_str) or pending.get(date_str) or SHIFT_TIMES[0]
        next_time = SHIFT_TIMES[(SHIFT_TIMES.index(current) + 1) % len(SHIFT_TIMES)] if current in SHIFT_TIMES else SHIFT_TIMES[0]
        preferred[date_str] = next_time
        if pending.get(date_str):
            pending[date_str] = next_time
    elif action == "save":
        return await schedule_bulk_edit_save(query, context, employee, week_start)
    else:
        await query.answer("Неизвестное действие.", show_alert=True)

    await query.edit_message_reply_markup(
        reply_markup=bulk_edit_keyboard(week_start, pending, preferred),
    )
    return SCHEDULE_EDIT_DAY


async def schedule_bulk_edit_save(query, context, employee, week_start):
    manager_employee = find_employee_for_telegram_user(query.from_user)
    updated_by = manager_employee["full_name"] if manager_employee else "manager"
    original = context.user_data.get("edit_original_shifts") or {}
    pending = context.user_data.get("edit_pending_shifts") or {}

    upsert_employee_week_schedule(employee, week_start, pending, updated_by)

    _, _, _, duty_by_date = get_schedule_matrix(week_start)
    removed_duties = []
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        if original.get(date_str) and not pending.get(date_str) and duty_by_date.get(date_str) == employee["employee_id"]:
            set_duty_for_day(week_start, day, None, updated_by)
            removed_duties.append(day_label(day))
    if removed_duties:
        rebuild_current_schedule_sheet(week_start)

    changed = [
        day_label(day)
        for day in week_dates(week_start)
        if original.get(date_to_str(day), "") != pending.get(date_to_str(day), "")
    ]
    duty_text = (
        "\nСняты дежурства: " + ", ".join(removed_duties) + "."
        if removed_duties
        else ""
    )
    await query.edit_message_text(
        "Расписание сотрудника сохранено ✅\n\n"
        f"Сотрудник: {employee['full_name']}\n"
        f"Изменено дней: {len(changed)}"
        f"{duty_text}\n\n"
        "Можно изменить другого сотрудника или завершить и обновить выгрузку.",
        reply_markup=edit_next_keyboard(),
    )
    return SCHEDULE_EDIT_NEXT


async def schedule_edit_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    date_str = query.data.replace("scheditday:", "")
    context.user_data["edit_date"] = date_str
    await query.edit_message_text(f"День: {day_label(date_str)}\nЧто сделать?", reply_markup=edit_action_keyboard())
    return SCHEDULE_EDIT_ACTION


async def schedule_edit_action_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("schedit:", "")
    context.user_data["edit_action"] = action

    if action == "clear":
        return await schedule_apply_edit(query, context, "")

    date_str = context.user_data["edit_date"]
    await query.edit_message_text(
        f"Выберите новое время для {day_label(date_str)}:",
        reply_markup=shift_time_keyboard(date_str, "edit_action"),
    )
    return SCHEDULE_EDIT_TIME


async def schedule_edit_time_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, date_str, compact_time = query.data.split(":")
    time_value = f"{compact_time[:2]}:{compact_time[2:]}"
    return await schedule_apply_edit(query, context, time_value)


async def schedule_apply_edit(query, context, shift_time):
    manager_employee = find_employee_for_telegram_user(query.from_user)
    employee = get_schedule_employee_by_id(context.user_data["edit_employee_id"])
    week_start = parse_date(context.user_data["schedule_week_start"])
    date_str = context.user_data["edit_date"]
    day = parse_date(date_str)

    updated_by = manager_employee["full_name"] if manager_employee else "manager"
    upsert_schedule_day(
        employee,
        week_start,
        day,
        shift_time,
        updated_by,
    )

    duty_removed = False
    if not shift_time:
        _, _, _, duty_by_date = get_schedule_matrix(week_start)
        if duty_by_date.get(date_str) == employee["employee_id"]:
            set_duty_for_day(week_start, day, None, updated_by)
            duty_removed = True

    rebuild_current_schedule_sheet(week_start)

    action_text = "смена убрана" if not shift_time else f"новое время: {shift_time}"
    duty_text = (
        "\nДежурство на этот день снято, так как смена сотрудника убрана."
        if duty_removed
        else ""
    )
    await query.edit_message_text(
        "Изменение сохранено ✅\n\n"
        f"Сотрудник: {employee['full_name']}\n"
        f"День: {day_label(day)}\n"
        f"Изменение: {action_text}{duty_text}\n\n"
        "Можно внести еще изменения. Когда закончите — нажмите «Завершить и обновить выгрузку».",
        reply_markup=edit_next_keyboard(),
    )
    return SCHEDULE_EDIT_NEXT


async def schedule_edit_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "Выберите сотрудника для следующего изменения:",
        reply_markup=employees_keyboard("schemp", "manager_week"),
    )
    return SCHEDULE_EDIT_EMPLOYEE


async def schedule_edit_export_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    manager_employee = current_employee(update)
    week_start = parse_date(context.user_data["schedule_week_start"])

    try:
        rebuild_current_schedule_sheet(week_start)
        export_status = await export_schedule_excel_to_topic(
            context,
            week_start,
            manager_employee,
            note="Итоговая версия после изменений",
        )
    except Exception as error:
        logging.exception("Не удалось выгрузить итоговое расписание в тему")
        export_status = f"Изменения сохранены, но файл в тему не отправлен ⚠️\nОшибка: {error}"

    context.user_data.clear()
    await query.edit_message_text(
        export_status,
        reply_markup=schedule_menu_keyboard(manager_employee),
    )
    return ConversationHandler.END


async def schedule_edit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    manager_employee = current_employee(update)
    context.user_data.clear()

    await query.edit_message_text(
        "Изменения сохранены ✅\n\nИтоговое расписание в тему не выгружалось.",
        reply_markup=schedule_menu_keyboard(manager_employee),
    )
    return ConversationHandler.END


# ============================================================
# ДЕЖУРНЫЕ
# ============================================================


async def schedule_duties_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee = current_employee(update)

    if not is_schedule_manager(employee):
        await query.edit_message_text("Недостаточно прав.", reply_markup=schedule_menu_keyboard(employee))
        return ConversationHandler.END

    context.user_data["schedule_manager_action"] = "duties"
    await query.edit_message_text(
        "Для какой недели назначить или изменить дежурных?",
        reply_markup=manager_week_keyboard("duties"),
    )
    return SCHEDULE_MANAGER_WEEK


async def schedule_start_duty_editing(query, context, week_start):
    suggested = suggest_week_duties(week_start)
    _, _, _, current_duties = get_schedule_matrix(week_start)

    pending = {}
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        existing_employee_id = current_duties.get(date_str, "")
        proposed = suggested.get(date_str)
        pending[date_str] = (
            existing_employee_id
            or (proposed["employee_id"] if proposed else "")
        )

    context.user_data["pending_duties"] = pending
    await query.edit_message_text(
        duty_overview_text(week_start, pending),
        reply_markup=duty_days_keyboard(week_start, pending),
    )
    return SCHEDULE_DUTY_DAY


async def schedule_duty_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    date_str = query.data.replace("schduty:day:", "")
    week_start = parse_date(context.user_data["schedule_week_start"])
    keyboard, working = duty_employee_keyboard(week_start, date_str)

    note = (
        "Выберите дежурного среди сотрудников на смене:"
        if working
        else "На этот день нет сотрудников на смене. Можно оставить день без дежурного."
    )
    await query.edit_message_text(
        f"{day_label(date_str)}\n\n{note}",
        reply_markup=keyboard,
    )
    return SCHEDULE_DUTY_EMPLOYEE


async def schedule_duty_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, _, date_str, employee_id = query.data.split(":")
    if employee_id == "none":
        employee_id = ""

    pending = context.user_data.setdefault("pending_duties", {})
    pending[date_str] = employee_id
    week_start = parse_date(context.user_data["schedule_week_start"])

    await query.edit_message_text(
        duty_overview_text(week_start, pending),
        reply_markup=duty_days_keyboard(week_start, pending),
    )
    return SCHEDULE_DUTY_DAY


async def schedule_duties_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    week_start = parse_date(context.user_data["schedule_week_start"])
    pending = context.user_data.get("pending_duties", {})

    await query.edit_message_text(
        duty_overview_text(week_start, pending),
        reply_markup=duty_days_keyboard(week_start, pending),
    )
    return SCHEDULE_DUTY_DAY


async def schedule_duties_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    week_start = parse_date(context.user_data["schedule_week_start"])
    suggested = suggest_week_duties(week_start)
    pending = {
        date_str: duty["employee_id"] if duty else ""
        for date_str, duty in suggested.items()
    }
    context.user_data["pending_duties"] = pending

    await query.edit_message_text(
        duty_overview_text(week_start, pending),
        reply_markup=duty_days_keyboard(week_start, pending),
    )
    return SCHEDULE_DUTY_DAY


async def schedule_duties_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    manager_employee = current_employee(update)
    week_start = parse_date(context.user_data["schedule_week_start"])
    raw = context.user_data.get("pending_duties", {})
    duties = {}

    for day in week_dates(week_start):
        date_str = date_to_str(day)
        employee_id = raw.get(date_str, "")
        duties[date_str] = get_schedule_employee_by_id(employee_id) if employee_id else None

    set_week_duties(week_start, duties, manager_employee["full_name"] if manager_employee else "manager")

    try:
        export_status = await export_schedule_excel_to_topic(
            context,
            week_start,
            manager_employee,
            note="Обновлены дежурные",
        )
    except Exception as error:
        logging.exception("Не удалось отправить расписание с дежурными в тему")
        export_status = f"Дежурные сохранены, но файл в тему не отправлен ⚠️\nОшибка: {error}"

    context.user_data.clear()
    await query.edit_message_text(
        "Дежурные сохранены ✅\n\n" + export_status,
        reply_markup=schedule_menu_keyboard(manager_employee),
    )
    return ConversationHandler.END


async def schedule_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("sch:back:", "", 1)

    if target in {"time", "confirm"}:
        selected = context.user_data.get("schedule_selected_dates", [])
        if not selected:
            week_start = parse_date(context.user_data["schedule_week_start"])
            await query.edit_message_text(
                f"Составить расписание на {format_week_range(week_start)}.\n\nВыберите рабочие дни.",
                reply_markup=day_selection_keyboard(selected, week_start),
            )
            return SCHEDULE_SELECT_DAYS
        current_index = int(context.user_data.get("schedule_current_time_index", 0))
        index = len(selected) - 1 if target == "confirm" else current_index - 1
        if index < 0:
            week_start = parse_date(context.user_data["schedule_week_start"])
            await query.edit_message_text(
                f"Составить расписание на {format_week_range(week_start)}.\n\nВыберите рабочие дни.",
                reply_markup=day_selection_keyboard(selected, week_start),
            )
            return SCHEDULE_SELECT_DAYS
        date_str = selected[index]
        context.user_data["schedule_current_time_index"] = index
        context.user_data.setdefault("schedule_shifts", {}).pop(date_str, None)
        await query.edit_message_text(
            f"Выберите время выхода на смену для {day_label(date_str)}:",
            reply_markup=shift_time_keyboard(date_str),
        )
        return SCHEDULE_SELECT_TIME

    if target == "manager_week":
        action = context.user_data.get("schedule_manager_action", "edit")
        labels = {
            "edit": "Какую неделю нужно изменить?",
            "duties": "Для какой недели назначить дежурных?",
        }
        context.user_data.pop("pending_duties", None)
        await query.edit_message_text(labels.get(action, "Выберите неделю:"), reply_markup=manager_week_keyboard(action))
        return SCHEDULE_MANAGER_WEEK
    if target == "edit_employee":
        context.user_data.pop("edit_employee_id", None)
        await query.edit_message_text("Выберите сотрудника:", reply_markup=employees_keyboard("schemp", "manager_week"))
        return SCHEDULE_EDIT_EMPLOYEE
    if target == "edit_day":
        week_start = parse_date(context.user_data["schedule_week_start"])
        employee = get_schedule_employee_by_id(context.user_data.get("edit_employee_id"))
        await query.edit_message_text(f"Сотрудник: {employee['full_name']}\nВыберите день:", reply_markup=edit_day_keyboard(week_start))
        return SCHEDULE_EDIT_DAY
    if target == "edit_action":
        date_str = context.user_data["edit_date"]
        await query.edit_message_text(f"День: {day_label(date_str)}\nЧто сделать?", reply_markup=edit_action_keyboard())
        return SCHEDULE_EDIT_ACTION

    return await schedule_cancel(update, context)


# ============================================================
# ПЯТНИЧНОЕ НАПОМИНАНИЕ
# ============================================================


def build_missing_schedule_text(missing_employees):
    mentions = []
    for employee in missing_employees:
        username = str(employee.get("telegram_username", "")).strip().lstrip("@")
        if username:
            mentions.append(f"@{username}")
        else:
            mentions.append(employee["full_name"])

    return "Заполните расписание❗️\n\n" + "\n".join(mentions)


async def delete_saved_schedule_reminder(context: ContextTypes.DEFAULT_TYPE, week_start):
    saved = get_active_schedule_reminder(week_start)
    if not saved:
        return False

    chat_id = saved.get("chat_id")
    message_id = saved.get("message_id")
    if not chat_id or not message_id:
        return False

    try:
        await context.bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
        return True
    except Exception:
        logging.exception("Не удалось удалить старое напоминание о расписании")
        return False


async def send_schedule_missing_reminder(context: ContextTypes.DEFAULT_TYPE, week_start):
    if not GROUP_CHAT_ID or not SCHEDULE_REMINDER_TOPIC_ID:
        logging.warning("Не настроены GROUP_CHAT_ID или SCHEDULE_REMINDER_TOPIC_ID для контроля расписания")
        return

    missing = get_missing_schedule_employees(week_start)

    # Всегда удаляем предыдущее напоминание, чтобы не засорять тему.
    await delete_saved_schedule_reminder(context, week_start)

    if not missing:
        mark_schedule_reminder_status(week_start, "completed")
        return

    message = await context.bot.send_message(
        chat_id=int(GROUP_CHAT_ID),
        message_thread_id=int(SCHEDULE_REMINDER_TOPIC_ID),
        text=build_missing_schedule_text(missing),
    )

    upsert_schedule_reminder(
        week_start=week_start,
        chat_id=int(GROUP_CHAT_ID),
        thread_id=int(SCHEDULE_REMINDER_TOPIC_ID),
        message_id=message.message_id,
        status="active",
    )


async def schedule_missing_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("schedule"):
        return
    current = today_msk()
    if current.weekday() != 4:
        return

    week_start = next_week_start(current)

    # В 09:00 готовим визуальный лист под следующую неделю.
    current_time = datetime.now(MSK_TZ).time()
    if current_time.hour == 9:
        try:
            rebuild_current_schedule_sheet(week_start)
        except Exception:
            logging.exception("Не удалось подготовить лист «Расписание» под новую неделю")

    await send_schedule_missing_reminder(context, week_start)


async def schedule_manager_overdue_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("schedule"):
        return
    current = today_msk()
    if current.weekday() != 4:
        return

    week_start = next_week_start(current)
    missing = get_missing_schedule_employees(week_start)

    if not missing:
        mark_schedule_reminder_status(week_start, "completed")
        return

    mark_schedule_reminder_status(week_start, "overdue")

    text = (
        "Не все сотрудники заполнили расписание до 19:00 ⚠️\n\n"
        + "\n".join(
            [
                f"@{str(employee.get('telegram_username', '')).strip().lstrip('@')}"
                if str(employee.get("telegram_username", "")).strip()
                else employee["full_name"]
                for employee in missing
            ]
        )
    )

    for employee in get_employees(include_inactive=False):
        if not has_role(employee, "warehouse_manager"):
            continue

        telegram_user_id = str(employee.get("telegram_user_id", "")).strip()
        if not telegram_user_id:
            continue

        try:
            await context.bot.send_message(chat_id=int(telegram_user_id), text=text)
        except Exception:
            logging.exception("Не удалось отправить руководителю личное сообщение о незаполненном расписании")


def setup_schedule_jobs(app):
    if not app.job_queue:
        logging.warning(
            "JobQueue не доступен. Установите зависимость python-telegram-bot[job-queue], "
            "иначе пятничное напоминание работать не будет."
        )
        return

    for hour in (9, 11, 13, 15, 17):
        app.job_queue.run_daily(
            schedule_missing_reminder_job,
            time=time(hour=hour, minute=0, tzinfo=MSK_TZ),
            name=f"schedule_missing_reminder_{hour}",
        )

    app.job_queue.run_daily(
        schedule_manager_overdue_job,
        time=time(hour=19, minute=0, tzinfo=MSK_TZ),
        name="schedule_manager_overdue",
    )


# ============================================================
# HANDLERS
# ============================================================


def get_schedule_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(schedule_create_start, pattern=r"^sch:create$"),
            CallbackQueryHandler(schedule_view_all, pattern=r"^sch:view_all$"),
            CallbackQueryHandler(schedule_export_topic, pattern=r"^sch:export_topic$"),
            CallbackQueryHandler(schedule_edit_start, pattern=r"^sch:edit$"),
            CallbackQueryHandler(schedule_duties_start, pattern=r"^sch:duties$"),
        ],
        states={
            SCHEDULE_MANAGER_WEEK: [
                CallbackQueryHandler(schedule_manager_week_selected, pattern=r"^schweek:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_SELECT_DAYS: [
                CallbackQueryHandler(schedule_day_toggle, pattern=r"^schday:"),
                CallbackQueryHandler(schedule_days_finished, pattern=r"^sch:finish_days$"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_SELECT_TIME: [
                CallbackQueryHandler(schedule_time_selected, pattern=r"^schtime:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_CONFIRM: [
                CallbackQueryHandler(schedule_confirm, pattern=r"^sch:confirm$"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_EDIT_EMPLOYEE: [
                CallbackQueryHandler(schedule_edit_employee_selected, pattern=r"^schemp:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_EDIT_DAY: [
                CallbackQueryHandler(schedule_bulk_edit_selected, pattern=r"^scheditbulk:"),
                CallbackQueryHandler(schedule_edit_day_selected, pattern=r"^scheditday:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_EDIT_ACTION: [
                CallbackQueryHandler(schedule_edit_action_selected, pattern=r"^schedit:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_EDIT_TIME: [
                CallbackQueryHandler(schedule_edit_time_selected, pattern=r"^schtime:"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_EDIT_NEXT: [
                CallbackQueryHandler(schedule_edit_again, pattern=r"^schedit:again$"),
                CallbackQueryHandler(schedule_edit_export_final, pattern=r"^schedit:export_final$"),
                CallbackQueryHandler(schedule_edit_done, pattern=r"^schedit:done$"),
                CallbackQueryHandler(schedule_menu, pattern=r"^sch:menu$"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_DUTY_DAY: [
                CallbackQueryHandler(schedule_duty_day_selected, pattern=r"^schduty:day:"),
                CallbackQueryHandler(schedule_duties_auto, pattern=r"^schduty:auto$"),
                CallbackQueryHandler(schedule_duties_confirm, pattern=r"^schduty:confirm$"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
            SCHEDULE_DUTY_EMPLOYEE: [
                CallbackQueryHandler(schedule_duty_employee_selected, pattern=r"^schduty:set:"),
                CallbackQueryHandler(schedule_duties_back, pattern=r"^schduty:back$"),
                CallbackQueryHandler(schedule_cancel, pattern=r"^sch:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(schedule_back, pattern=r"^sch:back:(time|confirm|manager_week|edit_employee|edit_day|edit_action)$")],
    )


def get_schedule_handlers():
    return [
        CallbackQueryHandler(schedule_menu, pattern=r"^section:schedule$"),
        CallbackQueryHandler(schedule_menu, pattern=r"^sch:menu$"),
        get_schedule_conversation_handler(),
    ]
