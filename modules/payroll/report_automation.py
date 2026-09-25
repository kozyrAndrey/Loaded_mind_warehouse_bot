"""Daily report quantities shared by manager suggestions and the brand summary."""

import json
from decimal import Decimal

from modules.payroll.google_sheets import (
    MANAGER_REPORTS_SHEET, REPORTS_SHEET, get_worksheet, records_from_worksheet,
)
from modules.employees.roles import has_role
from modules.schedule.config import parse_date
from modules.tasks.config import TASK_STATUS_DONE, TASK_TYPE_WAREHOUSE
from modules.tasks.storage import (
    get_task_export, get_tasks_by_date, get_working_employees_for_date,
)


PACKAGING_KPI_IDS = {f"kpi{number:03d}" for number in range(1, 7)}
VOLUME_KPI_IDS = {
    "sent_orders": "kpi007",
    "stock_shipments": "kpi008",
    "posted_returns": "kpi011",
}


def quantity(value):
    result = Decimal(str(value or 0).replace(",", "."))
    if not result.is_finite() or result < 0:
        raise ValueError("Количество KPI должно быть конечным неотрицательным числом.")
    return result


def quantity_text(value):
    text = format(quantity(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def reports_for_date(report_date):
    # Read saved report rows, not the salary totals in the daily KPI sheet.
    rows = records_from_worksheet(get_worksheet(REPORTS_SHEET))
    return latest_reports(rows, report_date)


def manager_report_ids_for_date(report_date):
    rows = records_from_worksheet(get_worksheet(MANAGER_REPORTS_SHEET))
    return {
        str(row.get("employee_id", "")).strip()
        for row in rows
        if row.get("Дата") == report_date and str(row.get("employee_id", "")).strip()
    }


def latest_reports(rows, report_date):
    by_employee = {}
    for row in rows:
        employee_id = str(row.get("employee_id", "")).strip()
        if row.get("Дата") == report_date and employee_id:
            # Worksheet order is append order. Replaced reports must not double count.
            by_employee[employee_id] = row
    return by_employee


def report_kpis(row):
    raw = row.get("KPI данные") or "[]"
    # Fail visibly on corrupt input instead of reporting a misleading zero.
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Некорректные данные KPI в отчете.")
    return items


def total_kpis(rows):
    totals = {}
    for row in rows:
        for item in report_kpis(row):
            key = str(item.get("kpi_id") or item.get("name") or "Без названия")
            entry = totals.setdefault(key, {"name": item.get("name") or key, "qty": Decimal(0)})
            entry["qty"] += quantity(item.get("qty"))
    return totals


def volume_values(rows):
    totals = total_kpis(rows)
    result = {
        field: quantity_text(totals.get(kpi_id, {}).get("qty", 0))
        for field, kpi_id in VOLUME_KPI_IDS.items()
    }
    result["posted_goods"] = quantity_text(sum(
        (item["qty"] for key, item in totals.items()
         if key in PACKAGING_KPI_IDS or str(item["name"]).strip().casefold().startswith("упаковка")),
        Decimal(0),
    ))
    return result


def load_day_reports(report_date, draft=None, include_manager_reports=False):
    reports = reports_for_date(report_date)
    saved_ids = set(reports)
    if draft:
        reports[str(draft["employee_id"])] = draft
    expected = get_working_employees_for_date(parse_date(report_date))
    manager_report_ids = (
        manager_report_ids_for_date(report_date) if include_manager_reports else set()
    )
    missing = [
        employee
        for employee in expected
        if str(employee["employee_id"]) not in reports
        and not (
            has_role(employee, "warehouse_manager")
            and str(employee["employee_id"]) in manager_report_ids
        )
    ]
    return {
        "reports": reports,
        "expected": expected,
        "missing": missing,
        "saved_ids": saved_ids,
        "manager_report_ids": manager_report_ids,
        "draft": draft,
    }


def report_coverage_text(day):
    expected_ids = {str(employee["employee_id"]) for employee in day["expected"]}
    saved_count = len(expected_ids & day["saved_ids"])
    lines = [f"Отчеты по расписанию сохранены: {saved_count} из {len(expected_ids)}."]
    if day["draft"]:
        lines.append("Учтен текущий черновик вашего складского отчета.")
    if day["missing"]:
        lines.append("Предварительный расчет. Нет отчетов: " + ", ".join(
            employee["full_name"] for employee in day["missing"]
        ) + ".")
    if not expected_ids:
        lines.append("В расписании нет смен на эту дату; полнота отчетов не подтверждена.")
    return "\n".join(lines)


def warehouse_tasks_for_report(report_date):
    day = parse_date(report_date)
    if not get_task_export(day, "warehouse"):
        return []
    return [task for task in get_tasks_by_date(day, include_cancelled=False)
            if task.get("Тип задачи") == TASK_TYPE_WAREHOUSE]


def completed_tasks_text(tasks):
    return "\n".join(
        f"• {task['Описание']}" for task in tasks if task.get("Статус") == TASK_STATUS_DONE
    )


def format_daily_summary(report_date, day):
    reports = day["reports"]
    lines = [f"📊 Отчеты склада за {report_date}",
             f"Отчитались по расписанию: {len(day['expected']) - len(day['missing'])} из {len(day['expected'])}"]
    if day["missing"]:
        lines.append("Ожидаются отчеты: " + ", ".join(employee["full_name"] for employee in day["missing"]))
    expected_ids = {str(employee["employee_id"]) for employee in day["expected"]}
    for employee_id, row in sorted(reports.items(), key=lambda pair: (pair[1].get("ФИО", ""), pair[0])):
        name = row.get("ФИО") or employee_id
        if employee_id not in expected_ids:
            name += " (вне расписания)"
        lines.extend(["", name, f"Время работы: {row.get('Рабочий промежуток') or '—'}",
                      f"Обед: {row.get('Обед') or 0} ч.",
                      f"Отработано часов: {row.get('Отработано часов') or 0}",
                      "Задачи:", row.get("Задачи") or "—", "KPI:"])
        items = report_kpis(row)
        lines.extend(f"{item.get('name') or item.get('kpi_id')}: {quantity_text(item.get('qty'))}" for item in items)
        if not items:
            lines.append("—")
    lines.extend(["", "ИТОГО KPI (количество)"])
    totals = total_kpis(reports.values())
    for key, item in sorted(totals.items()):
        lines.append(f"{item['name']}: {quantity_text(item['qty'])}")
    if not totals:
        lines.append("—")
    return "\n".join(lines)


def summary_chunks(text, limit=3800):
    """Keep every character, including long task descriptions and emoji."""
    chunks = []
    while text:
        end = 0
        units = 0
        for char in text:
            units += 2 if ord(char) > 0xFFFF else 1
            if units > limit:
                break
            end += 1
        if end < len(text):
            newline = text.rfind("\n", 0, end)
            if newline > 0:
                end = newline + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks
