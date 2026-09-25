from modules.schedule.config import date_to_str
from modules.tasks.config import (
    ASSIGNEE_MODE_NONE,
    ASSIGNEE_MODE_SPECIFIC,
    ASSIGNEE_MODE_WORKING_TODAY,
    TASK_SOURCE_TEMPLATE,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_TYPE_GENERAL,
    TASK_TYPE_LABELS,
    TASK_TYPE_WAREHOUSE,
    WEEKDAY_NAMES,
)


def employee_mention(employee):
    username = str(employee.get("telegram_username", "")).strip().lstrip("@")
    return f"@{username}" if username else employee.get("full_name", "")


def task_checkbox(status):
    if status == TASK_STATUS_DONE:
        return "✅"
    if status == TASK_STATUS_CANCELLED:
        return "🚫"
    return "⬜"


def format_assignees(record):
    return str(record.get("Исполнители", "")).strip() or "не назначены"


def task_source_label(record):
    return "из шаблона" if str(record.get("Источник", "")).strip() == TASK_SOURCE_TEMPLATE else "разовая"


def format_task_line(index, record, include_assignees=True, include_source=False):
    status = str(record.get("Статус", "")).strip()
    description = str(record.get("Описание", "")).strip()
    deadline = str(record.get("Дедлайн", "")).strip()

    lines = [f"{task_checkbox(status)} {index}. {description}"]
    if include_source:
        lines.append(f"Источник: {task_source_label(record)}")
    if include_assignees:
        lines.append(f"Исполнители: {format_assignees(record)}")
    if deadline:
        lines.append(f"Дедлайн: {deadline}")
    return "\n".join(lines)


def filter_active_export_tasks(tasks, task_type):
    filtered = [
        task for task in tasks
        if str(task.get("Тип задачи", "")).strip() == task_type
        and str(task.get("Статус", "")).strip() != TASK_STATUS_CANCELLED
    ]
    return sorted(filtered, key=task_deadline_sort_key)


def task_deadline_sort_key(task):
    deadline = str(task.get("Дедлайн", "")).strip()
    if not deadline:
        return (1, "99:99")
    return (0, deadline)


def format_warehouse_tasks_message(day, tasks):
    warehouse_tasks = filter_active_export_tasks(tasks, TASK_TYPE_WAREHOUSE)
    if not warehouse_tasks:
        return f"📋 Складские задачи на {date_to_str(day)}\n\nЗадач пока нет"

    lines = [f"📋 Складские задачи на {date_to_str(day)}", ""]
    for index, task in enumerate(warehouse_tasks, start=1):
        lines.append(format_task_line(index, task, include_assignees=True))
        lines.append("")
    return "\n".join(lines).strip()


def format_general_tasks_message(day, tasks):
    general_tasks = filter_active_export_tasks(tasks, TASK_TYPE_GENERAL)
    if not general_tasks:
        return f"📋 Нескладские задачи на {date_to_str(day)}\n\nЗадач пока нет"

    lines = [f"📋 Нескладские задачи на {date_to_str(day)}", ""]
    for index, task in enumerate(general_tasks, start=1):
        lines.append(format_task_line(index, task, include_assignees=False))
        lines.append("")
    return "\n".join(lines).strip()


def format_all_tasks_for_private_view(day, tasks):
    warehouse_tasks = sorted(
        [task for task in tasks if str(task.get("Тип задачи", "")).strip() == TASK_TYPE_WAREHOUSE],
        key=task_deadline_sort_key,
    )
    general_tasks = sorted(
        [task for task in tasks if str(task.get("Тип задачи", "")).strip() == TASK_TYPE_GENERAL],
        key=task_deadline_sort_key,
    )

    lines = [f"📋 Задачи на {date_to_str(day)}", "", "Складские:"]
    if warehouse_tasks:
        for index, task in enumerate(warehouse_tasks, start=1):
            lines.append(format_task_line(index, task, include_assignees=True, include_source=True))
            lines.append("")
    else:
        lines.append("Задач пока нет")
        lines.append("")

    lines.append("Нескладские:")
    if general_tasks:
        for index, task in enumerate(general_tasks, start=1):
            lines.append(format_task_line(index, task, include_assignees=False, include_source=True))
            lines.append("")
    else:
        lines.append("Задач пока нет")

    return "\n".join(lines).strip()


def template_assignee_label(template):
    mode = str(template.get("Тип исполнителей", "")).strip() or ASSIGNEE_MODE_NONE
    if mode == ASSIGNEE_MODE_WORKING_TODAY:
        return "исполнители: все, кто работает в этот день"
    if mode == ASSIGNEE_MODE_SPECIFIC:
        return f"исполнители: {format_assignees(template)}"
    return "исполнители не назначаются"


def format_regular_tasks_view(templates):
    if not templates:
        return "📋 Шаблоны регулярных задач\n\nШаблонов пока нет."

    templates_by_weekday = {weekday: [] for weekday in range(len(WEEKDAY_NAMES))}
    seen_by_weekday = {weekday: set() for weekday in range(len(WEEKDAY_NAMES))}
    for template in templates:
        try:
            weekday = int(str(template.get("weekday", "")).strip())
        except ValueError:
            continue
        if weekday < 0 or weekday >= len(WEEKDAY_NAMES):
            continue
        template_id = str(template.get("template_id") or "").strip()
        if not template_id or template_id in seen_by_weekday[weekday]:
            continue
        seen_by_weekday[weekday].add(template_id)
        templates_by_weekday[weekday].append(template)

    lines = ["📋 Шаблоны регулярных задач", ""]
    for weekday, weekday_name in enumerate(WEEKDAY_NAMES):
        day_templates = sorted(
            templates_by_weekday[weekday],
            key=lambda template: (
                0 if str(template.get("Тип задачи", "")).strip() == TASK_TYPE_WAREHOUSE else 1,
                str(template.get("Описание", "")).casefold(),
                str(template.get("Дедлайн", "")),
            ),
        )
        if not day_templates:
            continue

        lines.append(f"📅 {weekday_name}")
        for index, template in enumerate(day_templates, start=1):
            task_type = TASK_TYPE_LABELS.get(str(template.get("Тип задачи", "")).strip(), "Задача")
            description = str(template.get("Описание", "")).strip()
            deadline = str(template.get("Дедлайн", "")).strip()

            lines.append(f"{index}. {task_type}: {description}")
            lines.append(f"   {template_assignee_label(template)}")
            if deadline:
                lines.append(f"   дедлайн: {deadline}")
        lines.append("")

    return "\n".join(lines).strip()


def format_daily_staff_message(day, employees, schedule, duty_by_date):
    date_str = date_to_str(day)
    rows = []

    for employee in employees:
        employee_id = employee["employee_id"]
        shift_time = str(schedule.get(employee_id, {}).get(date_str, "")).strip()
        if not shift_time:
            continue
        suffix = ", дежурный" if duty_by_date.get(date_str) == employee_id else ""
        rows.append((shift_time, f"к {shift_time} {employee_mention(employee)}{suffix}"))

    if not rows:
        return "👋 утро доброе\n\nсегодня в танке никого нет"

    rows.sort(key=lambda item: item[0])
    return "👋 утро доброе\n\nсегодня в танке 🇷🇺\n\n" + "\n".join(row[1] for row in rows)
