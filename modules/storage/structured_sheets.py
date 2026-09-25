from sqlalchemy import text

from modules.storage.postgres import get_engine, session_scope


SHEET_TABLES = {
    ("payroll", "Сотрудники"): {
        "table": "payroll_employees",
        "columns": [
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Телефон", "phone"),
            ("telegram_user_id", "telegram_user_id"),
            ("telegram_username", "telegram_username"),
            ("role", "role"),
            ("roles", "roles"),
            ("hourly_rate", "hourly_rate"),
            ("fixed_salary", "fixed_salary"),
            ("include_in_common_fund", "include_in_common_fund"),
            ("is_active", "is_active"),
        ],
    },
    ("payroll", "Ежедневные отчеты"): {
        "table": "payroll_reports",
        "columns": [
            ("report_id", "report_id"),
            ("Дата", "report_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("telegram_user_id", "telegram_user_id"),
            ("Рабочий промежуток", "work_interval"),
            ("Отработано часов", "worked_hours"),
            ("Тип смены", "shift_type"),
            ("Обед", "lunch_hours"),
            ("Задачи", "tasks"),
            ("KPI данные", "kpi_data"),
            ("KPI сумма", "kpi_sum"),
            ("telegram_chat_id", "telegram_chat_id"),
            ("telegram_thread_id", "telegram_thread_id"),
            ("telegram_message_id", "telegram_message_id"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
        ],
    },
    ("payroll", "Отчеты руководителя склада"): {
        "table": "payroll_manager_reports",
        "columns": [
            ("manager_report_id", "manager_report_id"),
            ("Дата", "report_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("telegram_user_id", "telegram_user_id"),
            ("Данные отчета", "report_data"),
            ("Сообщения Telegram", "telegram_messages"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
        ],
    },
    ("payroll", "Расходы"): {
        "table": "payroll_expenses",
        "columns": [
            ("expense_id", "expense_id"),
            ("Дата", "expense_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Комментарий", "comment"),
            ("Сумма", "amount"),
            ("Создал", "created_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("payroll", "Штрафы"): {
        "table": "payroll_penalties",
        "columns": [
            ("penalty_id", "penalty_id"),
            ("Дата", "penalty_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Категория штрафа", "penalty_category"),
            ("Тип штрафа", "penalty_type"),
            ("Комментарий", "comment"),
            ("Сумма", "amount"),
            ("Назначил", "assigned_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("payroll", "Премиальные"): {
        "table": "payroll_bonuses",
        "columns": [
            ("bonus_id", "bonus_id"),
            ("Дата", "bonus_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Комментарий", "comment"),
            ("Сумма", "amount"),
            ("Назначил", "assigned_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("payroll", "KPI"): {
        "table": "payroll_kpi",
        "columns": [
            ("kpi_id", "kpi_id"),
            ("Название", "name"),
            ("Ставка", "rate"),
            ("Активно", "is_active"),
        ],
    },
    ("payroll", "Расчетные периоды"): {
        "table": "payroll_periods",
        "columns": [
            ("period_id", "period_id"),
            ("Название", "name"),
            ("Дата начала", "start_date"),
            ("Дата конца", "end_date"),
            ("Режим оплаты", "payment_mode"),
            ("Статус", "status"),
            ("Создал", "created_by"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
        ],
    },
    ("payroll", "KPI за день"): {
        "table": "payroll_kpi_daily",
        "columns": [
            ("Дата", "report_date"),
            ("Имя сотрудника", "full_name"),
            ("Отработанные часы", "worked_hours"),
            ("Упаковка 1 Cлой", "packing_1_layer"),
            ("Упаковка 2 Слой", "packing_2_layer"),
            ("Упаковка 3 Слой", "packing_3_layer"),
            ("Упаковка больших сумок", "packing_large_bags"),
            ("Упаковка маленьких сумок", "packing_small_bags"),
            ("Упаковка Ремни", "packing_belts"),
            ("Отправка", "shipping"),
            ("Сток", "stock"),
            ("УПД для стока", "stock_upd"),
            ("Пресс", "press"),
            ("Возврат", "return_qty"),
            ("Инвент", "inventory"),
            ("Общее", "total"),
        ],
    },
    ("payroll", "Дополнительные начисления"): {
        "table": "payroll_additional_payments",
        "columns": [
            ("additional_pay_id", "additional_pay_id"),
            ("Тип позиции", "position_type"),
            ("Название позиции", "position_name"),
            ("Дата начисления", "accrual_date"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Неделя начала", "week_start"),
            ("Неделя конца", "week_end"),
            ("Количество", "quantity"),
            ("Ставка за единицу", "unit_rate"),
            ("Недельная ставка", "weekly_rate"),
            ("Начислено до штрафа", "gross_amount"),
            ("Есть ошибки", "has_errors"),
            ("Описание ошибок", "error_comment"),
            ("Штраф за ошибки", "error_penalty"),
            ("Итого", "total_amount"),
            ("Комментарий", "comment"),
            ("Назначил", "assigned_by"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
        ],
    },
    ("payroll", "Водители"): {
        "table": "payroll_drivers",
        "columns": [
            ("driver_id", "driver_id"),
            ("ФИО", "full_name"),
            ("Телефон", "phone"),
            ("Номер машины", "vehicle_number"),
            ("Комментарий", "comment"),
            ("Активен", "is_active"),
            ("Создал", "created_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("payroll", "Оплата водителям"): {
        "table": "payroll_driver_payments",
        "columns": [
            ("driver_payment_id", "driver_payment_id"),
            ("Дата", "payment_date"),
            ("driver_id", "driver_id"),
            ("ФИО водителя", "driver_name"),
            ("Номер машины", "vehicle_number"),
            ("Сумма", "amount"),
            ("Комментарий", "comment"),
            ("Создал", "created_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("payroll", "Списания водителям"): {
        "table": "payroll_driver_write_offs",
        "columns": [
            ("driver_write_off_id", "driver_write_off_id"),
            ("Дата", "write_off_date"),
            ("driver_id", "driver_id"),
            ("ФИО водителя", "driver_name"),
            ("Номер машины", "vehicle_number"),
            ("Сумма", "amount"),
            ("Комментарий", "comment"),
            ("Создал", "created_by"),
            ("Создано", "created_at_text"),
        ],
    },
    ("operations", "Архив расписаний"): {
        "table": "schedule_archive",
        "columns": [
            ("Неделя начала", "week_start"),
            ("Неделя конца", "week_end"),
            ("Дата", "schedule_date"),
            ("День недели", "weekday"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("telegram_user_id", "telegram_user_id"),
            ("Время выхода", "shift_time"),
            ("Дежурный", "is_duty"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
            ("Обновил", "updated_by"),
        ],
    },
    ("operations", "Дежурства"): {
        "table": "schedule_duties",
        "columns": [
            ("Неделя начала", "week_start"),
            ("Неделя конца", "week_end"),
            ("Дата", "duty_date"),
            ("День недели", "weekday"),
            ("employee_id", "employee_id"),
            ("ФИО", "full_name"),
            ("Назначил", "assigned_by"),
            ("Создано", "created_at_text"),
            ("Обновлено", "updated_at_text"),
        ],
    },
    ("operations", "Выгрузки расписания"): {
        "table": "schedule_exports",
        "columns": [
            ("Неделя начала", "week_start"),
            ("Неделя конца", "week_end"),
            ("Версия", "version"),
            ("chat_id", "chat_id"),
            ("thread_id", "thread_id"),
            ("message_id", "message_id"),
            ("Файл", "filename"),
            ("Выгрузил", "sent_by"),
            ("Создано", "created_at_text"),
            ("Тип записи", "record_type"),
            ("Статус", "status"),
            ("Обновлено", "updated_at_text"),
        ],
    },
}


def table_config(source, sheet_name):
    return SHEET_TABLES.get((source, sheet_name))


def init_structured_sheet_tables():
    with get_engine().begin() as connection:
        for config in SHEET_TABLES.values():
            column_defs = ["id serial primary key", "row_number integer not null unique"]
            column_defs.extend(f"{column_name} text" for _, column_name in config["columns"])
            connection.execute(
                text(
                    f"create table if not exists {config['table']} "
                    f"({', '.join(column_defs)})"
                )
            )
            for _, column_name in config["columns"]:
                connection.execute(
                    text(f"alter table {config['table']} add column if not exists {column_name} text")
                )


def sync_structured_sheet(source, sheet_name, rows):
    config = table_config(source, sheet_name)
    if not config:
        return

    init_structured_sheet_tables()
    table_name = config["table"]
    columns = config["columns"]
    db_columns = ["row_number"] + [column_name for _, column_name in columns]
    placeholders = [f":{column_name}" for column_name in db_columns]

    with session_scope() as session:
        session.execute(text(f"delete from {table_name}"))

        for row_number, data in rows:
            values = {"row_number": row_number}
            for header, column_name in columns:
                values[column_name] = str((data or {}).get(header, "") or "")

            session.execute(
                text(
                    f"insert into {table_name} "
                    f"({', '.join(db_columns)}) "
                    f"values ({', '.join(placeholders)})"
                ),
                values,
            )


def sync_all_structured_sheets(archive_rows):
    grouped = {}

    for row in archive_rows:
        key = (row.source, row.sheet_name)
        grouped.setdefault(key, []).append((row.row_number, row.data or {}))

    for (source, sheet_name), rows in grouped.items():
        rows.sort(key=lambda item: item[0])
        sync_structured_sheet(source, sheet_name, rows)
