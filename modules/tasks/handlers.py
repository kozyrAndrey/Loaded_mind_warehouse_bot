import logging
from datetime import time, timedelta
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from config import GROUP_CHAT_ID, SCHEDULE_REMINDER_TOPIC_ID
from core.keyboards import build_main_menu_keyboard
from core.module_control import is_module_enabled
from modules.payroll.google_sheets import find_employee_for_telegram_user, get_employees
from modules.schedule.config import MSK_TZ, date_to_str, day_label, parse_date, today_msk
from modules.schedule.google_sheets import get_schedule_matrix
from modules.tasks.config import (
    ASSIGNEE_MODE_NONE,
    ASSIGNEE_MODE_SPECIFIC,
    ASSIGNEE_MODE_WORKING_TODAY,
    NO_DEADLINE_TEXT,
    TASK_DEADLINES,
    TASK_SOURCE_MANUAL,
    TASK_SOURCE_TEMPLATE,
    TASK_STATUS_ACTIVE,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_TYPE_GENERAL,
    TASK_TYPE_WAREHOUSE,
    WEEKDAY_NAMES,
    is_tasks_manager,
)
from modules.tasks.formatting import (
    filter_active_export_tasks,
    format_all_tasks_for_private_view,
    format_daily_staff_message,
    format_general_tasks_message,
    format_regular_tasks_view,
    format_warehouse_tasks_message,
    task_deadline_sort_key,
)
from modules.tasks.storage import (
    can_user_complete_task,
    create_task,
    create_task_template_series,
    assign_working_employees_to_unassigned_template_tasks,
    delete_task_template,
    get_task_by_id,
    get_task_export,
    get_task_template_by_id,
    get_task_templates,
    get_tasks_by_date,
    get_warehouse_managers,
    get_working_employees_for_date,
    mark_task_done,
    materialize_next_week_templates,
    materialize_templates_for_date,
    replace_task_template_series_weekdays,
    set_task_assignees,
    set_task_template_assignees,
    update_task_fields,
    update_task_template_fields,
    upsert_task_export,
)


(
    TASK_ADD_TYPE,
    TASK_ADD_DATE,
    TASK_ADD_DESCRIPTION,
    TASK_ADD_ASSIGNEES,
    TASK_ADD_DEADLINE,
    TASK_VIEW_DATE,
    TASK_EXPORT_DATE,
    TASK_EDIT_DATE,
    TASK_EDIT_SELECT,
    TASK_EDIT_FIELD,
    TASK_EDIT_DESCRIPTION,
    TASK_EDIT_ASSIGNEES,
    TASK_EDIT_DEADLINE,
    TASK_DELETE_DATE,
    TASK_DELETE_SELECT,
    TASK_DELETE_CONFIRM,
    REG_ADD_WEEKDAY,
    REG_ADD_TYPE,
    REG_ADD_DESCRIPTION,
    REG_ADD_ASSIGNEE_MODE,
    REG_ADD_ASSIGNEES,
    REG_ADD_DEADLINE,
    REG_EDIT_DAY,
    REG_EDIT_SELECT,
    REG_EDIT_FIELD,
    REG_EDIT_DESCRIPTION,
    REG_EDIT_WEEKDAY,
    REG_EDIT_TYPE,
    REG_EDIT_ASSIGNEE_MODE,
    REG_EDIT_ASSIGNEES,
    REG_EDIT_DEADLINE,
    REG_DELETE_DAY,
    REG_DELETE_SELECT,
    REG_DELETE_CONFIRM,
    REG_MANAGE_ACTION,
) = range(1200, 1235)


WEEKDAY_SHORT_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
PERSONAL_GENERAL_TASK_USERNAMES = ("fadexdf",)


def normalize_telegram_username(value):
    return str(value or "").strip().lstrip("@").casefold()


def personal_general_task_recipients():
    target_usernames = {normalize_telegram_username(value) for value in PERSONAL_GENERAL_TASK_USERNAMES}
    return [
        employee
        for employee in get_employees(include_inactive=False)
        if normalize_telegram_username(employee.get("telegram_username")) in target_usernames
        and str(employee.get("telegram_user_id", "")).strip()
    ]


def task_is_assigned_to_employee(task, employee):
    employee_id = str(employee.get("employee_id", "")).strip()
    assignee_ids = {
        value.strip()
        for value in str(task.get("Исполнители ID", "")).split(",")
        if value.strip()
    }
    if employee_id and employee_id in assignee_ids:
        return True

    username = normalize_telegram_username(employee.get("telegram_username"))
    assignee_usernames = {
        normalize_telegram_username(value)
        for value in str(task.get("Исполнители", "")).split(",")
        if str(value).strip().startswith("@")
    }
    return bool(username and username in assignee_usernames)


def personal_general_tasks(tasks, employee):
    return [
        task
        for task in tasks
        if str(task.get("Тип задачи", "")).strip() == TASK_TYPE_GENERAL
        and task_is_assigned_to_employee(task, employee)
    ]


def current_employee(update: Update):
    try:
        return find_employee_for_telegram_user(update.effective_user)
    except Exception:
        logging.exception("Не удалось определить сотрудника для задач")
        return None


def ensure_tasks_manager(update: Update):
    return is_tasks_manager(current_employee(update))


def tasks_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Задачи на день", callback_data="task:view")],
        [InlineKeyboardButton("➕ Добавить разовую задачу", callback_data="task:add")],
        [InlineKeyboardButton("✏️ Изменить задачу на день", callback_data="task:edit")],
        [InlineKeyboardButton("🚫 Отменить задачу на день", callback_data="task:delete")],
        [InlineKeyboardButton("📋 Шаблоны регулярных задач", callback_data="task:regular")],
        [InlineKeyboardButton("📤 Выгрузить задачи", callback_data="task:export")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ])


def regular_tasks_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить шаблон", callback_data="reg:add")],
        [InlineKeyboardButton("⚙️ Изменить или удалить", callback_data="reg:manage")],
        [InlineKeyboardButton("👀 Просмотр шаблонов", callback_data="reg:view")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:tasks")],
    ])


def task_type_keyboard(cancel_callback="task:cancel", back_target=None):
    rows = [
        [InlineKeyboardButton("📦 Складская", callback_data="tasktype:warehouse")],
        [InlineKeyboardButton("🧩 Нескладская", callback_data="tasktype:general")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data=cancel_callback)])
    return InlineKeyboardMarkup(rows)


def date_keyboard(prefix, days=14, back_target=None):
    rows = []
    current = today_msk()
    for offset in range(days):
        day = current + timedelta(days=offset)
        rows.append([InlineKeyboardButton(day_label(day), callback_data=f"{prefix}:{date_to_str(day)}")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def weekday_keyboard(prefix, back_target=None):
    rows = []
    for index, name in enumerate(WEEKDAY_NAMES):
        rows.append([InlineKeyboardButton(name, callback_data=f"{prefix}:{index}")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def weekday_multiselect_keyboard(prefix, selected_weekdays, back_target=None):
    selected = {int(value) for value in selected_weekdays or []}
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if index in selected else '⬜️'} {name}",
            callback_data=f"{prefix}:toggle:{index}",
        )]
        for index, name in enumerate(WEEKDAY_NAMES)
    ]
    rows.append([InlineKeyboardButton("✅ Сохранить дни", callback_data=f"{prefix}:done")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def deadline_keyboard(back_target=None):
    rows = []
    for index in range(0, len(TASK_DEADLINES), 3):
        rows.append([InlineKeyboardButton(value, callback_data=f"taskdeadline:{value}") for value in TASK_DEADLINES[index:index + 3]])
    rows.append([InlineKeyboardButton(NO_DEADLINE_TEXT, callback_data="taskdeadline:none")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def assignee_mode_keyboard(back_target=None):
    rows = [
        [InlineKeyboardButton("Все, кто работает в этот день", callback_data="regassigneemode:working_today")],
        [InlineKeyboardButton("Конкретные сотрудники", callback_data="regassigneemode:specific")],
        [InlineKeyboardButton("Без исполнителей", callback_data="regassigneemode:none")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def employee_label(employee, selected_ids):
    mark = "✅" if employee["employee_id"] in selected_ids else "⬜️"
    username = str(employee.get("telegram_username", "")).strip().lstrip("@")
    return f"{mark} {employee['full_name']}" + (f" @{username}" if username else "")


def assignees_keyboard(working, selected_ids, back_target=None):
    selected_ids = set(selected_ids or [])
    rows = [
        [InlineKeyboardButton(employee_label(employee, selected_ids), callback_data=f"taskassignee:{employee['employee_id']}")]
        for employee in working
    ]
    rows.append([InlineKeyboardButton("✅ Завершить выбор", callback_data="taskassignee:done")])
    rows.append([InlineKeyboardButton("👤 Без исполнителей", callback_data="taskassignee:none")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def regular_assignees_keyboard(selected_ids, back_target=None):
    selected_ids = set(selected_ids or [])
    employees = get_employees(include_inactive=False)
    rows = [
        [InlineKeyboardButton(employee_label(employee, selected_ids), callback_data=f"regassignee:{employee['employee_id']}")]
        for employee in employees
    ]
    rows.append([InlineKeyboardButton("✅ Завершить выбор", callback_data="regassignee:done")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def task_edit_sort_key(task):
    task_type = str(task.get("Тип задачи", "")).strip()
    type_order = {
        TASK_TYPE_WAREHOUSE: 0,
        TASK_TYPE_GENERAL: 1,
    }
    return (type_order.get(task_type, 2), *task_deadline_sort_key(task))


def task_select_keyboard(tasks, prefix, back_target=None, *, edit_mode=False):
    rows = []
    if edit_mode:
        tasks = sorted(tasks, key=task_edit_sort_key)
    for task in tasks:
        task_id = str(task.get("task_id", "")).strip()
        status = str(task.get("Статус", "")).strip()
        icon = "✅" if status == TASK_STATUS_DONE else "🚫" if status == TASK_STATUS_CANCELLED else "⬜"
        description = str(task.get("Описание", "")).strip()
        suffix = ""
        if edit_mode:
            deadline = str(task.get("Дедлайн", "")).strip()
            suffix = f" · {deadline or 'без дедлайна'}"
        max_description = max(12, 45 - len(icon) - 1 - len(suffix))
        if len(description) > max_description:
            description = description[:max_description - 3] + "..."
        rows.append([InlineKeyboardButton(f"{icon} {description}{suffix}", callback_data=f"{prefix}:{task_id}")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def regular_task_select_keyboard(templates, prefix, back_target=None):
    rows = []
    for template in templates:
        template_id = str(template.get("template_id", "")).strip()
        description = str(template.get("Описание", "")).strip()
        weekdays = template.get("weekdays") or [template.get("weekday")]
        day_text = ", ".join(
            WEEKDAY_SHORT_NAMES[int(day)]
            for day in weekdays
            if str(day).isdigit() and 0 <= int(day) < len(WEEKDAY_SHORT_NAMES)
        )
        max_description = 34 if day_text else 45
        if len(description) > max_description:
            description = description[:max_description - 3] + "..."
        label = f"{description} · {day_text}" if day_text else description
        rows.append([InlineKeyboardButton(label, callback_data=f"{prefix}:{template_id}")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def regular_templates_for_weekday(weekday):
    weekday = int(weekday)
    return [
        template for template in get_task_templates(active_only=True)
        if int(template.get("weekday", -1)) == weekday
    ]


def regular_template_series_list():
    grouped = {}
    for template in get_task_templates(active_only=True):
        series_id = str(template.get("series_id") or template.get("template_id") or "").strip()
        if not series_id:
            continue
        group = grouped.setdefault(series_id, {"template": dict(template), "weekdays": set()})
        try:
            group["weekdays"].add(int(template.get("weekday")))
        except (TypeError, ValueError):
            continue

    result = []
    for group in grouped.values():
        template = group["template"]
        template["weekdays"] = sorted(group["weekdays"])
        template["Дни недели"] = ", ".join(
            WEEKDAY_NAMES[weekday] for weekday in template["weekdays"]
        )
        result.append(template)
    return sorted(
        result,
        key=lambda template: (
            min(template["weekdays"], default=7),
            str(template.get("Описание", "")).casefold(),
        ),
    )


def edit_field_keyboard(task):
    task_type = str(task.get("Тип задачи", "")).strip()
    rows = [[InlineKeyboardButton("📝 Описание", callback_data="taskeditfield:description")]]
    if task_type == TASK_TYPE_WAREHOUSE:
        rows.append([InlineKeyboardButton("👥 Исполнители", callback_data="taskeditfield:assignees")])
    rows.extend([
        [InlineKeyboardButton("⏰ Дедлайн", callback_data="taskeditfield:deadline")],
        [InlineKeyboardButton("✅ Статус", callback_data="taskeditfield:status")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="taskback:edit_select")],
        [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")],
    ])
    return InlineKeyboardMarkup(rows)


async def return_to_task_edit(send_message, context, confirmation):
    task_id = context.user_data.get("edit_task_id")
    _, task = get_task_by_id(task_id)
    if not task:
        context.user_data.clear()
        await send_message("Задача не найдена.", reply_markup=tasks_menu_keyboard())
        return ConversationHandler.END

    context.user_data.pop("selected_employee_ids", None)
    await send_message(
        f"{confirmation}\n\nЧто изменить дальше?",
        reply_markup=edit_field_keyboard(task),
    )
    return TASK_EDIT_FIELD


def regular_edit_field_keyboard(template):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Описание", callback_data="regeditfield:description")],
        [InlineKeyboardButton("📅 Дни недели", callback_data="regeditfield:weekday")],
        [InlineKeyboardButton("📦 Тип задачи", callback_data="regeditfield:type")],
        [InlineKeyboardButton("👥 Исполнители", callback_data="regeditfield:assignees")],
        [InlineKeyboardButton("⏰ Дедлайн", callback_data="regeditfield:deadline")],
        [InlineKeyboardButton("⬅️ Назад к шаблону", callback_data="taskback:reg_manage_selected")],
        [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")],
    ])


def regular_template_action_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить", callback_data="regmanageaction:edit")],
        [InlineKeyboardButton("🗑 Удалить", callback_data="regmanageaction:delete")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="taskback:reg_manage_list")],
        [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")],
    ])


def status_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬜ Невыполнено", callback_data="taskstatus:active")],
        [InlineKeyboardButton("✅ Выполнено", callback_data="taskstatus:done")],
        [InlineKeyboardButton("🚫 Отменено", callback_data="taskstatus:cancelled")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="taskback:edit_field")],
        [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")],
    ])


def confirm_keyboard(prefix, item_id, back_target=None):
    rows = [
        [InlineKeyboardButton("✅ Да", callback_data=f"{prefix}:yes:{item_id}")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"taskback:{back_target}")])
    rows.append([InlineKeyboardButton("Отмена", callback_data="task:cancel")])
    return InlineKeyboardMarkup(rows)


def warehouse_tasks_inline_keyboard(tasks):
    rows = []
    for task in filter_active_export_tasks(tasks, TASK_TYPE_WAREHOUSE):
        icon = "✅" if str(task.get("Статус", "")).strip() == TASK_STATUS_DONE else "⬜"
        description = str(task.get("Описание", "")).strip()
        if len(description) > 45:
            description = description[:42] + "..."
        rows.append([InlineKeyboardButton(f"{icon} {description}", callback_data=f"taskdone:{task.get('task_id')}")])
    return InlineKeyboardMarkup(rows) if rows else None


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_tasks_manager(update):
        await query.edit_message_text("⛔️ Раздел задач доступен только руководителям.", reply_markup=build_main_menu_keyboard())
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text("🧩 Задачи:", reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def regular_tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("📋 Шаблоны регулярных задач:", reply_markup=regular_tasks_menu_keyboard())
    return ConversationHandler.END


async def irregular_tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🧩 Задачи:", reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def tasks_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    section = context.user_data.get("task_section")
    context.user_data.clear()
    reply_markup = regular_tasks_menu_keyboard() if section == "regular" else tasks_menu_keyboard()
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Действие отменено.", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Действие отменено.", reply_markup=reply_markup)
    return ConversationHandler.END


async def irregular_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["task_section"] = "daily"
    await query.edit_message_text("Выберите тип разовой задачи:", reply_markup=task_type_keyboard())
    return TASK_ADD_TYPE


async def task_add_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["task_type"] = query.data.replace("tasktype:", "")
    await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskdate", back_target="add_type"))
    return TASK_ADD_DATE


async def task_add_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["task_date"] = query.data.replace("taskdate:", "")
    await query.edit_message_text(f"Дата: {context.user_data['task_date']}\n\nВведите описание задачи:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:add_date")], [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")]]))
    return TASK_ADD_DESCRIPTION


async def task_add_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не должно быть пустым. Введите описание задачи:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:add_date")]]))
        return TASK_ADD_DESCRIPTION
    context.user_data["task_description"] = description

    if context.user_data.get("task_type") == TASK_TYPE_GENERAL:
        await update.message.reply_text("Выберите дедлайн:", reply_markup=deadline_keyboard("add_description"))
        return TASK_ADD_DEADLINE

    day = parse_date(context.user_data["task_date"])
    working = get_working_employees_for_date(day)
    context.user_data["selected_employee_ids"] = []
    if not working:
        await update.message.reply_text(
            "На эту дату пока нет сотрудников в расписании. Задачу можно создать без исполнителей.\n\nВыберите дедлайн:",
            reply_markup=deadline_keyboard("add_description"),
        )
        return TASK_ADD_DEADLINE

    await update.message.reply_text("Выберите исполнителей из тех, кто работает в выбранный день:", reply_markup=assignees_keyboard(working, [], "add_description"))
    return TASK_ADD_ASSIGNEES


async def task_assignee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("taskassignee:", "")
    day = parse_date(context.user_data["task_date"] if "task_date" in context.user_data else context.user_data["edit_task_date"])
    working = get_working_employees_for_date(day)

    if value == "none":
        context.user_data["selected_employee_ids"] = []
        if "edit_task_id" in context.user_data:
            set_task_assignees(context.user_data["edit_task_id"], [])
            await refresh_existing_exports_for_date(context, day)
            return await return_to_task_edit(
                query.edit_message_text,
                context,
                "Исполнители очищены ✅",
            )
        await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("add_assignees"))
        return TASK_ADD_DEADLINE

    if value == "done":
        selected_ids = set(context.user_data.get("selected_employee_ids", []))
        selected = [employee for employee in working if employee["employee_id"] in selected_ids]
        if "edit_task_id" in context.user_data:
            set_task_assignees(context.user_data["edit_task_id"], selected)
            await refresh_existing_exports_for_date(context, day)
            return await return_to_task_edit(
                query.edit_message_text,
                context,
                "Исполнители обновлены ✅",
            )
        await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("add_assignees"))
        return TASK_ADD_DEADLINE

    selected = set(context.user_data.get("selected_employee_ids", []))
    selected.remove(value) if value in selected else selected.add(value)
    context.user_data["selected_employee_ids"] = list(selected)
    await query.edit_message_text("Выберите исполнителей:", reply_markup=assignees_keyboard(working, selected, "edit_field" if "edit_task_id" in context.user_data else "add_description"))
    return TASK_EDIT_ASSIGNEES if "edit_task_id" in context.user_data else TASK_ADD_ASSIGNEES


async def task_deadline_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("taskdeadline:", "")
    deadline = "" if value == "none" else value

    if "edit_task_id" in context.user_data:
        update_task_fields(context.user_data["edit_task_id"], **{"Дедлайн": deadline})
        _, task = get_task_by_id(context.user_data["edit_task_id"])
        await refresh_existing_exports_for_date(context, parse_date(task["Дата"]))
        return await return_to_task_edit(
            query.edit_message_text,
            context,
            "Дедлайн обновлен ✅",
        )

    if "edit_template_id" in context.user_data:
        update_task_template_fields(context.user_data["edit_template_id"], **{"Дедлайн": deadline})
        context.user_data.clear()
        await query.edit_message_text("Дедлайн шаблона обновлен ✅", reply_markup=regular_tasks_menu_keyboard())
        return ConversationHandler.END

    if context.user_data.get("task_section") == "regular":
        context.user_data["regular_deadline"] = deadline
        return await finish_regular_task_creation(update, context)

    day = parse_date(context.user_data["task_date"])
    selected_ids = set(context.user_data.get("selected_employee_ids", []))
    assignees = []
    if context.user_data.get("task_type") == TASK_TYPE_WAREHOUSE and selected_ids:
        assignees = [employee for employee in get_working_employees_for_date(day) if employee["employee_id"] in selected_ids]

    employee = current_employee(update)
    create_task(
        day=day,
        task_type=context.user_data["task_type"],
        description=context.user_data["task_description"],
        assignee_employees=assignees,
        deadline=deadline,
        source=TASK_SOURCE_MANUAL,
        created_by=employee["full_name"] if employee else "manager",
    )
    await refresh_existing_exports_for_date(context, day)
    context.user_data.clear()
    await query.edit_message_text("Разовая задача создана ✅", reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def irregular_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["view_mode"] = "all"
    await query.edit_message_text("Выберите дату для просмотра задач:", reply_markup=date_keyboard("taskviewdate"))
    return TASK_VIEW_DATE


async def task_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["view_mode"] = "all"
    await query.edit_message_text("Выберите дату для просмотра задач:", reply_markup=date_keyboard("taskviewdate"))
    return TASK_VIEW_DATE


async def task_view_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = parse_date(query.data.replace("taskviewdate:", ""))
    materialize_templates_for_date(day)
    tasks = get_tasks_by_date(day)
    reply_markup = tasks_menu_keyboard()
    await query.edit_message_text(format_all_tasks_for_private_view(day, tasks), reply_markup=reply_markup)
    return ConversationHandler.END


async def task_export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Выберите дату для выгрузки задач:", reply_markup=date_keyboard("taskexportdate"))
    return TASK_EXPORT_DATE


async def task_export_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = parse_date(query.data.replace("taskexportdate:", ""))
    await query.edit_message_text(await export_tasks_for_date(context, day), reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def irregular_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["task_section"] = "daily"
    await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskeditdate"))
    return TASK_EDIT_DATE


async def task_edit_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = parse_date(query.data.replace("taskeditdate:", ""))
    context.user_data["edit_task_date"] = date_to_str(day)
    materialize_templates_for_date(day)
    tasks = get_tasks_by_date(day, include_cancelled=False)
    if not tasks:
        await query.edit_message_text("На эту дату задач пока нет.", reply_markup=tasks_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите задачу:",
        reply_markup=task_select_keyboard(tasks, "taskedit", "edit_date", edit_mode=True),
    )
    return TASK_EDIT_SELECT


async def task_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = query.data.replace("taskedit:", "")
    _, task = get_task_by_id(task_id)
    if not task:
        await query.edit_message_text("Задача не найдена.", reply_markup=tasks_menu_keyboard())
        return ConversationHandler.END
    context.user_data["edit_task_id"] = task_id
    await query.edit_message_text("Что изменить?", reply_markup=edit_field_keyboard(task))
    return TASK_EDIT_FIELD


async def task_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("taskeditfield:", "")
    _, task = get_task_by_id(context.user_data["edit_task_id"])
    if field == "description":
        await query.edit_message_text("Введите новое описание:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:edit_field")]]))
        return TASK_EDIT_DESCRIPTION
    if field == "deadline":
        await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("edit_field"))
        return TASK_EDIT_DEADLINE
    if field == "assignees":
        day = parse_date(task["Дата"])
        context.user_data["selected_employee_ids"] = [x.strip() for x in str(task.get("Исполнители ID", "")).split(",") if x.strip()]
        await query.edit_message_text("Выберите исполнителей:", reply_markup=assignees_keyboard(get_working_employees_for_date(day), context.user_data["selected_employee_ids"], "edit_field"))
        return TASK_EDIT_ASSIGNEES
    if field == "status":
        await query.edit_message_text("Выберите статус:", reply_markup=status_keyboard())
        return TASK_EDIT_FIELD
    return ConversationHandler.END


async def task_edit_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data["edit_task_id"]
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не должно быть пустым. Введите новое описание:")
        return TASK_EDIT_DESCRIPTION
    update_task_fields(task_id, **{"Описание": description})
    _, task = get_task_by_id(task_id)
    await refresh_existing_exports_for_date(context, parse_date(task["Дата"]))
    return await return_to_task_edit(
        update.message.reply_text,
        context,
        "Описание обновлено ✅",
    )


async def task_status_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    status = {
        "active": TASK_STATUS_ACTIVE,
        "done": TASK_STATUS_DONE,
        "cancelled": TASK_STATUS_CANCELLED,
    }.get(query.data.replace("taskstatus:", ""), TASK_STATUS_ACTIVE)
    update_task_fields(context.user_data["edit_task_id"], **{"Статус": status})
    _, task = get_task_by_id(context.user_data["edit_task_id"])
    await refresh_existing_exports_for_date(context, parse_date(task["Дата"]))
    return await return_to_task_edit(
        query.edit_message_text,
        context,
        "Статус обновлен ✅",
    )


async def irregular_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["task_section"] = "daily"
    await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskdeldate"))
    return TASK_DELETE_DATE


async def task_delete_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    day = parse_date(query.data.replace("taskdeldate:", ""))
    context.user_data["delete_task_date"] = date_to_str(day)
    materialize_templates_for_date(day)
    tasks = get_tasks_by_date(day, include_cancelled=False)
    if not tasks:
        await query.edit_message_text("На эту дату задач пока нет.", reply_markup=tasks_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text("Выберите задачу для отмены на эту дату:", reply_markup=task_select_keyboard(tasks, "taskdel", "delete_date"))
    return TASK_DELETE_SELECT


async def task_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = query.data.replace("taskdel:", "")
    _, task = get_task_by_id(task_id)
    if not task:
        await query.edit_message_text("Задача не найдена.", reply_markup=tasks_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        f"Отменить задачу на эту дату?\n\n{task.get('Описание', '')}",
        reply_markup=confirm_keyboard("taskdelconfirm", task_id, "delete_select"),
    )
    return TASK_DELETE_CONFIRM


async def task_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, task_id = query.data.split(":", 2)
    _, task = get_task_by_id(task_id)
    if task:
        update_task_fields(task_id, **{"Статус": TASK_STATUS_CANCELLED})
        await refresh_existing_exports_for_date(context, parse_date(task["Дата"]))
    context.user_data.clear()
    await query.edit_message_text("Задача отменена на выбранную дату ✅", reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def regular_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    text = format_regular_tasks_view(get_task_templates(active_only=True))
    if len(text) <= 4000:
        await query.edit_message_text(
            text,
            reply_markup=regular_tasks_menu_keyboard(),
        )
    else:
        await query.message.reply_document(
            document=InputFile(
                BytesIO(text.encode("utf-8")),
                filename="Шаблоны_регулярных_задач.txt",
            ),
            caption="📋 Шаблоны регулярных задач по дням недели",
        )
        await query.edit_message_text(
            "Список шаблонов слишком большой для одного сообщения, поэтому отправлен файлом `.txt`.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
    return ConversationHandler.END


async def regular_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["task_section"] = "regular"
    context.user_data["regular_weekdays"] = []
    await query.edit_message_text(
        "Выберите все дни, когда должна повторяться задача:",
        reply_markup=weekday_multiselect_keyboard("regweekday", []),
    )
    return REG_ADD_WEEKDAY


async def regular_add_weekday_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    value = query.data.replace("regweekday:", "", 1)
    selected = set(context.user_data.get("regular_weekdays", []))
    if value.startswith("toggle:"):
        await query.answer()
        weekday = int(value.replace("toggle:", "", 1))
        selected.remove(weekday) if weekday in selected else selected.add(weekday)
        context.user_data["regular_weekdays"] = sorted(selected)
        await query.edit_message_text(
            "Выберите все дни, когда должна повторяться задача:",
            reply_markup=weekday_multiselect_keyboard("regweekday", selected),
        )
        return REG_ADD_WEEKDAY
    if value != "done":
        await query.answer()
        return REG_ADD_WEEKDAY
    if not selected:
        await query.answer("Выберите хотя бы один день.", show_alert=True)
        return REG_ADD_WEEKDAY
    await query.answer()
    await query.edit_message_text("Выберите тип шаблона:", reply_markup=task_type_keyboard(back_target="reg_add_weekday"))
    return REG_ADD_TYPE


async def regular_add_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["regular_task_type"] = query.data.replace("tasktype:", "")
    await query.edit_message_text("Введите описание шаблона:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:reg_add_type")]]))
    return REG_ADD_DESCRIPTION


async def regular_add_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не должно быть пустым. Введите описание:")
        return REG_ADD_DESCRIPTION
    context.user_data["regular_description"] = description
    if context.user_data.get("regular_task_type") == TASK_TYPE_WAREHOUSE:
        await update.message.reply_text("Выберите режим исполнителей:", reply_markup=assignee_mode_keyboard("reg_add_description"))
        return REG_ADD_ASSIGNEE_MODE
    context.user_data["regular_assignee_mode"] = ASSIGNEE_MODE_NONE
    context.user_data["selected_employee_ids"] = []
    await update.message.reply_text("Выберите дедлайн:", reply_markup=deadline_keyboard("reg_add_description"))
    return REG_ADD_DEADLINE


async def regular_assignee_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = query.data.replace("regassigneemode:", "")
    if mode == "none":
        mode = ASSIGNEE_MODE_NONE
    context.user_data["regular_assignee_mode"] = mode
    context.user_data["selected_employee_ids"] = []
    if mode == ASSIGNEE_MODE_SPECIFIC:
        await query.edit_message_text("Выберите сотрудников:", reply_markup=regular_assignees_keyboard([], "reg_edit_field" if "edit_template_id" in context.user_data else "reg_add_mode"))
        return REG_EDIT_ASSIGNEES if "edit_template_id" in context.user_data else REG_ADD_ASSIGNEES
    if "edit_template_id" in context.user_data:
        set_task_template_assignees(context.user_data["edit_template_id"], mode, [])
        context.user_data.clear()
        await query.edit_message_text("Исполнители шаблона обновлены ✅", reply_markup=regular_tasks_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("reg_add_mode"))
    return REG_ADD_DEADLINE


async def regular_assignee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("regassignee:", "")
    selected = set(context.user_data.get("selected_employee_ids", []))

    if value == "done":
        employees = [employee for employee in get_employees(include_inactive=False) if employee["employee_id"] in selected]
        if "edit_template_id" in context.user_data:
            set_task_template_assignees(context.user_data["edit_template_id"], ASSIGNEE_MODE_SPECIFIC, employees)
            context.user_data.clear()
            await query.edit_message_text("Исполнители шаблона обновлены ✅", reply_markup=regular_tasks_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("reg_add_assignees"))
        return REG_ADD_DEADLINE

    selected.remove(value) if value in selected else selected.add(value)
    context.user_data["selected_employee_ids"] = list(selected)
    await query.edit_message_text("Выберите сотрудников:", reply_markup=regular_assignees_keyboard(selected, "reg_edit_field" if "edit_template_id" in context.user_data else "reg_add_mode"))
    return REG_EDIT_ASSIGNEES if "edit_template_id" in context.user_data else REG_ADD_ASSIGNEES


async def finish_regular_task_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_ids = set(context.user_data.get("selected_employee_ids", []))
    employees = [employee for employee in get_employees(include_inactive=False) if employee["employee_id"] in selected_ids]
    result = create_task_template_series(
        weekdays=context.user_data["regular_weekdays"],
        task_type=context.user_data["regular_task_type"],
        description=context.user_data["regular_description"],
        assignee_mode=context.user_data.get("regular_assignee_mode", ASSIGNEE_MODE_NONE),
        assignee_employees=employees,
        deadline=context.user_data.get("regular_deadline", ""),
    )
    day_names = ", ".join(WEEKDAY_NAMES[day] for day in result["weekdays"])
    context.user_data.clear()
    await update.callback_query.edit_message_text(
        f"Шаблон создан ✅\nДни: {day_names}",
        reply_markup=regular_tasks_menu_keyboard(),
    )
    return ConversationHandler.END


async def regular_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["task_section"] = "regular"
    templates = regular_template_series_list()
    if not templates:
        await query.edit_message_text(
            "Шаблонных задач пока нет.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите шаблонную задачу:",
        reply_markup=regular_task_select_keyboard(
            templates, "regmanage", "reg_menu",
        ),
    )
    return REG_EDIT_SELECT


async def regular_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await regular_manage_start(update, context)


async def regular_edit_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.replace("regeditday:", ""))
    context.user_data["regular_weekday_filter"] = weekday
    templates = regular_templates_for_weekday(weekday)
    if not templates:
        await query.edit_message_text(
            f"На {WEEKDAY_NAMES[weekday].lower()} регулярных задач пока нет.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        f"{WEEKDAY_NAMES[weekday]}. Выберите шаблон:",
        reply_markup=regular_task_select_keyboard(templates, "regedit", "reg_edit_day"),
    )
    return REG_EDIT_SELECT


async def regular_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = query.data.replace("regedit:", "")
    template = get_task_template_by_id(template_id)
    if not template:
        await query.edit_message_text("Регулярная задача не найдена.", reply_markup=regular_tasks_menu_keyboard())
        return ConversationHandler.END
    context.user_data["edit_template_id"] = template_id
    await query.edit_message_text("Что изменить?", reply_markup=regular_edit_field_keyboard(template))
    return REG_EDIT_FIELD


async def regular_manage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = query.data.replace("regmanage:", "", 1)
    template = get_task_template_by_id(template_id)
    if not template:
        await query.edit_message_text(
            "Регулярная задача не найдена.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    context.user_data["edit_template_id"] = template_id
    await query.edit_message_text(
        f"{template.get('Описание', '')}\n"
        f"Дни: {template.get('Дни недели') or template.get('День недели', '')}\n\n"
        "Выберите действие:",
        reply_markup=regular_template_action_keyboard(),
    )
    return REG_MANAGE_ACTION


async def regular_manage_action_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("regmanageaction:", "", 1)
    template_id = context.user_data.get("edit_template_id")
    template = get_task_template_by_id(template_id)
    if not template:
        await query.edit_message_text(
            "Регулярная задача не найдена.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    if action == "edit":
        await query.edit_message_text(
            "Что изменить?",
            reply_markup=regular_edit_field_keyboard(template),
        )
        return REG_EDIT_FIELD
    if action == "delete":
        await query.edit_message_text(
            f"Удалить шаблон сразу из всех дней?\n\n"
            f"{template.get('Описание', '')}\n"
            f"Дни: {template.get('Дни недели') or template.get('День недели', '')}",
            reply_markup=confirm_keyboard(
                "regdelconfirm", template_id, "reg_manage_selected",
            ),
        )
        return REG_DELETE_CONFIRM
    return REG_MANAGE_ACTION


async def regular_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("regeditfield:", "")
    if field == "description":
        await query.edit_message_text("Введите новое описание шаблона:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:reg_edit_field")]]))
        return REG_EDIT_DESCRIPTION
    if field == "weekday":
        template = get_task_template_by_id(context.user_data["edit_template_id"])
        selected = list(template.get("weekdays") or [template.get("weekday")])
        context.user_data["edit_template_weekdays"] = selected
        await query.edit_message_text(
            "Отметьте все дни, когда должна действовать задача:",
            reply_markup=weekday_multiselect_keyboard("regeditweekday", selected, "reg_edit_field"),
        )
        return REG_EDIT_WEEKDAY
    if field == "type":
        await query.edit_message_text("Выберите тип задачи:", reply_markup=task_type_keyboard(back_target="reg_edit_field"))
        return REG_EDIT_TYPE
    if field == "assignees":
        await query.edit_message_text("Выберите режим исполнителей:", reply_markup=assignee_mode_keyboard("reg_edit_field"))
        return REG_EDIT_ASSIGNEE_MODE
    if field == "deadline":
        await query.edit_message_text("Выберите дедлайн:", reply_markup=deadline_keyboard("reg_edit_field"))
        return REG_EDIT_DEADLINE
    return ConversationHandler.END


async def regular_edit_description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Описание не должно быть пустым. Введите новое описание:")
        return REG_EDIT_DESCRIPTION
    update_task_template_fields(context.user_data["edit_template_id"], **{"Описание": description})
    context.user_data.clear()
    await update.message.reply_text("Описание шаблона обновлено ✅", reply_markup=regular_tasks_menu_keyboard())
    return ConversationHandler.END


async def regular_edit_weekday_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    value = query.data.replace("regeditweekday:", "", 1)
    selected = set(context.user_data.get("edit_template_weekdays", []))
    if value.startswith("toggle:"):
        await query.answer()
        weekday = int(value.replace("toggle:", "", 1))
        selected.remove(weekday) if weekday in selected else selected.add(weekday)
        context.user_data["edit_template_weekdays"] = sorted(selected)
        await query.edit_message_text(
            "Отметьте все дни, когда должна действовать задача:",
            reply_markup=weekday_multiselect_keyboard(
                "regeditweekday", selected, "reg_edit_field",
            ),
        )
        return REG_EDIT_WEEKDAY
    if value != "done":
        await query.answer()
        return REG_EDIT_WEEKDAY
    if not selected:
        await query.answer("Нельзя убрать все дни. Для этого удалите шаблон.", show_alert=True)
        return REG_EDIT_WEEKDAY
    await query.answer()
    replace_task_template_series_weekdays(context.user_data["edit_template_id"], selected)
    day_names = ", ".join(WEEKDAY_NAMES[day] for day in sorted(selected))
    context.user_data.clear()
    await query.edit_message_text(
        f"Дни шаблона обновлены ✅\n{day_names}",
        reply_markup=regular_tasks_menu_keyboard(),
    )
    return ConversationHandler.END


async def regular_edit_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_type = query.data.replace("tasktype:", "")
    fields = {"Тип задачи": task_type}
    if task_type == TASK_TYPE_GENERAL:
        fields.update({"Тип исполнителей": ASSIGNEE_MODE_NONE, "Исполнители ID": "", "Исполнители": ""})
    update_task_template_fields(context.user_data["edit_template_id"], **fields)
    context.user_data.clear()
    await query.edit_message_text("Тип шаблона обновлен ✅", reply_markup=regular_tasks_menu_keyboard())
    return ConversationHandler.END


async def regular_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await regular_manage_start(update, context)


async def regular_delete_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.replace("regdelday:", ""))
    context.user_data["regular_weekday_filter"] = weekday
    templates = regular_templates_for_weekday(weekday)
    if not templates:
        await query.edit_message_text(
            f"На {WEEKDAY_NAMES[weekday].lower()} регулярных задач пока нет.",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        f"{WEEKDAY_NAMES[weekday]}. Выберите шаблон для удаления:",
        reply_markup=regular_task_select_keyboard(templates, "regdel", "reg_delete_day"),
    )
    return REG_DELETE_SELECT


async def regular_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    template_id = query.data.replace("regdel:", "")
    template = get_task_template_by_id(template_id)
    if not template:
        await query.edit_message_text("Регулярная задача не найдена.", reply_markup=regular_tasks_menu_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        f"Удалить шаблон сразу из всех дней?\n\n"
        f"{template.get('Описание', '')}\n"
        f"Дни: {template.get('Дни недели') or template.get('День недели', '')}",
        reply_markup=confirm_keyboard("regdelconfirm", template_id, "reg_delete_select"),
    )
    return REG_DELETE_CONFIRM


async def regular_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, template_id = query.data.split(":", 2)
    delete_task_template(template_id)
    context.user_data.clear()
    await query.edit_message_text(
        "Регулярная задача удалена из всех дней недели ✅",
        reply_markup=regular_tasks_menu_keyboard(),
    )
    return ConversationHandler.END


async def tasks_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("taskback:", "", 1)

    if target == "add_type":
        await query.edit_message_text("Выберите тип разовой задачи:", reply_markup=task_type_keyboard())
        return TASK_ADD_TYPE
    if target == "add_date":
        await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskdate", back_target="add_type"))
        return TASK_ADD_DATE
    if target == "add_description":
        await query.edit_message_text(f"Дата: {context.user_data.get('task_date', '')}\n\nВведите описание задачи:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:add_date")], [InlineKeyboardButton("❌ Отмена", callback_data="task:cancel")]]))
        return TASK_ADD_DESCRIPTION
    if target == "add_assignees":
        day = parse_date(context.user_data["task_date"])
        selected = context.user_data.get("selected_employee_ids", [])
        await query.edit_message_text("Выберите исполнителей:", reply_markup=assignees_keyboard(get_working_employees_for_date(day), selected, "add_description"))
        return TASK_ADD_ASSIGNEES

    if target == "edit_date":
        context.user_data.pop("edit_task_id", None)
        await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskeditdate"))
        return TASK_EDIT_DATE
    if target == "edit_select":
        context.user_data.pop("edit_task_id", None)
        day = parse_date(context.user_data["edit_task_date"])
        tasks = get_tasks_by_date(day, include_cancelled=False)
        await query.edit_message_text(
            "Выберите задачу:",
            reply_markup=task_select_keyboard(tasks, "taskedit", "edit_date", edit_mode=True),
        )
        return TASK_EDIT_SELECT
    if target == "edit_field":
        context.user_data.pop("selected_employee_ids", None)
        _, task = get_task_by_id(context.user_data.get("edit_task_id"))
        if not task:
            await query.edit_message_text("Задача не найдена.", reply_markup=tasks_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("Что изменить?", reply_markup=edit_field_keyboard(task))
        return TASK_EDIT_FIELD

    if target == "delete_date":
        await query.edit_message_text("Выберите дату:", reply_markup=date_keyboard("taskdeldate"))
        return TASK_DELETE_DATE
    if target == "delete_select":
        day = parse_date(context.user_data["delete_task_date"])
        tasks = get_tasks_by_date(day, include_cancelled=False)
        await query.edit_message_text("Выберите задачу для отмены:", reply_markup=task_select_keyboard(tasks, "taskdel", "delete_date"))
        return TASK_DELETE_SELECT

    if target == "reg_add_weekday":
        await query.edit_message_text(
            "Выберите все дни, когда должна повторяться задача:",
            reply_markup=weekday_multiselect_keyboard(
                "regweekday", context.user_data.get("regular_weekdays", []),
            ),
        )
        return REG_ADD_WEEKDAY
    if target == "reg_add_type":
        await query.edit_message_text("Выберите тип шаблона:", reply_markup=task_type_keyboard(back_target="reg_add_weekday"))
        return REG_ADD_TYPE
    if target == "reg_add_description":
        await query.edit_message_text("Введите описание шаблона:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="taskback:reg_add_type")]]))
        return REG_ADD_DESCRIPTION
    if target == "reg_add_mode":
        await query.edit_message_text("Выберите режим исполнителей:", reply_markup=assignee_mode_keyboard("reg_add_description"))
        return REG_ADD_ASSIGNEE_MODE
    if target == "reg_add_assignees":
        await query.edit_message_text("Выберите сотрудников:", reply_markup=regular_assignees_keyboard(context.user_data.get("selected_employee_ids", []), "reg_add_mode"))
        return REG_ADD_ASSIGNEES

    if target == "reg_menu":
        context.user_data.clear()
        await query.edit_message_text(
            "📋 Шаблоны регулярных задач:",
            reply_markup=regular_tasks_menu_keyboard(),
        )
        return ConversationHandler.END
    if target in {
        "reg_manage_list", "reg_edit_day", "reg_delete_day",
        "reg_edit_select", "reg_delete_select",
    }:
        context.user_data.pop("edit_template_id", None)
        templates = regular_template_series_list()
        if not templates:
            await query.edit_message_text(
                "Шаблонных задач пока нет.",
                reply_markup=regular_tasks_menu_keyboard(),
            )
            return ConversationHandler.END
        await query.edit_message_text(
            "Выберите шаблонную задачу:",
            reply_markup=regular_task_select_keyboard(
                templates, "regmanage", "reg_menu",
            ),
        )
        return REG_EDIT_SELECT
    if target == "reg_manage_selected":
        template = get_task_template_by_id(context.user_data.get("edit_template_id"))
        if not template:
            await query.edit_message_text(
                "Шаблон не найден.", reply_markup=regular_tasks_menu_keyboard(),
            )
            return ConversationHandler.END
        await query.edit_message_text(
            f"{template.get('Описание', '')}\n"
            f"Дни: {template.get('Дни недели') or template.get('День недели', '')}\n\n"
            "Выберите действие:",
            reply_markup=regular_template_action_keyboard(),
        )
        return REG_MANAGE_ACTION
    if target == "reg_edit_field":
        context.user_data.pop("edit_template_weekdays", None)
        template = get_task_template_by_id(context.user_data.get("edit_template_id"))
        if not template:
            await query.edit_message_text("Шаблон не найден.", reply_markup=regular_tasks_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("Что изменить?", reply_markup=regular_edit_field_keyboard(template))
        return REG_EDIT_FIELD

    await query.edit_message_text("Действие отменено.", reply_markup=tasks_menu_keyboard())
    return ConversationHandler.END


async def send_or_edit_task_message(context, day, export_type, chat_id, thread_id, text, reply_markup=None):
    existing = get_task_export(day, export_type)
    if existing and existing.get("chat_id") and existing.get("message_id"):
        try:
            await context.bot.edit_message_text(
                chat_id=int(existing["chat_id"]),
                message_id=int(existing["message_id"]),
                text=text,
                reply_markup=reply_markup,
            )
            return "обновлено"
        except BadRequest as error:
            if "Message is not modified" in str(error):
                return "без изменений"
            logging.exception("Не удалось отредактировать сообщение задач")
        except Exception:
            logging.exception("Не удалось отредактировать сообщение задач")

    kwargs = {"chat_id": int(chat_id), "text": text}
    if thread_id:
        kwargs["message_thread_id"] = int(thread_id)
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    message = await context.bot.send_message(**kwargs)
    upsert_task_export(day, export_type, chat_id, thread_id or "", message.message_id)
    return "отправлено"


async def export_warehouse_tasks_for_date(context, day):
    if not GROUP_CHAT_ID or not SCHEDULE_REMINDER_TOPIC_ID:
        return "не настроены GROUP_CHAT_ID или SCHEDULE_REMINDER_TOPIC_ID"
    materialize_templates_for_date(day)
    assign_working_employees_to_unassigned_template_tasks(day)
    tasks = get_tasks_by_date(day)
    return await send_or_edit_task_message(
        context,
        day,
        "warehouse",
        GROUP_CHAT_ID,
        SCHEDULE_REMINDER_TOPIC_ID,
        format_warehouse_tasks_message(day, tasks),
        warehouse_tasks_inline_keyboard(tasks),
    )


async def export_general_tasks_for_date(context, day):
    materialize_templates_for_date(day)
    tasks = get_tasks_by_date(day)
    managers = get_warehouse_managers()
    statuses = []
    for manager in managers:
        chat_id = str(manager.get("telegram_user_id", "")).strip()
        if chat_id:
            statuses.append(
                await send_or_edit_task_message(
                    context,
                    day,
                    f"general:{chat_id}",
                    chat_id,
                    "",
                    format_general_tasks_message(day, tasks),
                    None,
                )
            )
    manager_status = "; ".join(statuses) if statuses else "руководитель склада не найден"
    delivery_statuses = [f"руководитель: {manager_status}"]

    for employee in personal_general_task_recipients():
        assigned_tasks = personal_general_tasks(tasks, employee)
        if not assigned_tasks:
            continue
        chat_id = str(employee.get("telegram_user_id", "")).strip()
        employee_id = str(employee.get("employee_id", "")).strip()
        username = normalize_telegram_username(employee.get("telegram_username"))
        try:
            status = await send_or_edit_task_message(
                context,
                day,
                f"general_assignee:{employee_id or chat_id}",
                chat_id,
                "",
                format_general_tasks_message(day, assigned_tasks),
                None,
            )
            delivery_statuses.append(f"@{username}: {status}")
        except Exception:
            logging.exception("Не удалось отправить личные нескладские задачи сотруднику @%s", username)
            delivery_statuses.append(f"@{username}: ошибка отправки")

    return "; ".join(delivery_statuses)


async def export_tasks_for_date(context, day):
    warehouse_status = await export_warehouse_tasks_for_date(context, day)
    general_status = await export_general_tasks_for_date(context, day)
    tasks = get_tasks_by_date(day)
    manual_count = sum(1 for task in tasks if str(task.get("Источник", "")).strip() == TASK_SOURCE_MANUAL)
    template_count = sum(1 for task in tasks if str(task.get("Источник", "")).strip() == TASK_SOURCE_TEMPLATE)
    logging.info(
        "Tasks exported for %s: total=%s manual=%s template=%s warehouse_status=%s general_status=%s",
        date_to_str(day),
        len(tasks),
        manual_count,
        template_count,
        warehouse_status,
        general_status,
    )
    return f"Задачи на {date_to_str(day)} выгружены ✅\n\nСкладские в тему: {warehouse_status}\nНескладские в личные сообщения: {general_status}"


async def refresh_existing_exports_for_date(context, day):
    tasks = get_tasks_by_date(day)
    if get_task_export(day, "warehouse") and GROUP_CHAT_ID and SCHEDULE_REMINDER_TOPIC_ID:
        await send_or_edit_task_message(
            context,
            day,
            "warehouse",
            GROUP_CHAT_ID,
            SCHEDULE_REMINDER_TOPIC_ID,
            format_warehouse_tasks_message(day, tasks),
            warehouse_tasks_inline_keyboard(tasks),
        )
    general_was_exported = False
    for manager in get_warehouse_managers():
        chat_id = str(manager.get("telegram_user_id", "")).strip()
        export_type = f"general:{chat_id}"
        if chat_id and get_task_export(day, export_type):
            general_was_exported = True
            await send_or_edit_task_message(context, day, export_type, chat_id, "", format_general_tasks_message(day, tasks), None)

    for employee in personal_general_task_recipients():
        chat_id = str(employee.get("telegram_user_id", "")).strip()
        employee_id = str(employee.get("employee_id", "")).strip()
        export_type = f"general_assignee:{employee_id or chat_id}"
        existing = get_task_export(day, export_type)
        assigned_tasks = personal_general_tasks(tasks, employee)
        if not existing and not (general_was_exported and assigned_tasks):
            continue
        await send_or_edit_task_message(
            context,
            day,
            export_type,
            chat_id,
            "",
            format_general_tasks_message(day, assigned_tasks),
            None,
        )


async def task_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = query.data.replace("taskdone:", "")
    _, task = get_task_by_id(task_id)
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return
    employee = current_employee(update)
    if not can_user_complete_task(update.effective_user, task, employee):
        await query.answer("У вас нет прав отметить эту задачу", show_alert=True)
        return
    done = str(task.get("Статус", "")).strip() != TASK_STATUS_DONE
    mark_task_done(task_id, employee, done=done)
    _, updated = get_task_by_id(task_id)
    await refresh_existing_exports_for_date(context, parse_date(updated["Дата"]))
    await query.answer("Готово")


async def daily_staff_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("schedule"):
        return
    if not GROUP_CHAT_ID or not SCHEDULE_REMINDER_TOPIC_ID:
        return
    day = today_msk()
    week_start = day - timedelta(days=day.weekday())
    employees, dates, schedule, duty_by_date = get_schedule_matrix(week_start)
    await context.bot.send_message(
        chat_id=int(GROUP_CHAT_ID),
        message_thread_id=int(SCHEDULE_REMINDER_TOPIC_ID),
        text=format_daily_staff_message(day, employees, schedule, duty_by_date),
    )


async def daily_tasks_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("tasks"):
        return
    await export_tasks_for_date(context, today_msk())


async def auto_assign_template_tasks_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("tasks"):
        return
    assign_working_employees_to_unassigned_template_tasks(today_msk())


async def weekly_template_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("tasks"):
        return
    logging.info("Создано задач из регулярных задач: %s", materialize_next_week_templates(today_msk()))


def setup_tasks_jobs(app):
    if not app.job_queue:
        logging.warning("JobQueue не включен. Установите python-telegram-bot[job-queue].")
        return
    app.job_queue.run_daily(daily_staff_job, time=time(hour=9, minute=30, tzinfo=MSK_TZ), name="daily_staff_message")
    app.job_queue.run_daily(
        auto_assign_template_tasks_job,
        time=time(hour=9, minute=30, tzinfo=MSK_TZ),
        name="template_task_auto_assignment",
    )
    app.job_queue.run_daily(daily_tasks_job, time=time(hour=9, minute=35, tzinfo=MSK_TZ), name="daily_tasks_export")
    app.job_queue.run_daily(
        weekly_template_job,
        time=time(hour=23, minute=0, tzinfo=MSK_TZ),
        days=(6,),
        name="weekly_task_template_materialization",
    )


def get_tasks_handlers():
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(irregular_add_start, pattern=r"^task:add$"),
            CallbackQueryHandler(irregular_edit_start, pattern=r"^task:edit$"),
            CallbackQueryHandler(irregular_delete_start, pattern=r"^task:delete$"),
            CallbackQueryHandler(irregular_add_start, pattern=r"^irreg:add$"),
            CallbackQueryHandler(irregular_view_start, pattern=r"^irreg:view$"),
            CallbackQueryHandler(irregular_edit_start, pattern=r"^irreg:edit$"),
            CallbackQueryHandler(irregular_delete_start, pattern=r"^irreg:delete$"),
            CallbackQueryHandler(task_view_start, pattern=r"^task:view$"),
            CallbackQueryHandler(task_export_start, pattern=r"^task:export$"),
            CallbackQueryHandler(regular_add_start, pattern=r"^reg:add$"),
            CallbackQueryHandler(regular_manage_start, pattern=r"^reg:manage$"),
            CallbackQueryHandler(regular_edit_start, pattern=r"^reg:edit$"),
            CallbackQueryHandler(regular_delete_start, pattern=r"^reg:delete$"),
        ],
        states={
            TASK_ADD_TYPE: [CallbackQueryHandler(task_add_type_selected, pattern=r"^tasktype:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_ADD_DATE: [CallbackQueryHandler(task_add_date_selected, pattern=r"^taskdate:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_ADD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_add_description_received), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_ADD_ASSIGNEES: [CallbackQueryHandler(task_assignee_selected, pattern=r"^taskassignee:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_ADD_DEADLINE: [CallbackQueryHandler(task_deadline_selected, pattern=r"^taskdeadline:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_VIEW_DATE: [CallbackQueryHandler(task_view_date_selected, pattern=r"^taskviewdate:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EXPORT_DATE: [CallbackQueryHandler(task_export_date_selected, pattern=r"^taskexportdate:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_DATE: [CallbackQueryHandler(task_edit_date_selected, pattern=r"^taskeditdate:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_SELECT: [CallbackQueryHandler(task_edit_selected, pattern=r"^taskedit:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_FIELD: [CallbackQueryHandler(task_edit_field_selected, pattern=r"^taskeditfield:"), CallbackQueryHandler(task_status_selected, pattern=r"^taskstatus:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_edit_description_received), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_ASSIGNEES: [CallbackQueryHandler(task_assignee_selected, pattern=r"^taskassignee:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_EDIT_DEADLINE: [CallbackQueryHandler(task_deadline_selected, pattern=r"^taskdeadline:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_DELETE_DATE: [CallbackQueryHandler(task_delete_date_selected, pattern=r"^taskdeldate:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_DELETE_SELECT: [CallbackQueryHandler(task_delete_selected, pattern=r"^taskdel:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            TASK_DELETE_CONFIRM: [CallbackQueryHandler(task_delete_confirmed, pattern=r"^taskdelconfirm:yes:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_WEEKDAY: [CallbackQueryHandler(regular_add_weekday_selected, pattern=r"^regweekday:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_TYPE: [CallbackQueryHandler(regular_add_type_selected, pattern=r"^tasktype:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, regular_add_description_received), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_ASSIGNEE_MODE: [CallbackQueryHandler(regular_assignee_mode_selected, pattern=r"^regassigneemode:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_ASSIGNEES: [CallbackQueryHandler(regular_assignee_selected, pattern=r"^regassignee:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_ADD_DEADLINE: [CallbackQueryHandler(task_deadline_selected, pattern=r"^taskdeadline:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_DAY: [CallbackQueryHandler(regular_edit_day_selected, pattern=r"^regeditday:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_SELECT: [
                CallbackQueryHandler(regular_manage_selected, pattern=r"^regmanage:"),
                CallbackQueryHandler(regular_edit_selected, pattern=r"^regedit:"),
                CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$"),
            ],
            REG_MANAGE_ACTION: [
                CallbackQueryHandler(regular_manage_action_selected, pattern=r"^regmanageaction:"),
                CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$"),
            ],
            REG_EDIT_FIELD: [CallbackQueryHandler(regular_edit_field_selected, pattern=r"^regeditfield:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, regular_edit_description_received), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_WEEKDAY: [CallbackQueryHandler(regular_edit_weekday_selected, pattern=r"^regeditweekday:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_TYPE: [CallbackQueryHandler(regular_edit_type_selected, pattern=r"^tasktype:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_ASSIGNEE_MODE: [CallbackQueryHandler(regular_assignee_mode_selected, pattern=r"^regassigneemode:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_ASSIGNEES: [CallbackQueryHandler(regular_assignee_selected, pattern=r"^regassignee:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_EDIT_DEADLINE: [CallbackQueryHandler(task_deadline_selected, pattern=r"^taskdeadline:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_DELETE_DAY: [CallbackQueryHandler(regular_delete_day_selected, pattern=r"^regdelday:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_DELETE_SELECT: [CallbackQueryHandler(regular_delete_selected, pattern=r"^regdel:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
            REG_DELETE_CONFIRM: [CallbackQueryHandler(regular_delete_confirmed, pattern=r"^regdelconfirm:yes:"), CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$")],
        },
        fallbacks=[
            CallbackQueryHandler(tasks_back, pattern=r"^taskback:"),
            CallbackQueryHandler(tasks_cancel, pattern=r"^task:cancel$"),
        ],
        per_message=False,
        allow_reentry=True,
    )
    return [
        CallbackQueryHandler(tasks_menu, pattern=r"^section:tasks$"),
        CallbackQueryHandler(regular_tasks_menu, pattern=r"^task:regular$"),
        CallbackQueryHandler(irregular_tasks_menu, pattern=r"^task:irregular$"),
        CallbackQueryHandler(regular_view, pattern=r"^reg:view$"),
        CallbackQueryHandler(task_done_callback, pattern=r"^taskdone:"),
        conversation,
    ]
