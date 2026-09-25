"""Справочник водителей, начисления и списания водителям."""

from modules.payroll.google_sheets import (
    DRIVER_HEADERS,
    DRIVER_PAYMENT_HEADERS,
    DRIVER_WRITE_OFF_HEADERS,
    DRIVERS_SHEET,
    DRIVER_PAYMENTS_SHEET,
    DRIVER_WRITE_OFFS_SHEET,
    generate_id,
    get_worksheet,
    now_str,
    parse_date,
    records_from_worksheet,
    safe_bool,
    safe_float,
)


class DriverValidationError(ValueError):
    pass


def get_drivers(include_inactive=False):
    result = []
    for record in records_from_worksheet(get_worksheet(DRIVERS_SHEET)):
        if not record.get("driver_id"):
            continue
        driver = {
            "driver_id": str(record.get("driver_id", "")).strip(),
            "full_name": str(record.get("ФИО", "")).strip(),
            "phone": str(record.get("Телефон", "")).strip(),
            "vehicle_number": str(record.get("Номер машины", "")).strip(),
            "comment": str(record.get("Комментарий", "")).strip(),
            "is_active": safe_bool(record.get("Активен")),
            "created_by": str(record.get("Создал", "")).strip(),
            "created_at": str(record.get("Создано", "")).strip(),
        }
        if include_inactive or driver["is_active"]:
            result.append(driver)
    return result


def get_driver(driver_id):
    return next((item for item in get_drivers(True) if item["driver_id"] == str(driver_id)), None)


def create_driver(full_name, phone="", vehicle_number="", comment="", created_by=""):
    full_name = str(full_name or "").strip()
    if len(full_name) < 2:
        raise DriverValidationError("Укажите имя водителя.")
    duplicate = next((d for d in get_drivers(True) if d["full_name"].casefold() == full_name.casefold()), None)
    if duplicate:
        if duplicate["is_active"]:
            return duplicate
        raise DriverValidationError("Водитель с таким именем уже есть в архиве.")
    driver = {
        "driver_id": generate_id("driver"),
        "full_name": full_name,
        "phone": str(phone or "").strip(),
        "vehicle_number": str(vehicle_number or "").strip().upper(),
        "comment": str(comment or "").strip(),
        "is_active": True,
        "created_by": str(created_by or "").strip(),
        "created_at": now_str(),
    }
    get_worksheet(DRIVERS_SHEET).append_row([
        driver["driver_id"], driver["full_name"], driver["phone"], driver["vehicle_number"], driver["comment"],
        "TRUE", driver["created_by"], driver["created_at"],
    ])
    return driver


def add_driver_payment(driver, payment_date, amount, comment="", created_by=""):
    try:
        parse_date(payment_date)
    except ValueError as error:
        raise DriverValidationError("Дата должна быть в формате ДД.ММ.ГГГГ.") from error
    amount = safe_float(amount)
    if amount <= 0:
        raise DriverValidationError("Сумма оплаты должна быть больше нуля.")
    if not driver or not driver.get("driver_id"):
        raise DriverValidationError("Водитель не найден.")
    item = {
        "driver_payment_id": generate_id("driverpay"),
        "date": str(payment_date).strip(),
        "driver_id": driver["driver_id"],
        "driver_name": driver["full_name"],
        "vehicle_number": str(driver.get("vehicle_number", "")).strip(),
        "amount": amount,
        "comment": str(comment or "").strip(),
        "created_by": str(created_by or "").strip(),
        "created_at": now_str(),
    }
    get_worksheet(DRIVER_PAYMENTS_SHEET).append_row([
        item["driver_payment_id"], item["date"], item["driver_id"], item["driver_name"], item["vehicle_number"],
        item["amount"], item["comment"], item["created_by"], item["created_at"],
    ])
    return item


def get_driver_payments(start_date=None, end_date=None):
    result = []
    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None
    for record in records_from_worksheet(get_worksheet(DRIVER_PAYMENTS_SHEET)):
        if not record.get("driver_payment_id"):
            continue
        try:
            date = parse_date(record.get("Дата", ""))
        except ValueError:
            continue
        if start and date < start or end and date > end:
            continue
        result.append({
            "driver_payment_id": str(record.get("driver_payment_id", "")),
            "date": str(record.get("Дата", "")),
            "driver_id": str(record.get("driver_id", "")),
            "driver_name": str(record.get("ФИО водителя", "")),
            "vehicle_number": str(record.get("Номер машины", "")),
            "amount": safe_float(record.get("Сумма")),
            "comment": str(record.get("Комментарий", "")),
            "created_by": str(record.get("Создал", "")),
            "created_at": str(record.get("Создано", "")),
        })
    return result


def delete_driver_payment(payment_id):
    worksheet = get_worksheet(DRIVER_PAYMENTS_SHEET)
    for row_index, record in enumerate(records_from_worksheet(worksheet), start=2):
        if str(record.get("driver_payment_id", "")) == str(payment_id):
            worksheet.delete_rows(row_index)
            return True
    return False


def add_driver_write_off(driver, write_off_date, amount, comment="", created_by=""):
    """Записать уже выданную водителю сумму, уменьшающую выплату за период."""
    try:
        parse_date(write_off_date)
    except ValueError as error:
        raise DriverValidationError("Дата должна быть в формате ДД.ММ.ГГГГ.") from error
    amount = safe_float(amount)
    if amount <= 0:
        raise DriverValidationError("Сумма списания должна быть больше нуля.")
    if not driver or not driver.get("driver_id"):
        raise DriverValidationError("Водитель не найден.")
    item = {
        "driver_write_off_id": generate_id("driverwoff"),
        "date": str(write_off_date).strip(),
        "driver_id": driver["driver_id"],
        "driver_name": driver["full_name"],
        "vehicle_number": str(driver.get("vehicle_number", "")).strip(),
        "amount": amount,
        "comment": str(comment or "").strip(),
        "created_by": str(created_by or "").strip(),
        "created_at": now_str(),
    }
    get_worksheet(DRIVER_WRITE_OFFS_SHEET).append_row([
        item["driver_write_off_id"], item["date"], item["driver_id"], item["driver_name"],
        item["vehicle_number"], item["amount"], item["comment"], item["created_by"], item["created_at"],
    ])
    return item


def get_driver_write_offs(start_date=None, end_date=None):
    result = []
    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None
    for record in records_from_worksheet(get_worksheet(DRIVER_WRITE_OFFS_SHEET)):
        if not record.get("driver_write_off_id"):
            continue
        try:
            date = parse_date(record.get("Дата", ""))
        except ValueError:
            continue
        if start and date < start or end and date > end:
            continue
        result.append({
            "driver_write_off_id": str(record.get("driver_write_off_id", "")),
            "date": str(record.get("Дата", "")),
            "driver_id": str(record.get("driver_id", "")),
            "driver_name": str(record.get("ФИО водителя", "")),
            "vehicle_number": str(record.get("Номер машины", "")),
            "amount": safe_float(record.get("Сумма")),
            "comment": str(record.get("Комментарий", "")),
            "created_by": str(record.get("Создал", "")),
            "created_at": str(record.get("Создано", "")),
        })
    return result


def delete_driver_write_off(write_off_id):
    worksheet = get_worksheet(DRIVER_WRITE_OFFS_SHEET)
    for row_index, record in enumerate(records_from_worksheet(worksheet), start=2):
        if str(record.get("driver_write_off_id", "")) == str(write_off_id):
            worksheet.delete_rows(row_index)
            return True
    return False
