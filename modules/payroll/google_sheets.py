import json
import logging
import uuid
from datetime import datetime, timedelta

from modules.storage.google_archive import DatabaseWorksheet
from modules.payroll.additional_pay import ADDITIONAL_PAY_HEADERS, ADDITIONAL_PAY_SHEET
from modules.payroll.config import (
    KPI_DAILY_COLUMN_BY_KPI_ID,
    KPI_DAILY_COLUMNS,
    MANAGER_ROLES,
    PAYROLL_EMPLOYEES,
    PAYROLL_KPI,
    find_penalty_category_by_type_name,
    normalize_username,
)
from modules.employees.roles import (
    employee_roles,
    has_any_role,
    normalize_roles,
    primary_role,
    roles_to_storage,
)


EMPLOYEES_SHEET = "Сотрудники"
REPORTS_SHEET = "Ежедневные отчеты"
MANAGER_REPORTS_SHEET = "Отчеты руководителя склада"
EXPENSES_SHEET = "Расходы"
PENALTIES_SHEET = "Штрафы"
BONUSES_SHEET = "Премиальные"
KPI_SHEET = "KPI"
PERIODS_SHEET = "Расчетные периоды"
KPI_DAILY_SHEET = "KPI за день"
DRIVERS_SHEET = "Водители"
DRIVER_PAYMENTS_SHEET = "Оплата водителям"
DRIVER_WRITE_OFFS_SHEET = "Списания водителям"
PAYMENT_MODE_HOURLY = "hourly"
PAYMENT_MODE_SHIFT = "shift"
SHIFT_TYPE_FULL = "full"
SHIFT_TYPE_HALF = "half"
SHIFT_TYPE_LABELS = {
    SHIFT_TYPE_FULL: "10:00 — полная смена",
    SHIFT_TYPE_HALF: "15:00 — половина смены",
}
PAYMENT_MODE_LABELS = {
    PAYMENT_MODE_HOURLY: "По часам",
    PAYMENT_MODE_SHIFT: "Посменно",
}

EMPLOYEE_HEADERS = [
    "employee_id",
    "ФИО",
    "Телефон",
    "telegram_user_id",
    "telegram_username",
    "role",
    "roles",
    "hourly_rate",
    "fixed_salary",
    "include_in_common_fund",
    "is_active",
]

REPORT_HEADERS = [
    "report_id",
    "Дата",
    "employee_id",
    "ФИО",
    "telegram_user_id",
    "Рабочий промежуток",
    "Отработано часов",
    "Тип смены",
    "Обед",
    "Задачи",
    "KPI данные",
    "KPI сумма",
    "telegram_chat_id",
    "telegram_thread_id",
    "telegram_message_id",
    "Создано",
    "Обновлено",
]

MANAGER_REPORT_HEADERS = [
    "manager_report_id",
    "Дата",
    "employee_id",
    "ФИО",
    "telegram_user_id",
    "Данные отчета",
    "Сообщения Telegram",
    "Создано",
    "Обновлено",
]

EXPENSE_HEADERS = [
    "expense_id",
    "Дата",
    "employee_id",
    "ФИО",
    "Комментарий",
    "Сумма",
    "Создал",
    "Создано",
]

PENALTY_HEADERS = [
    "penalty_id",
    "Дата",
    "employee_id",
    "ФИО",
    "Категория штрафа",
    "Тип штрафа",
    "Комментарий",
    "Сумма",
    "Назначил",
    "Создано",
]

BONUS_HEADERS = [
    "bonus_id",
    "Дата",
    "employee_id",
    "ФИО",
    "Комментарий",
    "Сумма",
    "Назначил",
    "Создано",
]

OLD_PENALTY_HEADERS_WITH_TYPE = [
    "penalty_id",
    "Дата",
    "employee_id",
    "ФИО",
    "Тип штрафа",
    "Комментарий",
    "Сумма",
    "Назначил",
    "Создано",
]

OLD_PENALTY_HEADERS = [
    "penalty_id",
    "Дата",
    "employee_id",
    "ФИО",
    "Комментарий",
    "Сумма",
    "Назначил",
    "Создано",
]

KPI_HEADERS = [
    "kpi_id",
    "Название",
    "Ставка",
    "Активно",
]

PERIOD_HEADERS = [
    "period_id",
    "Название",
    "Дата начала",
    "Дата конца",
    "Режим оплаты",
    "Статус",
    "Создал",
    "Создано",
    "Обновлено",
]

KPI_DAILY_HEADERS = ["Дата", "Имя сотрудника", "Отработанные часы"] + KPI_DAILY_COLUMNS + ["Общее"]

DRIVER_HEADERS = [
    "driver_id",
    "ФИО",
    "Телефон",
    "Номер машины",
    "Комментарий",
    "Активен",
    "Создал",
    "Создано",
]

DRIVER_PAYMENT_HEADERS = [
    "driver_payment_id",
    "Дата",
    "driver_id",
    "ФИО водителя",
    "Номер машины",
    "Сумма",
    "Комментарий",
    "Создал",
    "Создано",
]

DRIVER_WRITE_OFF_HEADERS = [
    "driver_write_off_id",
    "Дата",
    "driver_id",
    "ФИО водителя",
    "Номер машины",
    "Сумма",
    "Комментарий",
    "Создал",
    "Создано",
]


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def date_today_str():
    return datetime.now().strftime("%d.%m.%Y")


def parse_date(value):
    value = str(value).strip()
    return datetime.strptime(value, "%d.%m.%Y")


def validate_date(value):
    try:
        parse_date(value)
        return True
    except ValueError:
        return False


def normalize_payment_mode(value):
    value = str(value or "").strip().lower()
    if value in {PAYMENT_MODE_SHIFT, "посменно", "смена"}:
        return PAYMENT_MODE_SHIFT
    return PAYMENT_MODE_HOURLY


def payment_mode_label(value):
    mode = normalize_payment_mode(value)
    return PAYMENT_MODE_LABELS.get(mode, PAYMENT_MODE_LABELS[PAYMENT_MODE_HOURLY])


def normalize_shift_type(value):
    value = str(value or "").strip().lower()
    if value in {SHIFT_TYPE_FULL, "полная", "full_shift"}:
        return SHIFT_TYPE_FULL
    if value in {SHIFT_TYPE_HALF, "половина", "half_shift"}:
        return SHIFT_TYPE_HALF
    return ""


def shift_type_label(value):
    return SHIFT_TYPE_LABELS.get(normalize_shift_type(value), "Не указан")


def paid_hours_for_report(hours, payment_mode, shift_type=""):
    if normalize_payment_mode(payment_mode) != PAYMENT_MODE_SHIFT:
        return safe_float(hours)
    return 4.0 if normalize_shift_type(shift_type) == SHIFT_TYPE_HALF else 8.0


def date_in_range(value, start_date, end_date):
    try:
        current = parse_date(value)
        start = parse_date(start_date)
        end = parse_date(end_date)
        return start <= current <= end
    except ValueError:
        return False


def safe_float(value):
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", ".").strip())
    except Exception:
        return 0.0


def safe_hourly_rate(value):
    # Ставка из справочника «Сотрудники» — актуальный источник данных.
    # Не исправляем её эвристикой: иначе корректная ставка выше 1000 ₽
    # молча уменьшалась в 10 раз при расчёте ЗП.
    return safe_float(value)


def safe_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "да", "истина"}


def money(value):
    value = float(value or 0)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def generate_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def column_letter(index):
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def payroll_is_configured():
    return True


def get_worksheet(title, rows=1000, cols=30):
    headers_by_title = {
        EMPLOYEES_SHEET: EMPLOYEE_HEADERS,
        REPORTS_SHEET: REPORT_HEADERS,
        MANAGER_REPORTS_SHEET: MANAGER_REPORT_HEADERS,
        EXPENSES_SHEET: EXPENSE_HEADERS,
        PENALTIES_SHEET: PENALTY_HEADERS,
        BONUSES_SHEET: BONUS_HEADERS,
        KPI_SHEET: KPI_HEADERS,
        PERIODS_SHEET: PERIOD_HEADERS,
        KPI_DAILY_SHEET: KPI_DAILY_HEADERS,
        ADDITIONAL_PAY_SHEET: ADDITIONAL_PAY_HEADERS,
        DRIVERS_SHEET: DRIVER_HEADERS,
        DRIVER_PAYMENTS_SHEET: DRIVER_PAYMENT_HEADERS,
        DRIVER_WRITE_OFFS_SHEET: DRIVER_WRITE_OFF_HEADERS,
    }
    headers = headers_by_title.get(title)
    if not headers:
        raise RuntimeError(f"Неизвестный payroll-раздел: {title}")
    return DatabaseWorksheet("payroll", "payroll", title, headers)


def ensure_headers(worksheet, headers):
    first_row = worksheet.row_values(1)
    if first_row != headers:
        end_col = column_letter(len(headers))
        worksheet.update(f"A1:{end_col}1", [headers])


def ensure_penalty_headers(worksheet):
    """Обновляет лист «Штрафы» до схемы с колонками «Категория штрафа» и «Тип штрафа»."""
    values = worksheet.get_all_values()

    if not values:
        end_col = column_letter(len(PENALTY_HEADERS))
        worksheet.update(f"A1:{end_col}1", [PENALTY_HEADERS])
        return

    first_row = values[0]

    if first_row == PENALTY_HEADERS:
        return

    new_values = [PENALTY_HEADERS]

    if first_row == OLD_PENALTY_HEADERS_WITH_TYPE:
        for row in values[1:]:
            padded = row + [""] * (len(OLD_PENALTY_HEADERS_WITH_TYPE) - len(row))
            penalty_type = padded[4] or "Другое"
            new_values.append([
                padded[0], padded[1], padded[2], padded[3],
                find_penalty_category_by_type_name(penalty_type),
                penalty_type, padded[5], padded[6], padded[7], padded[8],
            ])

        worksheet.clear()
        end_col = column_letter(len(PENALTY_HEADERS))
        worksheet.update(f"A1:{end_col}{len(new_values)}", new_values)
        return

    if first_row == OLD_PENALTY_HEADERS:
        for row in values[1:]:
            padded = row + [""] * (len(OLD_PENALTY_HEADERS) - len(row))
            new_values.append([
                padded[0], padded[1], padded[2], padded[3],
                "Другое", "Другое", padded[4], padded[5], padded[6], padded[7],
            ])

        worksheet.clear()
        end_col = column_letter(len(PENALTY_HEADERS))
        worksheet.update(f"A1:{end_col}{len(new_values)}", new_values)
        return

    ensure_headers(worksheet, PENALTY_HEADERS)


def records_from_worksheet(worksheet):
    # Исторически этот параметр защищал импортированные значения от
    # авто-преобразования. DB-backed адаптер его игнорирует, но сигнатура
    # совместима со старым worksheet API.
    return worksheet.get_all_records(numericise_ignore=["all"])



def rows_to_dict_by_key(values, key_name):
    if not values or len(values) <= 1:
        return {}, []

    headers = values[0]
    rows = values[1:]
    result = {}

    for index, row in enumerate(rows, start=2):
        row_data = dict(zip(headers, row))
        key_value = str(row_data.get(key_name, "")).strip()

        if key_value:
            result[key_value] = {
                "row_index": index,
                "row_data": row_data,
            }

    return result, headers


def sync_employees_sheet(worksheet):
    """Добавляет только отсутствующих стартовых сотрудников.

    Существующие строки управляются из раздела «Сотрудники». Их нельзя
    перезаписывать значениями PAYROLL_EMPLOYEES при каждом запуске: так
    терялись изменённые оклады, ставки и другие поля.
    """
    values = worksheet.get_all_values()
    existing_by_id, _ = rows_to_dict_by_key(values, "employee_id")

    rows_to_append = []

    for employee in PAYROLL_EMPLOYEES:
        row = [
            employee["employee_id"],
            employee["full_name"],
            str(employee.get("phone", "")),
            str(employee.get("telegram_user_id", "")),
            employee.get("telegram_username", ""),
            employee["role"],
            roles_to_storage(employee.get("roles"), employee["role"]),
            str(employee["hourly_rate"]).replace(".", ","),
            employee["fixed_salary"],
            str(employee["include_in_common_fund"]).upper(),
            str(employee["is_active"]).upper(),
        ]

        if employee["employee_id"] not in existing_by_id:
            rows_to_append.append(row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)


def sync_kpi_sheet(worksheet):
    """Добавляет отсутствующие стартовые KPI из payroll_config.py.

    Существующие строки намеренно не обновляются: справочник KPI управляется
    руководителями через бот, и их изменения должны переживать перезапуск.
    """
    values = worksheet.get_all_values()
    existing_by_id, _ = rows_to_dict_by_key(values, "kpi_id")

    rows_to_append = []

    for item in PAYROLL_KPI:
        row = [
            item["kpi_id"],
            item["name"],
            item["rate"],
            str(item["is_active"]).upper(),
        ]

        if item["kpi_id"] not in existing_by_id:
            rows_to_append.append(row)

    if rows_to_append:
        worksheet.append_rows(rows_to_append)


def ensure_bootstrap_admin(worksheet):
    """Создаёт первого администратора только в пустом справочнике."""
    from config import BOOTSTRAP_ADMIN_IDS

    if not BOOTSTRAP_ADMIN_IDS or any(row.get("employee_id") for row in worksheet.get_all_records()):
        return
    for user_id in sorted(BOOTSTRAP_ADMIN_IDS):
        worksheet.append_row([
            f"bootstrap_{user_id}", "Администратор Loaded Mind", "", user_id, "",
            "admin", "admin", "0", 0, "FALSE", "TRUE",
        ])


def init_payroll_sheet():
    from modules.storage.structured_sheets import init_structured_sheet_tables

    init_structured_sheet_tables()
    employees_ws = get_worksheet(EMPLOYEES_SHEET, rows=200, cols=12)
    reports_ws = get_worksheet(REPORTS_SHEET, rows=3000, cols=20)
    manager_reports_ws = get_worksheet(MANAGER_REPORTS_SHEET, rows=1000, cols=12)
    expenses_ws = get_worksheet(EXPENSES_SHEET, rows=1000, cols=12)
    penalties_ws = get_worksheet(PENALTIES_SHEET, rows=1000, cols=12)
    bonuses_ws = get_worksheet(BONUSES_SHEET, rows=1000, cols=12)
    kpi_ws = get_worksheet(KPI_SHEET, rows=100, cols=6)
    periods_ws = get_worksheet(PERIODS_SHEET, rows=200, cols=10)
    kpi_daily_ws = get_worksheet(KPI_DAILY_SHEET, rows=3000, cols=20)
    additional_pay_ws = get_worksheet(ADDITIONAL_PAY_SHEET, rows=1000, cols=22)
    drivers_ws = get_worksheet(DRIVERS_SHEET, rows=300, cols=8)
    driver_payments_ws = get_worksheet(DRIVER_PAYMENTS_SHEET, rows=2000, cols=10)
    driver_write_offs_ws = get_worksheet(DRIVER_WRITE_OFFS_SHEET, rows=2000, cols=10)

    ensure_headers(employees_ws, EMPLOYEE_HEADERS)
    ensure_headers(reports_ws, REPORT_HEADERS)
    ensure_headers(manager_reports_ws, MANAGER_REPORT_HEADERS)
    ensure_headers(expenses_ws, EXPENSE_HEADERS)
    ensure_penalty_headers(penalties_ws)
    ensure_headers(bonuses_ws, BONUS_HEADERS)
    ensure_headers(kpi_ws, KPI_HEADERS)
    ensure_headers(periods_ws, PERIOD_HEADERS)
    ensure_headers(kpi_daily_ws, KPI_DAILY_HEADERS)
    ensure_headers(additional_pay_ws, ADDITIONAL_PAY_HEADERS)
    ensure_headers(drivers_ws, DRIVER_HEADERS)
    ensure_headers(driver_payments_ws, DRIVER_PAYMENT_HEADERS)
    ensure_headers(driver_write_offs_ws, DRIVER_WRITE_OFF_HEADERS)

    # Сотрудники и KPI из конфига служат только стартовым наполнением.
    # Существующие записи управляются через бот и при запуске не перезаписываются.
    sync_employees_sheet(employees_ws)
    sync_kpi_sheet(kpi_ws)
    ensure_bootstrap_admin(employees_ws)

    return True


def get_employees(include_inactive=False):
    ws = get_worksheet(EMPLOYEES_SHEET)
    records = records_from_worksheet(ws)
    employees = []
    for record in records:
        if not record.get("employee_id"):
            continue
        employee = {
            "employee_id": str(record.get("employee_id", "")).strip(),
            "full_name": str(record.get("ФИО", "")).strip(),
            "phone": str(record.get("Телефон", "")).strip(),
            "telegram_user_id": str(record.get("telegram_user_id", "")).strip(),
            "telegram_username": normalize_username(record.get("telegram_username", "")),
            "role": str(record.get("role", "warehouse_employee")).strip(),
            "roles": normalize_roles(record.get("roles"), record.get("role", "warehouse_employee")),
            "hourly_rate": safe_hourly_rate(record.get("hourly_rate")),
            "fixed_salary": safe_float(record.get("fixed_salary")),
            "include_in_common_fund": safe_bool(record.get("include_in_common_fund")),
            "is_active": safe_bool(record.get("is_active")),
        }
        if include_inactive or employee["is_active"]:
            employees.append(employee)
    return employees


def get_employee_by_id(employee_id):
    for employee in get_employees(include_inactive=True):
        if employee["employee_id"] == str(employee_id):
            return employee
    return None


def append_employee(
    full_name,
    phone="",
    telegram_user_id="",
    telegram_username="",
    role="warehouse_employee",
    roles=None,
    hourly_rate=0,
    fixed_salary=0,
    include_in_common_fund=True,
):
    ws = get_worksheet(EMPLOYEES_SHEET)
    employee_id = generate_id("emp")
    row = [
        employee_id,
        str(full_name).strip(),
        str(phone or "").strip(),
        str(telegram_user_id or "").strip(),
        normalize_username(telegram_username),
        primary_role(roles, str(role or "warehouse_employee").strip()),
        roles_to_storage(roles, role),
        str(hourly_rate or 0).replace(".", ","),
        str(fixed_salary or 0).replace(".", ","),
        str(bool(include_in_common_fund)).upper(),
        "TRUE",
    ]
    ws.append_row(row)
    return get_employee_by_id(employee_id)


def set_employee_active(employee_id, is_active):
    ws = get_worksheet(EMPLOYEES_SHEET)
    values = ws.get_all_values()
    employees_by_id, _ = rows_to_dict_by_key(values, "employee_id")
    found = employees_by_id.get(str(employee_id))
    if not found:
        return None

    row_index = found["row_index"]
    data = found["row_data"]
    data["is_active"] = str(bool(is_active)).upper()

    row = [data.get(header, "") for header in EMPLOYEE_HEADERS]
    end_col = column_letter(len(EMPLOYEE_HEADERS))
    ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    return get_employee_by_id(employee_id)


def update_employee_fields(employee_id, **fields):
    ws = get_worksheet(EMPLOYEES_SHEET)
    values = ws.get_all_values()
    employees_by_id, _ = rows_to_dict_by_key(values, "employee_id")
    found = employees_by_id.get(str(employee_id))
    if not found:
        return None

    field_headers = {
        "full_name": "ФИО",
        "phone": "Телефон",
        "telegram_user_id": "telegram_user_id",
        "telegram_username": "telegram_username",
        "role": "role",
        "roles": "roles",
        "hourly_rate": "hourly_rate",
        "fixed_salary": "fixed_salary",
        "include_in_common_fund": "include_in_common_fund",
        "is_active": "is_active",
    }

    row_index = found["row_index"]
    data = found["row_data"]

    for field_name, value in fields.items():
        header = field_headers.get(field_name)
        if not header:
            continue

        if field_name == "telegram_username":
            value = normalize_username(value)
        elif field_name == "roles":
            value = roles_to_storage(value, data.get("role"))
        elif field_name in {"hourly_rate", "fixed_salary"}:
            value = str(safe_float(value)).replace(".", ",")
        elif field_name in {"include_in_common_fund", "is_active"}:
            value = str(bool(value)).upper()
        else:
            value = str(value or "").strip()

        data[header] = value

    if "roles" in fields:
        data["role"] = primary_role(fields["roles"], data.get("role") or "warehouse_employee")
    elif "role" in fields:
        data["roles"] = roles_to_storage([fields["role"]], fields["role"])

    row = [data.get(header, "") for header in EMPLOYEE_HEADERS]
    end_col = column_letter(len(EMPLOYEE_HEADERS))
    ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    return get_employee_by_id(employee_id)


def find_employee_for_telegram_user(user):
    telegram_user_id = str(user.id)
    username = normalize_username(user.username)

    for employee in get_employees(include_inactive=True):
        if employee["telegram_user_id"] and employee["telegram_user_id"] == telegram_user_id:
            return employee

    # Временный fallback, пока telegram_user_id не заполнены.
    if username:
        for employee in get_employees(include_inactive=True):
            if employee["telegram_username"] == username:
                return employee

    return None


def is_manager(employee):
    return has_any_role(employee, MANAGER_ROLES)


def get_kpi_items(active_only=True):
    ws = get_worksheet(KPI_SHEET)
    records = records_from_worksheet(ws)
    items = []
    for record in records:
        if not record.get("kpi_id"):
            continue
        item = {
            "kpi_id": str(record.get("kpi_id", "")).strip(),
            "name": str(record.get("Название", "")).strip(),
            "rate": safe_float(record.get("Ставка")),
            "is_active": safe_bool(record.get("Активно")),
        }
        if active_only and not item["is_active"]:
            continue
        items.append(item)
    return items


def get_kpi_by_id(kpi_id):
    kpi_id = str(kpi_id or "").strip()
    return next(
        (item for item in get_kpi_items(active_only=False) if item["kpi_id"] == kpi_id),
        None,
    )


def _find_kpi_by_name(name, exclude_kpi_id=None):
    normalized_name = str(name or "").strip().casefold()
    if not normalized_name:
        return None

    for item in get_kpi_items(active_only=False):
        if exclude_kpi_id and item["kpi_id"] == str(exclude_kpi_id):
            continue
        if item["name"].casefold() == normalized_name:
            return item
    return None


def append_kpi(name, rate):
    name = str(name or "").strip()
    rate = safe_float(rate)
    if not name:
        raise ValueError("Название KPI не должно быть пустым.")
    if rate <= 0:
        raise ValueError("Ставка KPI должна быть больше нуля.")
    if _find_kpi_by_name(name):
        raise ValueError("KPI с таким названием уже существует.")

    kpi_id = generate_id("kpi")
    get_worksheet(KPI_SHEET).append_row([kpi_id, name, rate, "TRUE"])
    return get_kpi_by_id(kpi_id)


def update_kpi_fields(kpi_id, **fields):
    ws = get_worksheet(KPI_SHEET)
    values = ws.get_all_values()
    items_by_id, _ = rows_to_dict_by_key(values, "kpi_id")
    found = items_by_id.get(str(kpi_id))
    if not found:
        return None

    data = found["row_data"]
    if "name" in fields:
        name = str(fields["name"] or "").strip()
        if not name:
            raise ValueError("Название KPI не должно быть пустым.")
        if _find_kpi_by_name(name, exclude_kpi_id=kpi_id):
            raise ValueError("KPI с таким названием уже существует.")
        data["Название"] = name

    if "rate" in fields:
        rate = safe_float(fields["rate"])
        if rate <= 0:
            raise ValueError("Ставка KPI должна быть больше нуля.")
        data["Ставка"] = rate

    if "is_active" in fields:
        data["Активно"] = str(bool(fields["is_active"])).upper()

    row = [data.get(header, "") for header in KPI_HEADERS]
    end_col = column_letter(len(KPI_HEADERS))
    ws.update(f"A{found['row_index']}:{end_col}{found['row_index']}", [row])
    return get_kpi_by_id(kpi_id)


def set_kpi_active(kpi_id, is_active):
    return update_kpi_fields(kpi_id, is_active=is_active)


def find_report_row(employee_id, report_date):
    ws = get_worksheet(REPORTS_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return None, None

    headers = values[0]
    for index, row in enumerate(values[1:], start=2):
        row_data = dict(zip(headers, row))
        if row_data.get("employee_id") == employee_id and row_data.get("Дата") == report_date:
            return index, row_data
    return None, None


def report_exists(employee_id, report_date):
    row_index, _ = find_report_row(employee_id, report_date)
    return row_index is not None


def find_manager_report_row(employee_id, report_date):
    ws = get_worksheet(MANAGER_REPORTS_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return None, None
    headers = values[0]
    for index, row in enumerate(values[1:], start=2):
        row_data = dict(zip(headers, row))
        if row_data.get("employee_id") == str(employee_id) and row_data.get("Дата") == str(report_date):
            return index, row_data
    return None, None


def append_manager_report(employee, report_date, manager_report, telegram_messages=None):
    if find_manager_report_row(employee["employee_id"], report_date)[0] is not None:
        raise ValueError("Руководительский отчет за эту дату уже существует.")
    ws = get_worksheet(MANAGER_REPORTS_SHEET)
    created_at = now_str()
    report_id = generate_id("manager_report")
    ws.append_row(
        [
            report_id,
            report_date,
            employee["employee_id"],
            employee["full_name"],
            employee.get("telegram_user_id", ""),
            json.dumps(manager_report or {}, ensure_ascii=False),
            json.dumps(telegram_messages or [], ensure_ascii=False),
            created_at,
            created_at,
        ]
    )
    return report_id


def kpi_to_json(kpi_items):
    return json.dumps(kpi_items or [], ensure_ascii=False)


def kpi_from_json(value):
    try:
        if not value:
            return []
        return json.loads(value)
    except Exception:
        return []


def calculate_kpi_sum(kpi_items):
    total = 0.0
    for item in kpi_items or []:
        qty = safe_float(item.get("qty"))
        rate = safe_float(item.get("rate"))
        total += qty * rate
    return total


def build_kpi_daily_quantity_map(kpi_items):
    result = {column: 0.0 for column in KPI_DAILY_COLUMNS}

    for item in kpi_items or []:
        kpi_id = str(item.get("kpi_id", "")).strip()
        column = KPI_DAILY_COLUMN_BY_KPI_ID.get(kpi_id)

        if not column:
            continue

        result[column] += safe_float(item.get("qty"))

    return result


def find_kpi_daily_row(report_date, employee_full_name):
    ws = get_worksheet(KPI_DAILY_SHEET)
    values = ws.get_all_values()

    if len(values) <= 1:
        return None

    for index, row in enumerate(values[1:], start=2):
        row_date = row[0] if len(row) > 0 else ""
        row_name = row[1] if len(row) > 1 else ""

        if row_date == report_date and row_name == employee_full_name:
            return index

    return None


def calculate_daily_salary_total(
    employee,
    hours,
    kpi_items,
    payment_mode=PAYMENT_MODE_HOURLY,
    shift_type="",
):
    """Считает дневную сумму для листа «KPI за день».

    Формула:
    отработанные часы × ставка сотрудника + сумма KPI за день.

    Важно: в листе «KPI за день» колонка «Общее» — это не сумма
    количеств KPI, а именно дневная сумма в рублях.
    """
    hourly_rate = safe_hourly_rate(employee.get("hourly_rate", 0))
    hours_value = paid_hours_for_report(hours, payment_mode, shift_type)
    kpi_sum = calculate_kpi_sum(kpi_items)

    return hours_value * hourly_rate + kpi_sum


def upsert_daily_kpi_row(employee, report_date, hours, kpi_items, shift_type=""):
    ws = get_worksheet(KPI_DAILY_SHEET)
    quantity_map = build_kpi_daily_quantity_map(kpi_items)
    period = get_period_for_date(report_date)
    payment_mode = period.get("payment_mode") if period else PAYMENT_MODE_HOURLY
    daily_salary_total = calculate_daily_salary_total(
        employee,
        hours,
        kpi_items,
        payment_mode=payment_mode,
        shift_type=shift_type,
    )

    row = [
        report_date,
        employee["full_name"],
        safe_float(hours),
    ]

    for column in KPI_DAILY_COLUMNS:
        row.append(quantity_map[column])

    row.append(daily_salary_total)

    row_index = find_kpi_daily_row(report_date, employee["full_name"])

    if row_index:
        end_col = column_letter(len(KPI_DAILY_HEADERS))
        ws.update(f"A{row_index}:{end_col}{row_index}", [row])
    else:
        ws.append_row(row)


def append_daily_report(
    employee,
    report_date,
    interval,
    hours,
    tasks,
    kpi_items,
    telegram_data=None,
    shift_type="",
    lunch_hours=0,
):
    telegram_data = telegram_data or {}
    ws = get_worksheet(REPORTS_SHEET)
    report_id = generate_id("report")
    created_at = now_str()
    kpi_sum = calculate_kpi_sum(kpi_items)

    row = [
        report_id,
        report_date,
        employee["employee_id"],
        employee["full_name"],
        employee.get("telegram_user_id", ""),
        interval,
        hours,
        normalize_shift_type(shift_type),
        safe_float(lunch_hours),
        tasks,
        kpi_to_json(kpi_items),
        kpi_sum,
        telegram_data.get("chat_id", ""),
        telegram_data.get("thread_id", ""),
        telegram_data.get("message_id", ""),
        created_at,
        created_at,
    ]
    ws.append_row(row)
    upsert_daily_kpi_row(employee, report_date, hours, kpi_items, shift_type=shift_type)
    return report_id


def update_daily_report(row_index, report_data):
    ws = get_worksheet(REPORTS_SHEET)
    values = [[report_data.get(header, "") for header in REPORT_HEADERS]]
    end_col = column_letter(len(REPORT_HEADERS))
    ws.update(f"A{row_index}:{end_col}{row_index}", values)

    employee = get_employee_by_id(report_data.get("employee_id"))
    if employee:
        upsert_daily_kpi_row(
            employee=employee,
            report_date=report_data.get("Дата", ""),
            hours=safe_float(report_data.get("Отработано часов")),
            kpi_items=kpi_from_json(report_data.get("KPI данные", "")),
            shift_type=report_data.get("Тип смены", ""),
        )


def delete_daily_kpi_row(report_date, employee_full_name):
    row_index = find_kpi_daily_row(report_date, employee_full_name)
    if row_index:
        get_worksheet(KPI_DAILY_SHEET).delete_rows(row_index)
        return True
    return False


def delete_daily_report(row_index):
    _, report_data = find_report_by_row(row_index)
    if not report_data:
        return None
    get_worksheet(REPORTS_SHEET).delete_rows(row_index)
    delete_daily_kpi_row(report_data.get("Дата", ""), report_data.get("ФИО", ""))
    return report_data_to_model(report_data)


def update_report_message_ids(row_index, chat_id, thread_id, message_id):
    _, report_data = find_report_by_row(row_index)
    if not report_data:
        return
    report_data["telegram_chat_id"] = str(chat_id or "")
    report_data["telegram_thread_id"] = str(thread_id or "")
    report_data["telegram_message_id"] = str(message_id or "")
    report_data["Обновлено"] = now_str()
    update_daily_report(row_index, report_data)


def find_report_by_row(row_index):
    ws = get_worksheet(REPORTS_SHEET)
    values = ws.get_all_values()
    if row_index < 2 or row_index > len(values):
        return None, None
    headers = values[0]
    row_data = dict(zip(headers, values[row_index - 1]))
    return row_index, row_data


def report_data_to_model(report_data):
    employee = get_employee_by_id(report_data.get("employee_id")) or {
        "employee_id": report_data.get("employee_id", ""),
        "full_name": report_data.get("ФИО", ""),
        "telegram_user_id": report_data.get("telegram_user_id", ""),
    }
    report_date = report_data.get("Дата", "")
    period = get_period_for_date(report_date)
    return {
        "report_id": report_data.get("report_id", ""),
        "date": report_date,
        "employee": employee,
        "interval": report_data.get("Рабочий промежуток", ""),
        "hours": safe_float(report_data.get("Отработано часов")),
        "shift_type": normalize_shift_type(report_data.get("Тип смены", "")),
        "payment_mode": period.get("payment_mode") if period else PAYMENT_MODE_HOURLY,
        "lunch_hours": safe_float(report_data.get("Обед")),
        "tasks": report_data.get("Задачи", ""),
        "kpi_items": kpi_from_json(report_data.get("KPI данные", "")),
        "kpi_sum": safe_float(report_data.get("KPI сумма")),
        "telegram_chat_id": report_data.get("telegram_chat_id", ""),
        "telegram_thread_id": report_data.get("telegram_thread_id", ""),
        "telegram_message_id": report_data.get("telegram_message_id", ""),
        "created_at": report_data.get("Создано", ""),
        "updated_at": report_data.get("Обновлено", ""),
    }


def append_expense(employee, expense_date, comment, amount, created_by):
    ws = get_worksheet(EXPENSES_SHEET)
    ws.append_row([
        generate_id("expense"),
        expense_date,
        employee["employee_id"],
        employee["full_name"],
        comment,
        safe_float(amount),
        created_by,
        now_str(),
    ])


def expense_record_from_row(record):
    return {
        "expense_id": str(record.get("expense_id", "")),
        "employee_id": str(record.get("employee_id", "")),
        "full_name": str(record.get("ФИО", "")),
        "amount": safe_float(record.get("Сумма")),
        "comment": str(record.get("Комментарий", "")),
        "date": str(record.get("Дата", "")),
        "created_by": str(record.get("Создал", "")),
        "created_at": str(record.get("Создано", "")),
    }


def bonus_record_from_row(record):
    return {
        "bonus_id": str(record.get("bonus_id", "")),
        "employee_id": str(record.get("employee_id", "")),
        "full_name": str(record.get("ФИО", "")),
        "amount": safe_float(record.get("Сумма")),
        "comment": str(record.get("Комментарий", "")),
        "date": str(record.get("Дата", "")),
        "assigned_by": str(record.get("Назначил", "")),
        "created_at": str(record.get("Создано", "")),
    }


def append_bonus(employee, bonus_date, comment, amount, assigned_by):
    ws = get_worksheet(BONUSES_SHEET)
    bonus_id = generate_id("bonus")
    ws.append_row([
        bonus_id,
        bonus_date,
        employee["employee_id"],
        employee["full_name"],
        comment,
        safe_float(amount),
        assigned_by,
        now_str(),
    ])
    return bonus_id


def list_bonuses_in_period(start_date=None, end_date=None, employee_id=None, limit=None):
    ws = get_worksheet(BONUSES_SHEET)
    bonuses = []
    for record in records_from_worksheet(ws):
        if start_date and end_date and not date_in_range(record.get("Дата", ""), start_date, end_date):
            continue
        if employee_id and str(record.get("employee_id", "")) != str(employee_id):
            continue
        bonuses.append(bonus_record_from_row(record))

    def sort_key(item):
        try:
            return parse_date(item["date"])
        except ValueError:
            return datetime.min

    bonuses.sort(key=sort_key, reverse=True)
    if limit:
        return bonuses[:limit]
    return bonuses


def get_bonuses_in_period(start_date, end_date):
    return list_bonuses_in_period(start_date, end_date)


def delete_bonus(bonus_id):
    ws = get_worksheet(BONUSES_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return None

    headers = values[0]
    for row_index, row in enumerate(values[1:], start=2):
        data = dict(zip(headers, row))
        if str(data.get("bonus_id", "")) != str(bonus_id):
            continue
        bonus = bonus_record_from_row(data)
        ws.delete_rows(row_index)
        return bonus
    return None


def append_penalty(employee, penalty_date, penalty_category, penalty_type, comment, amount, created_by):
    ws = get_worksheet(PENALTIES_SHEET)
    ws.append_row([
        generate_id("penalty"),
        penalty_date,
        employee["employee_id"],
        employee["full_name"],
        penalty_category,
        penalty_type,
        comment,
        safe_float(amount),
        created_by,
        now_str(),
    ])


def get_reports_in_period(start_date, end_date):
    ws = get_worksheet(REPORTS_SHEET)
    reports = []
    for record in records_from_worksheet(ws):
        if date_in_range(record.get("Дата", ""), start_date, end_date):
            reports.append(report_data_to_model(record))
    return reports


def get_expenses_in_period(start_date, end_date):
    return list_expenses_in_period(start_date, end_date)


def list_expenses_in_period(start_date, end_date, employee_id=None):
    ws = get_worksheet(EXPENSES_SHEET)
    expenses = []
    for record in records_from_worksheet(ws):
        if not date_in_range(record.get("Дата", ""), start_date, end_date):
            continue
        if employee_id and str(record.get("employee_id", "")) != str(employee_id):
            continue
        expenses.append(expense_record_from_row(record))
    return expenses


def delete_expense(expense_id, employee_id=None):
    ws = get_worksheet(EXPENSES_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return None

    headers = values[0]
    for row_index, row in enumerate(values[1:], start=2):
        data = dict(zip(headers, row))
        if str(data.get("expense_id", "")) != str(expense_id):
            continue
        if employee_id and str(data.get("employee_id", "")) != str(employee_id):
            return None
        expense = expense_record_from_row(data)
        ws.delete_rows(row_index)
        return expense
    return None


def get_penalties_in_period(start_date, end_date):
    ws = get_worksheet(PENALTIES_SHEET)
    penalties = []
    for record in records_from_worksheet(ws):
        if date_in_range(record.get("Дата", ""), start_date, end_date):
            penalties.append({
                "employee_id": str(record.get("employee_id", "")),
                "amount": safe_float(record.get("Сумма")),
                "penalty_category": str(record.get("Категория штрафа", "")),
                "penalty_type": str(record.get("Тип штрафа", "")),
                "comment": str(record.get("Комментарий", "")),
                "date": str(record.get("Дата", "")),
            })
    return penalties


def count_employee_penalties_by_type(employee_id, penalty_type, start_date, end_date):
    count = 0
    for penalty in get_penalties_in_period(start_date, end_date):
        if str(penalty.get("employee_id")) != str(employee_id):
            continue
        if str(penalty.get("penalty_type")) != str(penalty_type):
            continue
        count += 1
    return count


def get_periods():
    ws = get_worksheet(PERIODS_SHEET)
    periods = []
    for record in records_from_worksheet(ws):
        if not record.get("period_id"):
            continue
        periods.append({
            "period_id": str(record.get("period_id", "")),
            "name": str(record.get("Название", "")),
            "start_date": str(record.get("Дата начала", "")),
            "end_date": str(record.get("Дата конца", "")),
            "payment_mode": normalize_payment_mode(record.get("Режим оплаты")),
            "status": str(record.get("Статус", "")),
            "created_by": str(record.get("Создал", "")),
            "created_at": str(record.get("Создано", "")),
            "updated_at": str(record.get("Обновлено", "")),
        })
    return periods


def get_period_for_date(report_date):
    """Возвращает последний созданный период, включающий дату отчета."""
    for period in reversed(get_periods()):
        if date_in_range(report_date, period["start_date"], period["end_date"]):
            return period
    return None


def find_active_period_row():
    ws = get_worksheet(PERIODS_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return None, None
    headers = values[0]
    for index, row in enumerate(values[1:], start=2):
        data = dict(zip(headers, row))
        if str(data.get("Статус", "")).lower() == "active":
            return index, data
    return None, None


def get_active_period():
    _, data = find_active_period_row()
    if not data:
        return None
    return {
        "period_id": data.get("period_id", ""),
        "name": data.get("Название", ""),
        "start_date": data.get("Дата начала", ""),
        "end_date": data.get("Дата конца", ""),
        "payment_mode": normalize_payment_mode(data.get("Режим оплаты")),
        "status": data.get("Статус", ""),
        "created_by": data.get("Создал", ""),
        "created_at": data.get("Создано", ""),
        "updated_at": data.get("Обновлено", ""),
    }


def close_active_periods():
    ws = get_worksheet(PERIODS_SHEET)
    values = ws.get_all_values()
    if len(values) <= 1:
        return
    headers = values[0]
    for index, row in enumerate(values[1:], start=2):
        data = dict(zip(headers, row))
        if str(data.get("Статус", "")).lower() == "active":
            data["Статус"] = "closed"
            data["Обновлено"] = now_str()
            ws.update(f"A{index}:I{index}", [[data.get(header, "") for header in PERIOD_HEADERS]])


def create_active_period(name, start_date, end_date, created_by, payment_mode=PAYMENT_MODE_HOURLY):
    close_active_periods()
    ws = get_worksheet(PERIODS_SHEET)
    created_at = now_str()
    period_id = generate_id("period")
    ws.append_row([
        period_id,
        name,
        start_date,
        end_date,
        normalize_payment_mode(payment_mode),
        "active",
        created_by,
        created_at,
        created_at,
    ])
    return period_id


def update_active_period(name=None, start_date=None, end_date=None, payment_mode=None):
    ws = get_worksheet(PERIODS_SHEET)
    row_index, data = find_active_period_row()
    if not data:
        return False
    if name is not None:
        data["Название"] = name
    if start_date is not None:
        data["Дата начала"] = start_date
    if end_date is not None:
        data["Дата конца"] = end_date
    if payment_mode is not None:
        data["Режим оплаты"] = normalize_payment_mode(payment_mode)
    data["Обновлено"] = now_str()
    ws.update(f"A{row_index}:I{row_index}", [[data.get(header, "") for header in PERIOD_HEADERS]])
    return True


def cleanup_old_operational_data(days=365):
    cutoff = datetime.now() - timedelta(days=days)
    result = {}

    for title, date_header in [
        (REPORTS_SHEET, "Дата"),
        (EXPENSES_SHEET, "Дата"),
        (PENALTIES_SHEET, "Дата"),
        (BONUSES_SHEET, "Дата"),
        (ADDITIONAL_PAY_SHEET, "Дата начисления"),
    ]:
        ws = get_worksheet(title)
        values = ws.get_all_values()
        if len(values) <= 1:
            result[title] = 0
            continue

        headers = values[0]
        rows_to_delete = []
        for index, row in enumerate(values[1:], start=2):
            data = dict(zip(headers, row))
            try:
                row_date = parse_date(data.get(date_header, ""))
            except ValueError:
                continue
            if row_date < cutoff:
                rows_to_delete.append(index)

        # Удаляем снизу вверх, чтобы индексы не съезжали.
        for row_index in reversed(rows_to_delete):
            ws.delete_rows(row_index)

        result[title] = len(rows_to_delete)

    return result
