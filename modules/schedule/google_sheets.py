import logging
import uuid
from datetime import datetime

from config import OPERATIONS_GOOGLE_SHEET_ID
from modules.storage.google_archive import DatabaseWorksheet
from modules.payroll.google_sheets import (
    column_letter,
    safe_bool,
    now_str,
)
from modules.schedule.config import (
    SCHEDULE_ARCHIVE_SHEET,
    SCHEDULE_DUTIES_SHEET,
    SCHEDULE_EXPORTS_SHEET,
    SCHEDULE_SHEET,
    date_to_str,
    day_label,
    format_week_range,
    get_schedule_employees,
    parse_date,
    week_dates,
    week_end,
)


SCHEDULE_ARCHIVE_HEADERS = [
    "Неделя начала",
    "Неделя конца",
    "Дата",
    "День недели",
    "employee_id",
    "ФИО",
    "telegram_user_id",
    "Время выхода",
    "Дежурный",
    "Создано",
    "Обновлено",
    "Обновил",
]

DUTIES_HEADERS = [
    "Неделя начала",
    "Неделя конца",
    "Дата",
    "День недели",
    "employee_id",
    "ФИО",
    "Назначил",
    "Создано",
    "Обновлено",
]

EXPORTS_HEADERS = [
    "Неделя начала",
    "Неделя конца",
    "Версия",
    "chat_id",
    "thread_id",
    "message_id",
    "Файл",
    "Выгрузил",
    "Создано",
    "Тип записи",
    "Статус",
    "Обновлено",
]


GREEN = {"red": 0.82, "green": 0.93, "blue": 0.82}
RED = {"red": 0.96, "green": 0.80, "blue": 0.80}
BLUE = {"red": 0.76, "green": 0.87, "blue": 0.98}
HEADER_BG = {"red": 0.88, "green": 0.88, "blue": 0.88}
WHITE = {"red": 1, "green": 1, "blue": 1}


def operations_is_configured():
    return True


def get_schedule_worksheet(title, rows=1000, cols=30):
    visual_headers = [f"column_{index}" for index in range(1, max(cols, 12) + 1)]
    headers_by_title = {
        SCHEDULE_ARCHIVE_SHEET: SCHEDULE_ARCHIVE_HEADERS,
        SCHEDULE_DUTIES_SHEET: DUTIES_HEADERS,
        SCHEDULE_EXPORTS_SHEET: EXPORTS_HEADERS,
        SCHEDULE_SHEET: visual_headers,
    }
    headers = headers_by_title.get(title)
    if not headers:
        raise RuntimeError(f"Неизвестный operations-лист: {title}")
    return DatabaseWorksheet("operations", OPERATIONS_GOOGLE_SHEET_ID, title, headers)


def ensure_headers(worksheet, headers):
    first_row = worksheet.row_values(1)
    if first_row != headers:
        end_col = column_letter(len(headers))
        worksheet.update(f"A1:{end_col}1", [headers])


def schedule_records(worksheet):
    return worksheet.get_all_records(numericise_ignore=["all"])


def init_schedule_sheet():
    archive = get_schedule_worksheet(SCHEDULE_ARCHIVE_SHEET, rows=3000, cols=20)
    duties = get_schedule_worksheet(SCHEDULE_DUTIES_SHEET, rows=1000, cols=12)
    exports = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET, rows=500, cols=12)
    visual = get_schedule_worksheet(SCHEDULE_SHEET, rows=100, cols=12)

    ensure_headers(archive, SCHEDULE_ARCHIVE_HEADERS)
    ensure_headers(duties, DUTIES_HEADERS)
    ensure_headers(exports, EXPORTS_HEADERS)

    try:
        visual.format("A1:Z1", {"textFormat": {"bold": True}, "backgroundColor": HEADER_BG})
    except Exception:
        logging.exception("Не удалось применить базовое форматирование листа расписания")

    return True


def get_week_start_str(week_start):
    return date_to_str(week_start)


def get_week_end_str(week_start):
    return date_to_str(week_end(week_start))


def find_archive_row(week_start, employee_id, date_str):
    ws = get_schedule_worksheet(SCHEDULE_ARCHIVE_SHEET)
    records = schedule_records(ws)
    week_start_str = get_week_start_str(week_start)
    for index, record in enumerate(records, start=2):
        if (
            str(record.get("Неделя начала", "")).strip() == week_start_str
            and str(record.get("employee_id", "")).strip() == str(employee_id)
            and str(record.get("Дата", "")).strip() == str(date_str)
        ):
            return index
    return None


def schedule_has_submission(employee_id, week_start):
    ws = get_schedule_worksheet(SCHEDULE_ARCHIVE_SHEET)
    records = schedule_records(ws)
    week_start_str = get_week_start_str(week_start)
    for record in records:
        if (
            str(record.get("Неделя начала", "")).strip() == week_start_str
            and str(record.get("employee_id", "")).strip() == str(employee_id)
        ):
            return True
    return False


def upsert_schedule_day(employee, week_start, day, shift_time, updated_by):
    ws = get_schedule_worksheet(SCHEDULE_ARCHIVE_SHEET)
    week_start_str = get_week_start_str(week_start)
    week_end_str = get_week_end_str(week_start)
    date_str = date_to_str(day)
    row_index = find_archive_row(week_start, employee["employee_id"], date_str)
    existing_created = now_str()

    if row_index:
        current_values = ws.row_values(row_index)
        if len(current_values) >= 10 and current_values[9]:
            existing_created = current_values[9]

    row = [
        week_start_str,
        week_end_str,
        date_str,
        day_label(day),
        employee["employee_id"],
        employee["full_name"],
        employee.get("telegram_user_id", ""),
        shift_time or "",
        "",
        existing_created,
        now_str(),
        updated_by,
    ]

    end_col = column_letter(len(SCHEDULE_ARCHIVE_HEADERS))
    if row_index:
        ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    else:
        ws.append_row(row)


def upsert_employee_week_schedule(employee, week_start, shifts, updated_by):
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        upsert_schedule_day(employee, week_start, day, shifts.get(date_str, ""), updated_by)
    rebuild_current_schedule_sheet(week_start)


def get_schedule_matrix(week_start):
    employees = get_schedule_employees()
    dates = week_dates(week_start)
    date_strs = [date_to_str(d) for d in dates]
    week_start_str = get_week_start_str(week_start)

    archive_ws = get_schedule_worksheet(SCHEDULE_ARCHIVE_SHEET)
    duty_ws = get_schedule_worksheet(SCHEDULE_DUTIES_SHEET)

    schedule = {emp["employee_id"]: {date_str: "" for date_str in date_strs} for emp in employees}
    duty_by_date = {}

    for record in schedule_records(archive_ws):
        if str(record.get("Неделя начала", "")).strip() != week_start_str:
            continue
        employee_id = str(record.get("employee_id", "")).strip()
        date_str = str(record.get("Дата", "")).strip()
        if employee_id in schedule and date_str in schedule[employee_id]:
            schedule[employee_id][date_str] = str(record.get("Время выхода", "")).strip()

    for record in schedule_records(duty_ws):
        if str(record.get("Неделя начала", "")).strip() != week_start_str:
            continue
        date_str = str(record.get("Дата", "")).strip()
        employee_id = str(record.get("employee_id", "")).strip()
        if date_str in date_strs and employee_id:
            duty_by_date[date_str] = employee_id

    return employees, dates, schedule, duty_by_date


def clear_visual_sheet(ws, rows=100, cols=12):
    ws.clear()
    ws.resize(rows=rows, cols=cols)


def rebuild_current_schedule_sheet(week_start):
    ws = get_schedule_worksheet(SCHEDULE_SHEET, rows=100, cols=12)
    employees, dates, schedule, duty_by_date = get_schedule_matrix(week_start)

    header = [f"Расписание {format_week_range(week_start)}"] + [day_label(day) for day in dates]
    rows = [header]

    for employee in employees:
        row = [employee["full_name"]]
        for day in dates:
            date_str = date_to_str(day)
            value = schedule.get(employee["employee_id"], {}).get(date_str, "")
            if duty_by_date.get(date_str) == employee["employee_id"] and value:
                value = f"{value}\nДежурный"
            row.append(value)
        rows.append(row)

    clear_visual_sheet(ws, rows=max(30, len(rows) + 5), cols=8)
    ws.update(f"A1:H{len(rows)}", rows)

    try:
        ws.format("A1:H1", {"textFormat": {"bold": True}, "backgroundColor": HEADER_BG})
        ws.format(f"A2:A{len(rows)}", {"textFormat": {"bold": True}, "backgroundColor": WHITE})

        for row_index, employee in enumerate(employees, start=2):
            for col_offset, day in enumerate(dates, start=2):
                date_str = date_to_str(day)
                cell = f"{column_letter(col_offset)}{row_index}"
                shift_time = schedule.get(employee["employee_id"], {}).get(date_str, "")
                if duty_by_date.get(date_str) == employee["employee_id"] and shift_time:
                    color = BLUE
                elif shift_time:
                    color = GREEN
                else:
                    color = RED
                ws.format(cell, {"backgroundColor": color, "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"})
    except Exception:
        logging.exception("Не удалось применить цвета к листу расписания")

    return True


def get_employee_week_schedule(employee_id, week_start):
    employees, dates, schedule, duty_by_date = get_schedule_matrix(week_start)
    employee = next((emp for emp in employees if emp["employee_id"] == str(employee_id)), None)
    if not employee:
        return None
    return employee, dates, schedule.get(employee["employee_id"], {}), duty_by_date


def build_personal_schedule_text(employee_id, week_start):
    data = get_employee_week_schedule(employee_id, week_start)
    if not data:
        return "Сотрудник не найден в расписании."

    employee, dates, schedule, duty_by_date = data
    lines = [f"📅 Ваше расписание на {format_week_range(week_start)}", ""]
    for day in dates:
        date_str = date_to_str(day)
        value = schedule.get(date_str, "")
        duty_mark = " · дежурный" if duty_by_date.get(date_str) == employee_id and value else ""
        lines.append(f"{day_label(day)} — {value or 'выходной'}{duty_mark}")
    return "\n".join(lines)


def get_next_export_version(week_start):
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    week_start_str = get_week_start_str(week_start)
    max_version = 0
    for record in schedule_records(ws):
        if str(record.get("Неделя начала", "")).strip() != week_start_str:
            continue

        record_type = str(record.get("Тип записи", "")).strip()
        # Старые строки без типа считаем выгрузками расписания.
        if record_type and record_type != "schedule_export":
            continue

        try:
            version = int(str(record.get("Версия", "0")).strip() or 0)
        except ValueError:
            version = 0
        max_version = max(max_version, version)
    return max_version + 1


def append_schedule_export(week_start, version, chat_id, thread_id, message_id, filename, sent_by):
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    ws.append_row([
        get_week_start_str(week_start),
        get_week_end_str(week_start),
        version,
        chat_id,
        thread_id,
        message_id,
        filename,
        sent_by,
        now_str(),
        "schedule_export",
        "sent",
        now_str(),
    ])


def get_latest_schedule_export(week_start):
    """Возвращает последнюю актуальную выгрузку расписания для выбранной недели."""
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    week_start_str = get_week_start_str(week_start)
    latest = None

    for index, record in enumerate(schedule_records(ws), start=2):
        if str(record.get("Неделя начала", "")).strip() != week_start_str:
            continue

        record_type = str(record.get("Тип записи", "")).strip()
        if record_type and record_type != "schedule_export":
            continue

        status = str(record.get("Статус", "")).strip()
        if status in {"replaced", "deleted"}:
            continue

        try:
            version = int(str(record.get("Версия", "0")).strip() or 0)
        except ValueError:
            version = 0

        candidate = {
            "row_index": index,
            "version": version,
            "chat_id": str(record.get("chat_id", "")).strip(),
            "thread_id": str(record.get("thread_id", "")).strip(),
            "message_id": str(record.get("message_id", "")).strip(),
        }
        if latest is None or version >= latest["version"]:
            latest = candidate

    return latest


def mark_schedule_export_status(row_index, status):
    """Обновляет статус старой выгрузки после ее замены/удаления в Telegram."""
    if not row_index:
        return False

    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    status_col = column_letter(EXPORTS_HEADERS.index("Статус") + 1)
    updated_col = column_letter(EXPORTS_HEADERS.index("Обновлено") + 1)
    ws.update(f"{status_col}{row_index}:{updated_col}{row_index}", [[status, now_str()]])
    return True


def get_missing_schedule_employees(week_start):
    """Возвращает сотрудников, которые еще не заполнили расписание на неделю."""
    missing = []
    for employee in get_schedule_employees():
        if not schedule_has_submission(employee["employee_id"], week_start):
            missing.append(employee)
    return missing


def get_schedule_reminder_row(week_start):
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    week_start_str = get_week_start_str(week_start)
    records = schedule_records(ws)

    for index, record in enumerate(records, start=2):
        if (
            str(record.get("Неделя начала", "")).strip() == week_start_str
            and str(record.get("Тип записи", "")).strip() == "schedule_reminder"
        ):
            return index, record

    return None, None


def upsert_schedule_reminder(week_start, chat_id, thread_id, message_id, status="active"):
    """Сохраняет ID последнего сообщения-напоминания в лист «Выгрузки расписания»."""
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    row_index, record = get_schedule_reminder_row(week_start)
    created_at = now_str()

    if record:
        created_at = str(record.get("Создано", "")).strip() or created_at

    row = [
        get_week_start_str(week_start),
        get_week_end_str(week_start),
        "",
        chat_id,
        thread_id,
        message_id,
        "",
        "bot",
        created_at,
        "schedule_reminder",
        status,
        now_str(),
    ]

    end_col = column_letter(len(EXPORTS_HEADERS))
    if row_index:
        ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    else:
        ws.append_row(row)


def get_active_schedule_reminder(week_start):
    row_index, record = get_schedule_reminder_row(week_start)
    if not record:
        return None

    if str(record.get("Статус", "")).strip() not in {"active", ""}:
        return None

    return {
        "row_index": row_index,
        "chat_id": str(record.get("chat_id", "")).strip(),
        "thread_id": str(record.get("thread_id", "")).strip(),
        "message_id": str(record.get("message_id", "")).strip(),
        "status": str(record.get("Статус", "")).strip() or "active",
    }


def mark_schedule_reminder_status(week_start, status):
    ws = get_schedule_worksheet(SCHEDULE_EXPORTS_SHEET)
    row_index, record = get_schedule_reminder_row(week_start)
    if not row_index:
        return False

    status_col = column_letter(EXPORTS_HEADERS.index("Статус") + 1)
    updated_col = column_letter(EXPORTS_HEADERS.index("Обновлено") + 1)

    ws.update(f"{status_col}{row_index}:{updated_col}{row_index}", [[status, now_str()]])
    return True


def set_duty_for_day(week_start, day, employee, assigned_by):
    ws = get_schedule_worksheet(SCHEDULE_DUTIES_SHEET)
    week_start_str = get_week_start_str(week_start)
    date_str = date_to_str(day)
    records = schedule_records(ws)
    row_index = None
    created_at = now_str()

    for index, record in enumerate(records, start=2):
        if (
            str(record.get("Неделя начала", "")).strip() == week_start_str
            and str(record.get("Дата", "")).strip() == date_str
        ):
            row_index = index
            created_at = str(record.get("Создано", "")).strip() or created_at
            break

    row = [
        week_start_str,
        get_week_end_str(week_start),
        date_str,
        day_label(day),
        employee["employee_id"] if employee else "",
        employee["full_name"] if employee else "",
        assigned_by,
        created_at,
        now_str(),
    ]
    end_col = column_letter(len(DUTIES_HEADERS))

    if row_index:
        ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    else:
        ws.append_row(row)


def set_week_duties(week_start, duty_by_date, assigned_by):
    for day in week_dates(week_start):
        date_str = date_to_str(day)
        employee = duty_by_date.get(date_str)
        set_duty_for_day(week_start, day, employee, assigned_by)
    rebuild_current_schedule_sheet(week_start)


def suggest_week_duties(week_start):
    employees, dates, schedule, current_duties = get_schedule_matrix(week_start)
    employees_by_id = {emp["employee_id"]: emp for emp in employees}
    duty_counts = {emp["employee_id"]: 0 for emp in employees}
    previous_employee_id = None
    result = {}

    for day in dates:
        date_str = date_to_str(day)
        candidates = [
            emp for emp in employees
            if schedule.get(emp["employee_id"], {}).get(date_str)
        ]

        if not candidates:
            result[date_str] = None
            previous_employee_id = None
            continue

        if len(candidates) > 1:
            non_previous = [emp for emp in candidates if emp["employee_id"] != previous_employee_id]
            if non_previous:
                candidates = non_previous

        candidates.sort(key=lambda emp: (duty_counts.get(emp["employee_id"], 0), emp["full_name"]))
        chosen = candidates[0]
        result[date_str] = chosen
        duty_counts[chosen["employee_id"]] += 1
        previous_employee_id = chosen["employee_id"]

    return result
