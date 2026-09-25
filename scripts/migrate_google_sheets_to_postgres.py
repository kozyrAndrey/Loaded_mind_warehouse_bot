import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import gspread
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    GOOGLE_CREDENTIALS_PATH,
    GOOGLE_SHEET_ID,
    GOOGLE_WORKSHEET_NAME,
    OPERATIONS_GOOGLE_SHEET_ID,
    PAYROLL_GOOGLE_SHEET_ID,
)
from modules.payroll.google_sheets import (
    ADDITIONAL_PAY_SHEET,
    BONUSES_SHEET,
    EMPLOYEES_SHEET,
    EXPENSES_SHEET,
    KPI_DAILY_SHEET,
    KPI_SHEET,
    PENALTIES_SHEET,
    PERIODS_SHEET,
    REPORTS_SHEET,
)
from modules.receiving.google_sheets import (
    EXPORT_CHAT_ID_INDEX,
    EXPORT_DATE_INDEX,
    EXPORT_ID_INDEX,
    EXPORT_MESSAGE_IDS_INDEX,
    EXPORT_THREAD_ID_INDEX,
    EXPORT_USER_INDEX,
    normalize_sheet_date,
    pad_row,
    row_is_exported,
    safe_int,
)
from modules.receiving.postgres_storage import IncomingGood, init_receiving_storage
from modules.receiving.products import CATEGORIES
from modules.schedule.config import (
    SCHEDULE_ARCHIVE_SHEET,
    SCHEDULE_DUTIES_SHEET,
    SCHEDULE_EXPORTS_SHEET,
)
from modules.storage.google_archive import replace_sheet_archive
from modules.storage.postgres import session_scope


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def with_retries(label, func, attempts=5, delay=2):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as error:
            last_error = error
            if attempt == attempts:
                break

            logging.warning(
                "%s failed on attempt %s/%s: %s. Retrying in %ss",
                label,
                attempt,
                attempts,
                error,
                delay,
            )
            time.sleep(delay)

    raise last_error


def parse_datetime(value):
    value = str(value or "").strip()
    if not value:
        return None

    for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                return parsed.replace(hour=0, minute=0, second=0)
            return parsed
        except ValueError:
            pass

    return None


def parse_date(value):
    normalized = normalize_sheet_date(value)
    return datetime.strptime(normalized, "%d.%m.%Y").date()


def spreadsheet_client():
    return gspread.service_account(filename=GOOGLE_CREDENTIALS_PATH)


def worksheet_rows(spreadsheet, sheet_name):
    worksheet = with_retries(f"open worksheet {sheet_name}", lambda: spreadsheet.worksheet(sheet_name))
    values = with_retries(f"read worksheet {sheet_name}", worksheet.get_all_values)

    if not values:
        return [], []

    headers = values[0]
    rows = []

    for row_number, row in enumerate(values[1:], start=2):
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        data = {
            headers[index] or f"column_{index + 1}": padded[index]
            for index in range(len(headers))
        }
        rows.append((row_number, data))

    return headers, rows


def archive_sheet(source, spreadsheet_id, spreadsheet, sheet_name):
    try:
        _, rows = worksheet_rows(spreadsheet, sheet_name)
    except gspread.WorksheetNotFound:
        logging.warning("%s: лист %s не найден, пропускаю", source, sheet_name)
        return 0

    replace_sheet_archive(source, spreadsheet_id, sheet_name, rows)
    logging.info("%s:%s archived rows=%s", source, sheet_name, len(rows))
    return len(rows)


def infer_product_ids(category_name, product_name):
    category_name = str(category_name or "").strip()
    product_name = str(product_name or "").strip()

    for category_id, category in CATEGORIES.items():
        if category.get("name") != category_name:
            continue

        for product_id, known_product_name in category.get("products", {}).items():
            if known_product_name == product_name:
                return category_id, product_id

        return category_id, f"imported:{product_name}"

    return "imported", f"imported:{product_name}"


def receiving_duplicate_exists(session, record_date, user_id, username, product_name, size, packed, defective, rework):
    statement = (
        select(IncomingGood.id)
        .where(
            IncomingGood.record_date == record_date,
            IncomingGood.user_id == user_id,
            IncomingGood.username == username,
            IncomingGood.product_name == product_name,
            IncomingGood.size == size,
            IncomingGood.packed == packed,
            IncomingGood.defective == defective,
            IncomingGood.rework == rework,
        )
        .limit(1)
    )
    return session.execute(statement).first() is not None


def import_receiving_structured(spreadsheet):
    init_receiving_storage()
    worksheet = with_retries(
        f"open worksheet {GOOGLE_WORKSHEET_NAME}",
        lambda: spreadsheet.worksheet(GOOGLE_WORKSHEET_NAME),
    )
    values = with_retries(f"read worksheet {GOOGLE_WORKSHEET_NAME}", worksheet.get_all_values)

    if len(values) <= 1:
        return {"inserted": 0, "skipped": 0}

    inserted = 0
    skipped = 0

    with session_scope() as session:
        for row in values[1:]:
            if len(row) < 9:
                skipped += 1
                continue

            row = pad_row(row)
            category_name = str(row[3]).strip()
            product_name = str(row[4]).strip()
            size = str(row[5]).strip()

            if not category_name or not product_name or not size:
                skipped += 1
                continue

            try:
                record_date = parse_date(row[0])
            except ValueError:
                skipped += 1
                continue

            user_id = None
            if str(row[1]).strip():
                try:
                    user_id = int(str(row[1]).strip())
                except ValueError:
                    user_id = None

            username = str(row[2]).strip() or None
            packed = safe_int(row[6])
            defective = safe_int(row[7])
            rework = safe_int(row[8])

            if receiving_duplicate_exists(
                session,
                record_date,
                user_id,
                username,
                product_name,
                size,
                packed,
                defective,
                rework,
            ):
                skipped += 1
                continue

            category_id, product_id = infer_product_ids(category_name, product_name)
            exported_at = parse_datetime(row[EXPORT_DATE_INDEX])

            session.add(
                IncomingGood(
                    created_at=parse_datetime(row[0]) or datetime.combine(record_date, datetime.min.time()),
                    record_date=record_date,
                    user_id=user_id,
                    username=username,
                    category_id=category_id,
                    category_name=category_name,
                    product_id=product_id,
                    product_name=product_name,
                    size=size,
                    packed=packed,
                    defective=defective,
                    rework=rework,
                    exported=row_is_exported(row),
                    exported_at=exported_at,
                    exported_by=str(row[EXPORT_USER_INDEX]).strip() or None,
                    export_id=str(row[EXPORT_ID_INDEX]).strip() or None,
                    export_chat_id=str(row[EXPORT_CHAT_ID_INDEX]).strip() or None,
                    export_thread_id=str(row[EXPORT_THREAD_ID_INDEX]).strip() or None,
                    export_message_ids=str(row[EXPORT_MESSAGE_IDS_INDEX]).strip() or None,
                )
            )
            inserted += 1

    return {"inserted": inserted, "skipped": skipped}


def migrate_receiving(gc):
    if not GOOGLE_SHEET_ID:
        logging.warning("GOOGLE_SHEET_ID пустой, приемку пропускаю")
        return

    spreadsheet = with_retries("open receiving spreadsheet", lambda: gc.open_by_key(GOOGLE_SHEET_ID))
    archive_sheet("receiving", GOOGLE_SHEET_ID, spreadsheet, GOOGLE_WORKSHEET_NAME)
    result = import_receiving_structured(spreadsheet)
    logging.info("receiving structured inserted=%s skipped=%s", result["inserted"], result["skipped"])


def migrate_payroll(gc):
    if not PAYROLL_GOOGLE_SHEET_ID:
        logging.warning("PAYROLL_GOOGLE_SHEET_ID пустой, ЗП пропускаю")
        return

    spreadsheet = with_retries("open payroll spreadsheet", lambda: gc.open_by_key(PAYROLL_GOOGLE_SHEET_ID))
    for sheet_name in (
        EMPLOYEES_SHEET,
        REPORTS_SHEET,
        EXPENSES_SHEET,
        PENALTIES_SHEET,
        BONUSES_SHEET,
        KPI_SHEET,
        PERIODS_SHEET,
        KPI_DAILY_SHEET,
        ADDITIONAL_PAY_SHEET,
    ):
        archive_sheet("payroll", PAYROLL_GOOGLE_SHEET_ID, spreadsheet, sheet_name)


def migrate_operations(gc):
    if not OPERATIONS_GOOGLE_SHEET_ID:
        logging.warning("OPERATIONS_GOOGLE_SHEET_ID пустой, операции пропускаю")
        return

    spreadsheet = with_retries("open operations spreadsheet", lambda: gc.open_by_key(OPERATIONS_GOOGLE_SHEET_ID))
    for sheet_name in (
        SCHEDULE_ARCHIVE_SHEET,
        SCHEDULE_DUTIES_SHEET,
        SCHEDULE_EXPORTS_SHEET,
    ):
        archive_sheet("operations", OPERATIONS_GOOGLE_SHEET_ID, spreadsheet, sheet_name)


def main():
    parser = argparse.ArgumentParser(description="Migrate Google Sheets data to PostgreSQL.")
    parser.add_argument(
        "--only",
        choices=("all", "receiving", "payroll", "operations"),
        default="all",
        help="Which data group to migrate.",
    )
    args = parser.parse_args()

    gc = spreadsheet_client()

    if args.only in ("all", "receiving"):
        migrate_receiving(gc)
    if args.only in ("all", "payroll"):
        migrate_payroll(gc)
    if args.only in ("all", "operations"):
        migrate_operations(gc)


if __name__ == "__main__":
    main()
