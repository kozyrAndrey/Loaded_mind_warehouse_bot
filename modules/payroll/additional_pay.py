import uuid
from datetime import date, datetime, timedelta

from modules.employees.roles import has_any_role
from modules.storage.google_archive import DatabaseWorksheet


ADDITIONAL_PAY_SHEET = "Дополнительные начисления"
TREND_ISLAND_POSITION_TYPE = "trend_island"
TREND_ISLAND_POSITION_NAME = "Trend Island"
TREND_ISLAND_UNIT_RATE = 2000.0
TREND_ISLAND_WEEKLY_RATE = 1500.0
ADDITIONAL_PAY_MANAGER_ROLES = {"brand_manager", "admin"}

ADDITIONAL_PAY_HEADERS = [
    "additional_pay_id",
    "Тип позиции",
    "Название позиции",
    "Дата начисления",
    "employee_id",
    "ФИО",
    "Неделя начала",
    "Неделя конца",
    "Количество",
    "Ставка за единицу",
    "Недельная ставка",
    "Начислено до штрафа",
    "Есть ошибки",
    "Описание ошибок",
    "Штраф за ошибки",
    "Итого",
    "Комментарий",
    "Назначил",
    "Создано",
    "Обновлено",
]


class AdditionalPayValidationError(ValueError):
    pass


def get_worksheet():
    return DatabaseWorksheet(
        "payroll",
        "payroll",
        ADDITIONAL_PAY_SHEET,
        ADDITIONAL_PAY_HEADERS,
    )


def now_str():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def date_to_str(value):
    if isinstance(value, str):
        return value
    return value.strftime("%d.%m.%Y")


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip(), "%d.%m.%Y").date()


def safe_float(value):
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def safe_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "да", "истина"}


def can_manage_additional_pay(employee):
    return has_any_role(employee, ADDITIONAL_PAY_MANAGER_ROLES)


def previous_completed_week(base_date=None):
    current = parse_date(base_date or date.today())
    current_monday = current - timedelta(days=current.weekday())
    week_start = current_monday - timedelta(days=7)
    return week_start, week_start + timedelta(days=6)


def recent_completed_weeks(base_date=None, count=8):
    week_start, _ = previous_completed_week(base_date)
    return [
        (
            week_start - timedelta(days=7 * offset),
            week_start - timedelta(days=7 * offset) + timedelta(days=6),
        )
        for offset in range(max(1, int(count)))
    ]


def calculate_trend_island_pay(quantity, error_penalty=0):
    quantity_value = safe_float(quantity)
    if quantity_value < 0 or not quantity_value.is_integer():
        raise AdditionalPayValidationError("Количество поставок должно быть целым числом от 0.")

    penalty_value = safe_float(error_penalty)
    if penalty_value < 0:
        raise AdditionalPayValidationError("Штраф не может быть отрицательным.")

    quantity_value = int(quantity_value)
    gross_amount = quantity_value * TREND_ISLAND_UNIT_RATE
    if quantity_value:
        gross_amount += TREND_ISLAND_WEEKLY_RATE

    if penalty_value > gross_amount:
        raise AdditionalPayValidationError(
            "Штраф за ошибки не может превышать начисление за неделю."
        )

    return {
        "quantity": quantity_value,
        "unit_rate": TREND_ISLAND_UNIT_RATE,
        "weekly_rate": TREND_ISLAND_WEEKLY_RATE if quantity_value else 0.0,
        "gross_amount": gross_amount,
        "error_penalty": penalty_value,
        "total_amount": gross_amount - penalty_value,
    }


def _record_from_row(record):
    return {
        "additional_pay_id": str(record.get("additional_pay_id", "")).strip(),
        "position_type": str(record.get("Тип позиции", "")).strip(),
        "position_name": str(record.get("Название позиции", "")).strip(),
        "accrual_date": str(record.get("Дата начисления", "")).strip(),
        "employee_id": str(record.get("employee_id", "")).strip(),
        "full_name": str(record.get("ФИО", "")).strip(),
        "week_start": str(record.get("Неделя начала", "")).strip(),
        "week_end": str(record.get("Неделя конца", "")).strip(),
        "quantity": int(safe_float(record.get("Количество"))),
        "unit_rate": safe_float(record.get("Ставка за единицу")),
        "weekly_rate": safe_float(record.get("Недельная ставка")),
        "gross_amount": safe_float(record.get("Начислено до штрафа")),
        "has_errors": safe_bool(record.get("Есть ошибки")),
        "error_comment": str(record.get("Описание ошибок", "")).strip(),
        "error_penalty": safe_float(record.get("Штраф за ошибки")),
        "total_amount": safe_float(record.get("Итого")),
        "comment": str(record.get("Комментарий", "")).strip(),
        "assigned_by": str(record.get("Назначил", "")).strip(),
        "created_at": str(record.get("Создано", "")).strip(),
        "updated_at": str(record.get("Обновлено", "")).strip(),
    }


def list_additional_payments(
    start_date=None,
    end_date=None,
    employee_id=None,
    position_type=None,
    limit=None,
):
    items = []
    for record in get_worksheet().get_all_records(numericise_ignore=[1]):
        item = _record_from_row(record)
        if not item["additional_pay_id"]:
            continue
        if employee_id and item["employee_id"] != str(employee_id):
            continue
        if position_type and item["position_type"] != str(position_type):
            continue
        if start_date and end_date:
            accrual_date = parse_date(item["accrual_date"])
            if not parse_date(start_date) <= accrual_date <= parse_date(end_date):
                continue
        items.append(item)

    items.sort(
        key=lambda item: (parse_date(item["accrual_date"]), item["created_at"]),
        reverse=True,
    )
    return items[: int(limit)] if limit else items


def get_additional_payment(additional_pay_id):
    return next(
        (
            item
            for item in list_additional_payments()
            if item["additional_pay_id"] == str(additional_pay_id)
        ),
        None,
    )


def find_trend_island_payment(employee_id, week_start, exclude_id=None):
    week_start = date_to_str(parse_date(week_start))
    return next(
        (
            item
            for item in list_additional_payments(
                employee_id=employee_id,
                position_type=TREND_ISLAND_POSITION_TYPE,
            )
            if item["week_start"] == week_start
            and item["additional_pay_id"] != str(exclude_id or "")
        ),
        None,
    )


def _validated_trend_data(
    employee,
    week_start,
    quantity,
    has_errors,
    error_comment,
    error_penalty,
    comment,
):
    if not employee or not employee.get("employee_id"):
        raise AdditionalPayValidationError("Руководитель склада не найден.")

    week_start_value = parse_date(week_start)
    if week_start_value.weekday() != 0:
        raise AdditionalPayValidationError("Неделя должна начинаться в понедельник.")
    week_end_value = week_start_value + timedelta(days=6)

    calculation = calculate_trend_island_pay(quantity, error_penalty)
    has_errors = bool(has_errors)
    error_comment = str(error_comment or "").strip()
    comment = str(comment or "").strip()

    if calculation["quantity"] == 0 and has_errors:
        raise AdditionalPayValidationError("При нуле поставок нельзя указать ошибки.")
    if has_errors and not error_comment:
        raise AdditionalPayValidationError("Опишите обнаруженные ошибки.")
    if has_errors and calculation["error_penalty"] <= 0:
        raise AdditionalPayValidationError("Для ошибки укажите штраф больше 0 ₽.")
    if not has_errors and calculation["error_penalty"]:
        raise AdditionalPayValidationError("Штраф можно указать только при наличии ошибок.")

    return {
        **calculation,
        "week_start": date_to_str(week_start_value),
        "week_end": date_to_str(week_end_value),
        "accrual_date": date_to_str(week_end_value),
        "has_errors": has_errors,
        "error_comment": error_comment if has_errors else "",
        "comment": comment,
    }


def append_trend_island_payment(
    employee,
    week_start,
    quantity,
    has_errors,
    error_comment,
    error_penalty,
    comment,
    assigned_by,
):
    data = _validated_trend_data(
        employee,
        week_start,
        quantity,
        has_errors,
        error_comment,
        error_penalty,
        comment,
    )
    if find_trend_island_payment(employee["employee_id"], data["week_start"]):
        raise AdditionalPayValidationError(
            "Начисление Trend Island за эту неделю уже существует. Измените существующую запись."
        )

    additional_pay_id = f"addpay_{uuid.uuid4().hex[:10]}"
    timestamp = now_str()
    row = [
        additional_pay_id,
        TREND_ISLAND_POSITION_TYPE,
        TREND_ISLAND_POSITION_NAME,
        data["accrual_date"],
        employee["employee_id"],
        employee["full_name"],
        data["week_start"],
        data["week_end"],
        data["quantity"],
        data["unit_rate"],
        data["weekly_rate"],
        data["gross_amount"],
        str(data["has_errors"]).upper(),
        data["error_comment"],
        data["error_penalty"],
        data["total_amount"],
        data["comment"],
        str(assigned_by or "").strip(),
        timestamp,
        timestamp,
    ]
    get_worksheet().append_row(row)
    return get_additional_payment(additional_pay_id)


def update_trend_island_payment(
    additional_pay_id,
    employee,
    week_start,
    quantity,
    has_errors,
    error_comment,
    error_penalty,
    comment,
    assigned_by,
):
    worksheet = get_worksheet()
    values = worksheet.get_all_values()
    if len(values) <= 1:
        return None

    headers = values[0]
    for row_index, row in enumerate(values[1:], start=2):
        record = dict(zip(headers, row))
        if str(record.get("additional_pay_id", "")) != str(additional_pay_id):
            continue

        existing = _record_from_row(record)
        data = _validated_trend_data(
            employee,
            week_start,
            quantity,
            has_errors,
            error_comment,
            error_penalty,
            comment,
        )
        if find_trend_island_payment(
            employee["employee_id"],
            data["week_start"],
            exclude_id=additional_pay_id,
        ):
            raise AdditionalPayValidationError(
                "Начисление Trend Island за эту неделю уже существует."
            )

        updated = [
            additional_pay_id,
            TREND_ISLAND_POSITION_TYPE,
            TREND_ISLAND_POSITION_NAME,
            data["accrual_date"],
            employee["employee_id"],
            employee["full_name"],
            data["week_start"],
            data["week_end"],
            data["quantity"],
            data["unit_rate"],
            data["weekly_rate"],
            data["gross_amount"],
            str(data["has_errors"]).upper(),
            data["error_comment"],
            data["error_penalty"],
            data["total_amount"],
            data["comment"],
            str(assigned_by or "").strip(),
            existing["created_at"] or now_str(),
            now_str(),
        ]
        worksheet.update(f"A{row_index}:T{row_index}", [updated])
        return get_additional_payment(additional_pay_id)
    return None


def delete_additional_payment(additional_pay_id):
    worksheet = get_worksheet()
    values = worksheet.get_all_values()
    if len(values) <= 1:
        return None

    headers = values[0]
    for row_index, row in enumerate(values[1:], start=2):
        record = dict(zip(headers, row))
        if str(record.get("additional_pay_id", "")) != str(additional_pay_id):
            continue
        item = _record_from_row(record)
        worksheet.delete_rows(row_index)
        return item
    return None


def get_additional_payments_in_period(start_date, end_date):
    return list_additional_payments(start_date=start_date, end_date=end_date)
