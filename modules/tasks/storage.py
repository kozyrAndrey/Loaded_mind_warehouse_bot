import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import Date, DateTime, Index, Integer, String, Text, UniqueConstraint, delete, select, text
from sqlalchemy.orm import Mapped, mapped_column

from modules.payroll.google_sheets import get_employees
from modules.employees.roles import has_any_role, has_role
from modules.schedule.config import date_to_str, parse_date
from modules.schedule.google_sheets import get_schedule_matrix
from modules.storage.postgres import Base, get_engine, session_scope
from modules.tasks.config import (
    ASSIGNEE_MODE_NONE,
    ASSIGNEE_MODE_SPECIFIC,
    ASSIGNEE_MODE_WORKING_TODAY,
    DEFAULT_WEEKLY_TASK_TEMPLATES,
    TASK_SOURCE_MANUAL,
    TASK_SOURCE_TEMPLATE,
    TASK_STATUS_ACTIVE,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_TYPE_WAREHOUSE,
    WAREHOUSE_MANAGER_ROLE,
    WEEKDAY_NAMES,
    get_week_start_for_date,
)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_task_date", "task_date"),
        Index("ix_tasks_task_type", "task_type"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_template_id", "template_id"),
    )

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_date: Mapped[date] = mapped_column(Date, nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_ids: Mapped[str | None] = mapped_column(Text)
    assignee_names: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=TASK_STATUS_ACTIVE)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default=TASK_SOURCE_MANUAL)
    template_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    done_by_user_id: Mapped[str | None] = mapped_column(String(100))
    done_by_name: Mapped[str | None] = mapped_column(String(255))
    done_at: Mapped[datetime | None] = mapped_column(DateTime)


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    __table_args__ = (
        Index("ix_task_templates_weekday", "weekday"),
        Index("ix_task_templates_active", "is_active"),
        Index("ix_task_templates_series_id", "series_id"),
    )

    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    weekday_name: Mapped[str] = mapped_column(String(50), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_mode: Mapped[str] = mapped_column(String(50), nullable=False, default=ASSIGNEE_MODE_NONE)
    assignee_ids: Mapped[str | None] = mapped_column(Text)
    assignee_names: Mapped[str | None] = mapped_column(Text)
    deadline: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class TaskExport(Base):
    __tablename__ = "task_exports"
    __table_args__ = (
        UniqueConstraint("task_date", "export_type", name="uq_task_exports_date_type"),
        Index("ix_task_exports_task_date", "task_date"),
        Index("ix_task_exports_export_type", "export_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_date: Mapped[date] = mapped_column(Date, nullable=False)
    export_type: Mapped[str] = mapped_column(String(100), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(100))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    message_id: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


def generate_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def init_tasks_storage():
    Base.metadata.create_all(get_engine(), tables=[Task.__table__, TaskTemplate.__table__, TaskExport.__table__])
    ensure_tasks_columns()
    seed_default_task_templates_if_empty()
    ensure_task_template_series_ids()
    return True


def ensure_tasks_columns():
    statements = [
        "alter table tasks add column if not exists done_by_user_id varchar(100)",
        "alter table tasks add column if not exists done_by_name varchar(255)",
        "alter table tasks add column if not exists done_at timestamp",
        "create index if not exists ix_tasks_task_date on tasks (task_date)",
        "create index if not exists ix_tasks_task_type on tasks (task_type)",
        "create index if not exists ix_tasks_status on tasks (status)",
        "create index if not exists ix_tasks_template_id on tasks (template_id)",
        "alter table task_templates add column if not exists series_id varchar(64) not null default ''",
        "create index if not exists ix_task_templates_weekday on task_templates (weekday)",
        "create index if not exists ix_task_templates_active on task_templates (is_active)",
        "create index if not exists ix_task_templates_series_id on task_templates (series_id)",
        "create unique index if not exists uq_task_exports_date_type on task_exports (task_date, export_type)",
        "create index if not exists ix_task_exports_task_date on task_exports (task_date)",
        "create index if not exists ix_task_exports_export_type on task_exports (export_type)",
    ]
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def normalize_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_date(value)


def task_to_dict(task):
    return {
        "task_id": task.task_id,
        "Дата": date_to_str(task.task_date),
        "Тип задачи": task.task_type,
        "Описание": task.description,
        "Исполнители ID": task.assignee_ids or "",
        "Исполнители": task.assignee_names or "",
        "Дедлайн": task.deadline or "",
        "Статус": task.status,
        "Источник": task.source,
        "template_id": task.template_id or "",
        "Создал": task.created_by or "",
        "Создано": task.created_at.strftime("%d.%m.%Y %H:%M:%S") if task.created_at else "",
        "Обновлено": task.updated_at.strftime("%d.%m.%Y %H:%M:%S") if task.updated_at else "",
        "Выполнил ID": task.done_by_user_id or "",
        "Выполнил": task.done_by_name or "",
        "Выполнено": task.done_at.strftime("%d.%m.%Y %H:%M:%S") if task.done_at else "",
    }


def template_to_dict(template):
    return {
        "template_id": template.template_id,
        "series_id": template.series_id or template.template_id,
        "День недели": template.weekday_name,
        "weekday": template.weekday,
        "Тип задачи": template.task_type,
        "Описание": template.description,
        "Тип исполнителей": template.assignee_mode,
        "Исполнители ID": template.assignee_ids or "",
        "Исполнители": template.assignee_names or "",
        "Дедлайн": template.deadline or "",
        "Активно": template.is_active,
        "Создано": template.created_at.strftime("%d.%m.%Y %H:%M:%S") if template.created_at else "",
        "Обновлено": template.updated_at.strftime("%d.%m.%Y %H:%M:%S") if template.updated_at else "",
    }


def normalize_employee_list(employees):
    ids = []
    names = []
    for employee in employees or []:
        employee_id = str(employee.get("employee_id", "")).strip()
        if not employee_id:
            continue
        ids.append(employee_id)
        username = str(employee.get("telegram_username", "")).strip().lstrip("@")
        names.append(f"@{username}" if username else employee.get("full_name", employee_id))
    return ",".join(ids), ", ".join(names)


def seed_default_task_templates_if_empty():
    with session_scope() as session:
        has_templates = session.execute(select(TaskTemplate.template_id).limit(1)).first()
        if has_templates:
            return
        for item in DEFAULT_WEEKLY_TASK_TEMPLATES:
            weekday = int(item["weekday"])
            session.add(
                TaskTemplate(
                    template_id=generate_id("tpl"),
                    series_id="",
                    weekday_name=WEEKDAY_NAMES[weekday],
                    weekday=weekday,
                    task_type=item["task_type"],
                    description=item["description"],
                    assignee_mode=item.get("assignee_mode", ASSIGNEE_MODE_NONE),
                    deadline=item.get("deadline", ""),
                )
            )


def _template_series_signature(template):
    return (
        str(template.task_type or "").strip(),
        str(template.description or "").strip(),
        str(template.assignee_mode or "").strip(),
        str(template.assignee_ids or "").strip(),
        str(template.assignee_names or "").strip(),
        str(template.deadline or "").strip(),
        bool(template.is_active),
    )


def ensure_task_template_series_ids():
    """Group legacy identical weekday rows into stable recurring-task series."""
    with session_scope() as session:
        templates = session.execute(
            select(TaskTemplate)
            .where((TaskTemplate.series_id == "") | TaskTemplate.series_id.is_(None))
            .order_by(TaskTemplate.created_at, TaskTemplate.template_id)
        ).scalars().all()
        grouped = {}
        for template in templates:
            by_weekday = grouped.setdefault(_template_series_signature(template), {})
            by_weekday.setdefault(template.weekday, []).append(template)

        updated = 0
        for by_weekday in grouped.values():
            occurrences = max((len(rows) for rows in by_weekday.values()), default=0)
            for occurrence in range(occurrences):
                series_id = generate_id("tplseries")
                for rows in by_weekday.values():
                    if occurrence < len(rows):
                        rows[occurrence].series_id = series_id
                        updated += 1
        return updated


def get_task_by_id(task_id):
    with session_scope() as session:
        task = session.get(Task, str(task_id))
        return (None, task_to_dict(task)) if task else (None, None)


def get_tasks_by_date(day, include_cancelled=True):
    task_date = normalize_day(day)
    with session_scope() as session:
        statement = select(Task).where(Task.task_date == task_date).order_by(Task.created_at, Task.task_id)
        if not include_cancelled:
            statement = statement.where(Task.status != "cancelled")
        tasks = session.execute(statement).scalars().all()
    return [task_to_dict(task) for task in tasks]


def get_manual_tasks_by_date(day, include_cancelled=True):
    task_date = normalize_day(day)
    with session_scope() as session:
        statement = (
            select(Task)
            .where(Task.task_date == task_date, Task.source == TASK_SOURCE_MANUAL)
            .order_by(Task.created_at, Task.task_id)
        )
        if not include_cancelled:
            statement = statement.where(Task.status != "cancelled")
        tasks = session.execute(statement).scalars().all()
    return [task_to_dict(task) for task in tasks]


def get_employees_by_ids(employee_ids):
    wanted = {item.strip() for item in str(employee_ids or "").split(",") if item.strip()}
    if not wanted:
        return []
    return [
        employee for employee in get_employees(include_inactive=False)
        if str(employee.get("employee_id", "")).strip() in wanted
    ]


def get_working_employees_for_date(day):
    task_date = normalize_day(day)
    week_start = get_week_start_for_date(task_date)
    date_str = date_to_str(task_date)
    employees, dates, schedule, duty_by_date = get_schedule_matrix(week_start)

    working = []
    for employee in employees:
        shift_time = str(schedule.get(employee["employee_id"], {}).get(date_str, "")).strip()
        if shift_time:
            item = dict(employee)
            item["shift_time"] = shift_time
            working.append(item)
    return working


def create_task(day, task_type, description, assignee_employees=None, deadline="", source=TASK_SOURCE_MANUAL, template_id="", created_by="bot"):
    task_date = normalize_day(day)
    employee_ids, employee_names = normalize_employee_list(assignee_employees)
    task_id = generate_id("task")
    with session_scope() as session:
        task = Task(
            task_id=task_id,
            task_date=task_date,
            task_type=task_type,
            description=str(description).strip(),
            assignee_ids=employee_ids,
            assignee_names=employee_names,
            deadline=deadline or "",
            source=source,
            template_id=template_id or "",
            created_by=created_by,
        )
        session.add(task)
    return task_id


def delete_task(task_id):
    with session_scope() as session:
        task = session.get(Task, str(task_id))
        if not task:
            return None
        result = task_to_dict(task)
        session.delete(task)
        return result


def update_task_fields(task_id, **fields):
    field_map = {
        "Дата": "task_date",
        "Тип задачи": "task_type",
        "Описание": "description",
        "Исполнители ID": "assignee_ids",
        "Исполнители": "assignee_names",
        "Дедлайн": "deadline",
        "Статус": "status",
        "Источник": "source",
        "template_id": "template_id",
        "Создал": "created_by",
        "Выполнил ID": "done_by_user_id",
        "Выполнил": "done_by_name",
        "Выполнено": "done_at",
    }
    with session_scope() as session:
        task = session.get(Task, str(task_id))
        if not task:
            return False
        for key, value in fields.items():
            attr = field_map.get(key)
            if not attr:
                continue
            if attr == "task_date":
                value = normalize_day(value)
            if attr == "done_at" and isinstance(value, str) and value:
                value = datetime.now()
            setattr(task, attr, value)
        task.updated_at = datetime.now()
    return True


def set_task_assignees(task_id, employees):
    employee_ids, employee_names = normalize_employee_list(employees)
    return update_task_fields(task_id, **{"Исполнители ID": employee_ids, "Исполнители": employee_names})


def mark_task_done(task_id, employee, done=True):
    return update_task_fields(
        task_id,
        **{
            "Статус": TASK_STATUS_DONE if done else TASK_STATUS_ACTIVE,
            "Выполнил ID": str(employee.get("telegram_user_id", "")).strip() if done and employee else "",
            "Выполнил": employee.get("full_name", "") if done and employee else "",
            "Выполнено": datetime.now() if done else None,
        },
    )


def get_task_templates(active_only=True):
    with session_scope() as session:
        statement = select(TaskTemplate).order_by(TaskTemplate.weekday, TaskTemplate.created_at)
        if active_only:
            statement = statement.where(TaskTemplate.is_active.is_(True))
        templates = session.execute(statement).scalars().all()
    return [template_to_dict(template) for template in templates]


def get_task_template_by_id(template_id):
    with session_scope() as session:
        template = session.get(TaskTemplate, str(template_id))
        if not template:
            return None
        series_id = template.series_id or template.template_id
        templates = session.execute(
            select(TaskTemplate)
            .where(TaskTemplate.series_id == series_id)
            .order_by(TaskTemplate.weekday, TaskTemplate.created_at)
        ).scalars().all()
        if not templates:
            templates = [template]
        result = template_to_dict(template)
        result["weekdays"] = sorted({row.weekday for row in templates})
        result["Дни недели"] = ", ".join(WEEKDAY_NAMES[row] for row in result["weekdays"])
        return result


def create_task_template(weekday, task_type, description, assignee_mode=ASSIGNEE_MODE_NONE, assignee_employees=None, deadline=""):
    result = create_task_template_series(
        [weekday], task_type, description, assignee_mode, assignee_employees, deadline,
    )
    return result["template_ids"][0]


def create_task_template_series(weekdays, task_type, description, assignee_mode=ASSIGNEE_MODE_NONE, assignee_employees=None, deadline=""):
    weekdays = sorted({int(weekday) for weekday in weekdays})
    if not weekdays or any(weekday < 0 or weekday >= len(WEEKDAY_NAMES) for weekday in weekdays):
        raise ValueError("Нужно выбрать хотя бы один корректный день недели.")
    employee_ids, employee_names = normalize_employee_list(assignee_employees)
    if assignee_mode != ASSIGNEE_MODE_SPECIFIC:
        employee_ids = ""
        employee_names = ""

    series_id = generate_id("tplseries")
    template_ids = []
    with session_scope() as session:
        for weekday in weekdays:
            template_id = generate_id("tpl")
            template_ids.append(template_id)
            session.add(TaskTemplate(
                template_id=template_id,
                series_id=series_id,
                weekday_name=WEEKDAY_NAMES[weekday],
                weekday=weekday,
                task_type=task_type,
                description=str(description).strip(),
                assignee_mode=assignee_mode,
                assignee_ids=employee_ids,
                assignee_names=employee_names,
                deadline=deadline or "",
            ))
    return {"series_id": series_id, "template_ids": template_ids, "weekdays": weekdays}


def update_task_template_fields(template_id, **fields):
    field_map = {
        "weekday": "weekday",
        "Тип задачи": "task_type",
        "Описание": "description",
        "Тип исполнителей": "assignee_mode",
        "Исполнители ID": "assignee_ids",
        "Исполнители": "assignee_names",
        "Дедлайн": "deadline",
        "Активно": "is_active",
    }
    with session_scope() as session:
        template = session.get(TaskTemplate, str(template_id))
        if not template:
            return False
        series_id = template.series_id or template.template_id
        templates = [template]
        if "weekday" not in fields:
            templates = session.execute(
                select(TaskTemplate).where(TaskTemplate.series_id == series_id)
            ).scalars().all() or [template]
        for row in templates:
            for key, value in fields.items():
                attr = field_map.get(key)
                if not attr:
                    continue
                if attr == "weekday":
                    value = int(value)
                    row.weekday_name = WEEKDAY_NAMES[value]
                setattr(row, attr, value)
            row.updated_at = datetime.now()
    return True


def replace_task_template_series_weekdays(template_id, weekdays):
    weekdays = sorted({int(weekday) for weekday in weekdays})
    if not weekdays or any(weekday < 0 or weekday >= len(WEEKDAY_NAMES) for weekday in weekdays):
        raise ValueError("Нужно выбрать хотя бы один корректный день недели.")

    with session_scope() as session:
        anchor = session.get(TaskTemplate, str(template_id))
        if not anchor:
            return None
        series_id = anchor.series_id or anchor.template_id
        anchor.series_id = series_id
        rows = session.execute(
            select(TaskTemplate)
            .where(TaskTemplate.series_id == series_id)
            .order_by(TaskTemplate.created_at, TaskTemplate.template_id)
        ).scalars().all() or [anchor]
        by_weekday = {}
        for row in rows:
            if row.weekday in by_weekday or row.weekday not in weekdays:
                session.delete(row)
            else:
                by_weekday[row.weekday] = row

        source = anchor
        for weekday in weekdays:
            if weekday in by_weekday:
                continue
            session.add(TaskTemplate(
                template_id=generate_id("tpl"),
                series_id=series_id,
                weekday_name=WEEKDAY_NAMES[weekday],
                weekday=weekday,
                task_type=source.task_type,
                description=source.description,
                assignee_mode=source.assignee_mode,
                assignee_ids=source.assignee_ids,
                assignee_names=source.assignee_names,
                deadline=source.deadline,
                is_active=source.is_active,
            ))
        return {"series_id": series_id, "weekdays": weekdays}


def set_task_template_assignees(template_id, assignee_mode, employees=None):
    employee_ids, employee_names = normalize_employee_list(employees)
    if assignee_mode != ASSIGNEE_MODE_SPECIFIC:
        employee_ids = ""
        employee_names = ""
    return update_task_template_fields(
        template_id,
        **{
            "Тип исполнителей": assignee_mode,
            "Исполнители ID": employee_ids,
            "Исполнители": employee_names,
        },
    )


def delete_task_template(template_id):
    with session_scope() as session:
        template = session.get(TaskTemplate, str(template_id))
        if not template:
            return None
        result = template_to_dict(template)
        series_id = template.series_id or template.template_id
        templates = session.execute(
            select(TaskTemplate).where(TaskTemplate.series_id == series_id)
        ).scalars().all() or [template]
        result["weekdays"] = sorted({row.weekday for row in templates})
        for row in templates:
            session.delete(row)
        return result


def template_task_exists(day, template_id):
    task_date = normalize_day(day)
    with session_scope() as session:
        return bool(
            session.execute(
                select(Task.task_id).where(
                    Task.task_date == task_date,
                    Task.source == TASK_SOURCE_TEMPLATE,
                    Task.template_id == str(template_id),
                )
            ).first()
        )


def resolve_template_assignees(template, day):
    return []


def materialize_templates_for_date(day):
    task_date = normalize_day(day)
    created_count = 0
    for template in get_task_templates(active_only=True):
        try:
            weekday = int(str(template.get("weekday", "")).strip())
        except ValueError:
            continue
        if weekday != task_date.weekday():
            continue

        template_id = str(template.get("template_id", "")).strip()
        if not template_id or template_task_exists(task_date, template_id):
            continue

        create_task(
            day=task_date,
            task_type=str(template.get("Тип задачи", "")).strip(),
            description=str(template.get("Описание", "")).strip(),
            assignee_employees=resolve_template_assignees(template, task_date),
            deadline=str(template.get("Дедлайн", "")).strip(),
            source=TASK_SOURCE_TEMPLATE,
            template_id=template_id,
            created_by="template",
        )
        created_count += 1
    logging.info(
        "Task templates materialized for %s: created=%s",
        date_to_str(task_date),
        created_count,
    )
    return created_count


def assign_working_employees_to_unassigned_template_tasks(day):
    task_date = normalize_day(day)
    working_ids, working_names = normalize_employee_list(get_working_employees_for_date(task_date))
    updated_count = 0

    with session_scope() as session:
        tasks = (
            session.execute(
                select(Task).where(
                    Task.task_date == task_date,
                    Task.source == TASK_SOURCE_TEMPLATE,
                    Task.status != TASK_STATUS_CANCELLED,
                )
            )
            .scalars()
            .all()
        )

        for task in tasks:
            if not task.template_id or (task.assignee_ids or "").strip():
                continue

            task.assignee_ids = working_ids
            task.assignee_names = working_names
            task.updated_at = datetime.now()
            updated_count += 1

    logging.info(
        "Working employees assigned to unassigned template tasks for %s: updated=%s working=%s",
        date_to_str(task_date),
        updated_count,
        len([item for item in working_ids.split(",") if item.strip()]),
    )
    return {
        "checked": len(tasks),
        "updated": updated_count,
        "working_count": len([item for item in working_ids.split(",") if item.strip()]),
    }


def materialize_templates_for_period(start_day, end_day):
    current = normalize_day(start_day)
    end = normalize_day(end_day)
    total = 0
    while current <= end:
        total += materialize_templates_for_date(current)
        current += timedelta(days=1)
    return total


def materialize_next_week_templates(base_day):
    base = normalize_day(base_day)
    next_monday = base + timedelta(days=(7 - base.weekday()))
    return materialize_templates_for_period(next_monday, next_monday + timedelta(days=6))


def add_task_to_template(record, assignee_mode=None):
    task_date = normalize_day(record.get("Дата", ""))
    task_type = str(record.get("Тип задачи", "")).strip()
    if assignee_mode is None:
        if task_type == TASK_TYPE_WAREHOUSE and str(record.get("Исполнители ID", "")).strip():
            assignee_mode = ASSIGNEE_MODE_SPECIFIC
        elif task_type == TASK_TYPE_WAREHOUSE:
            assignee_mode = ASSIGNEE_MODE_WORKING_TODAY
        else:
            assignee_mode = ASSIGNEE_MODE_NONE

    template_id = generate_id("tpl")
    series_id = generate_id("tplseries")
    with session_scope() as session:
        session.add(
            TaskTemplate(
                template_id=template_id,
                series_id=series_id,
                weekday_name=WEEKDAY_NAMES[task_date.weekday()],
                weekday=task_date.weekday(),
                task_type=task_type,
                description=str(record.get("Описание", "")).strip(),
                assignee_mode=assignee_mode,
                assignee_ids=str(record.get("Исполнители ID", "")).strip() if assignee_mode == ASSIGNEE_MODE_SPECIFIC else "",
                assignee_names=str(record.get("Исполнители", "")).strip() if assignee_mode == ASSIGNEE_MODE_SPECIFIC else "",
                deadline=str(record.get("Дедлайн", "")).strip(),
            )
        )
    return template_id


def get_export_row(day, export_type):
    task_date = normalize_day(day)
    with session_scope() as session:
        export = (
            session.execute(
                select(TaskExport).where(TaskExport.task_date == task_date, TaskExport.export_type == str(export_type))
            )
            .scalars()
            .first()
        )
        return (export.id, export) if export else (None, None)


def upsert_task_export(day, export_type, chat_id, thread_id, message_id):
    task_date = normalize_day(day)
    now = datetime.now()
    with session_scope() as session:
        export = (
            session.execute(
                select(TaskExport).where(TaskExport.task_date == task_date, TaskExport.export_type == str(export_type))
            )
            .scalars()
            .first()
        )
        if export:
            export.chat_id = str(chat_id or "")
            export.thread_id = str(thread_id or "")
            export.message_id = str(message_id or "")
            export.version = int(export.version or 0) + 1
            export.updated_at = now
        else:
            session.add(
                TaskExport(
                    task_date=task_date,
                    export_type=str(export_type),
                    chat_id=str(chat_id or ""),
                    thread_id=str(thread_id or ""),
                    message_id=str(message_id or ""),
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def get_task_export(day, export_type):
    _, export = get_export_row(day, export_type)
    if not export:
        return None
    return {
        "chat_id": str(export.chat_id or "").strip(),
        "thread_id": str(export.thread_id or "").strip(),
        "message_id": str(export.message_id or "").strip(),
    }


def get_warehouse_managers():
    return [
        employee for employee in get_employees(include_inactive=False)
        if has_role(employee, WAREHOUSE_MANAGER_ROLE) and str(employee.get("telegram_user_id", "")).strip()
    ]


def can_user_complete_task(user, task_record, employee):
    if not employee:
        return False
    if has_any_role(employee, {"warehouse_manager", "brand_manager", "admin"}):
        return True
    if str(task_record.get("Тип задачи", "")).strip() != TASK_TYPE_WAREHOUSE:
        return False

    assignee_ids = {item.strip() for item in str(task_record.get("Исполнители ID", "")).split(",") if item.strip()}
    if not assignee_ids:
        return has_role(employee, "warehouse_employee")
    return str(employee.get("employee_id", "")).strip() in assignee_ids
