import logging
import os
from copy import deepcopy
import re
import uuid
from decimal import InvalidOperation
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import GROUP_CHAT_ID, PAYROLL_REPORT_TOPIC_ID

# ID темы «Штрафы» в общей Telegram-группе.
# Добавь в .env: PAYROLL_PENALTIES_TOPIC_ID=...
PAYROLL_PENALTIES_TOPIC_ID = os.getenv("PAYROLL_PENALTIES_TOPIC_ID", "")
from core.keyboards import build_main_menu_keyboard
from modules.payroll.config import (
    PENALTY_ABSENCE_NO_REASON_TYPE_ID,
    PENALTY_AUTO_DISMISSAL_TYPE_ID,
    PENALTY_TYPES,
    PENALTY_TYPE_GROUPS,
)
from modules.payroll.calculations import build_full_payroll_text, build_personal_salary_text
from modules.payroll.google_sheets import (
    append_manager_report,
    append_daily_report,
    append_bonus,
    append_expense,
    append_penalty,
    cleanup_old_operational_data,
    create_active_period,
    delete_bonus,
    delete_expense,
    delete_daily_kpi_row,
    delete_daily_report,
    find_employee_for_telegram_user,
    find_manager_report_row,
    find_report_row,
    get_active_period,
    list_bonuses_in_period,
    get_employee_by_id,
    get_employees,
    get_kpi_items,
    get_period_for_date,
    get_periods,
    is_manager,
    list_expenses_in_period,
    kpi_from_json,
    kpi_to_json,
    calculate_kpi_sum,
    count_employee_penalties_by_type,
    money,
    report_data_to_model,
    safe_float,
    payment_mode_label,
    shift_type_label,
    normalize_shift_type,
    PAYMENT_MODE_HOURLY,
    PAYMENT_MODE_SHIFT,
    SHIFT_TYPE_FULL,
    SHIFT_TYPE_HALF,
    update_active_period,
    update_daily_report,
    update_report_message_ids,
    validate_date,
    now_str,
)
from modules.employees.roles import format_role_labels, has_role
from modules.payroll.pdf_reports import create_payroll_pdf
from modules.payroll.vacations import (
    VacationValidationError,
    create_vacation,
    delete_vacation,
    get_vacation,
    list_vacations,
    update_vacation_period,
)
from modules.tasks.config import TASK_TYPE_GENERAL, TASK_TYPE_WAREHOUSE
from modules.tasks.storage import get_tasks_by_date, materialize_templates_for_date
from modules.schedule.google_sheets import get_schedule_matrix
from modules.payroll.daily_summary import refresh_daily_summary
from modules.payroll.report_automation import (
    completed_tasks_text, load_day_reports, quantity, quantity_text,
    report_coverage_text, summary_chunks, volume_values, warehouse_tasks_for_report,
)


(
    CREATE_EMPLOYEE,
    CREATE_DATE,
    CREATE_INTERVAL,
    CREATE_LUNCH,
    CREATE_TASKS,
    CREATE_KPI_SELECT,
    CREATE_KPI_QTY,
    CREATE_MANAGER_VOLUMES,
    CREATE_MANAGER_SPEED,
    CREATE_MANAGER_ERRORS,
    CREATE_MANAGER_PROBLEMS,
    CREATE_MANAGER_PERSONAL_PLAN,
    CREATE_MANAGER_WAREHOUSE_PLAN,
    CREATE_MANAGER_COMPLETION,
    EDIT_EMPLOYEE,
    EDIT_DATE,
    EDIT_FIELD,
    EDIT_VALUE,
    EDIT_KPI_SELECT,
    EDIT_KPI_QTY,
    SALARY_EMPLOYEE,
    EXPENSE_EMPLOYEE,
    EXPENSE_DATE,
    EXPENSE_COMMENT,
    EXPENSE_AMOUNT,
    EXPENSE_DELETE_SELECT,
    PENALTY_EMPLOYEE,
    PENALTY_DATE,
    PENALTY_TYPE_GROUP,
    PENALTY_TYPE,
    PENALTY_COMMENT,
    PENALTY_AMOUNT,
    PENALTY_PHOTOS,
    PENALTY_CONFIRM,
    PAYROLL_PERIOD_SELECT,
    PERIOD_NAME,
    PERIOD_START,
    PERIOD_END,
    PERIOD_PAYMENT_MODE,
    PERIOD_EDIT_FIELD,
    PERIOD_EDIT_VALUE,
    BONUS_EMPLOYEE,
    BONUS_DATE,
    BONUS_AMOUNT,
    BONUS_COMMENT,
    BONUS_DELETE_SELECT,
    CLEANUP_CONFIRM,
    VACATION_EMPLOYEE,
    VACATION_START,
    VACATION_END,
    VACATION_EDIT_SELECT,
    VACATION_EDIT_START,
    VACATION_EDIT_END,
    VACATION_DELETE_SELECT,
    CREATE_SHIFT_TYPE,
    EDIT_SHIFT_TYPE,
) = range(300, 356)

CREATE_MANAGER_DATE = 356


# ============================================================
# ОБЩИЕ КЛАВИАТУРЫ
# ============================================================


def payroll_main_keyboard(manager=False, warehouse_manager=False):
    rows = [
        [InlineKeyboardButton("📝 Создать ежедневный отчет", callback_data="pay:create_report")],
        [InlineKeyboardButton("✏️ Изменить отчет", callback_data="pay:edit_report")],
        [InlineKeyboardButton("💰 Проверить свою ЗП", callback_data="pay:check_salary")],
        [InlineKeyboardButton("💸 Расходы", callback_data="pay:expenses")],
    ]

    if warehouse_manager:
        rows.insert(
            1,
            [InlineKeyboardButton("🧭 Руководительский отчет", callback_data="pay:manager_report")],
        )

    if manager:
        rows.extend(
            [
                [InlineKeyboardButton("⚠️ Штраф", callback_data="pay:add_penalty")],
                [InlineKeyboardButton("🎁 Премиальные", callback_data="pay:bonuses")],
                [InlineKeyboardButton("🏖 Отпускные", callback_data="pay:vacations")],
                [InlineKeyboardButton("🚚 Водители", callback_data="pay:drivers")],
                [InlineKeyboardButton("📊 Рассчитать ЗП за период", callback_data="pay:calculate_period")],
                [InlineKeyboardButton("⚙️ Расчетные периоды", callback_data="pay:periods")],
                [InlineKeyboardButton("⚙️ Позиции KPI", callback_data="pay:kpi_management")],
                [InlineKeyboardButton("🧹 Очистить данные старше 1 года", callback_data="pay:cleanup")],
            ]
        )

    rows.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")])
    return InlineKeyboardMarkup(rows)


def payroll_back_keyboard(back_target=None):
    rows = []
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"payback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def expenses_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить расход", callback_data="expense:add")],
            [
                InlineKeyboardButton("Удалить расход", callback_data="expense:delete"),
                InlineKeyboardButton("Посмотреть расходы", callback_data="expense:view"),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def bonuses_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Назначить премиальные", callback_data="bonus:add")],
            [
                InlineKeyboardButton("Удалить запись", callback_data="bonus:delete"),
                InlineKeyboardButton("Просмотр последних 10 записей", callback_data="bonus:view"),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def vacations_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить отпуск", callback_data="vacation:add")],
            [
                InlineKeyboardButton("✏️ Изменить период", callback_data="vacation:edit"),
                InlineKeyboardButton("🗑 Удалить", callback_data="vacation:delete"),
            ],
            [InlineKeyboardButton("👀 Последние записи", callback_data="vacation:view")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def vacations_select_keyboard(vacations, prefix):
    rows = []
    for vacation in vacations:
        label = (
            f"{vacation['employee_name']} · "
            f"{vacation['start_date']}—{vacation['end_date']}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"{prefix}:{vacation['vacation_id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def vacation_delete_confirm_keyboard(vacation_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Да, удалить",
                    callback_data=f"vacationdelconfirm:{vacation_id}",
                )
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="vacation:delete")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def date_keyboard(prefix, back_target=None):
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    today_text = today.strftime("%d.%m.%Y")
    yesterday_text = yesterday.strftime("%d.%m.%Y")
    rows = [
            [InlineKeyboardButton(today_text, callback_data=f"{prefix}:{today_text}")],
            [InlineKeyboardButton(yesterday_text, callback_data=f"{prefix}:{yesterday_text}")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"payback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def edit_report_date_keyboard():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(today.strftime("%d.%m.%Y"), callback_data=f"editnewdate:{today.strftime('%d.%m.%Y')}")],
        [InlineKeyboardButton(yesterday.strftime("%d.%m.%Y"), callback_data=f"editnewdate:{yesterday.strftime('%d.%m.%Y')}")],
        [InlineKeyboardButton("Ввести другую дату", callback_data="editnewdate:manual")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="editfield:back")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
    ])


def employees_keyboard(prefix, include_cancel=True):
    rows = []
    for employee in get_employees():
        rows.append(
            [InlineKeyboardButton(employee["full_name"], callback_data=f"{prefix}:{employee['employee_id']}")]
        )
    if include_cancel:
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def payroll_periods_keyboard(periods):
    rows = []
    for period in reversed(periods[-20:]):
        status = "активный" if period["status"] == "active" else "закрытый"
        label = f"{period['name']} ({period['start_date']} — {period['end_date']}, {status})"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"payperiod:{period['period_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def kpi_keyboard(prefix, back_target=None):
    rows = []
    for item in get_kpi_items():
        rows.append(
            [InlineKeyboardButton(item["name"], callback_data=f"{prefix}:{item['kpi_id']}")]
        )
    rows.append([InlineKeyboardButton("✅ Завершить KPI", callback_data=f"{prefix}:done")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"payback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def edit_field_keyboard(include_shift_type=False):
    rows = [
        [InlineKeyboardButton("Дата отчета", callback_data="editfield:date")],
        [InlineKeyboardButton("Рабочий промежуток", callback_data="editfield:interval")],
    ]
    if include_shift_type:
        rows.append([InlineKeyboardButton("Тип смены", callback_data="editfield:shift_type")])
    rows.extend(
        [
            [InlineKeyboardButton("Время обеда", callback_data="editfield:lunch")],
            [InlineKeyboardButton("Задачи", callback_data="editfield:tasks")],
            [InlineKeyboardButton("KPI", callback_data="editfield:kpi")],
            [InlineKeyboardButton("🗑 Удалить отчет", callback_data="editfield:delete")],
            [InlineKeyboardButton("✅ Завершить изменение", callback_data="editfield:finish")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def edit_field_keyboard_for_context(context):
    period = context.user_data.get("edit_period") or {}
    return edit_field_keyboard(period.get("payment_mode") == PAYMENT_MODE_SHIFT)


def shift_type_keyboard(prefix, back_target=None):
    rows = [
            [
                InlineKeyboardButton(
                    "10:00 — полная смена",
                    callback_data=f"{prefix}:{SHIFT_TYPE_FULL}",
                )
            ],
            [
                InlineKeyboardButton(
                    "15:00 — половина смены",
                    callback_data=f"{prefix}:{SHIFT_TYPE_HALF}",
                )
            ],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"payback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def edit_mode_keyboard(prefix):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить полностью", callback_data=f"{prefix}:replace")],
            [InlineKeyboardButton("➕ Дополнить", callback_data=f"{prefix}:append")],
            [InlineKeyboardButton("⬅️ Назад к выбору поля", callback_data="editfield:back")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def periods_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Создать текущий период", callback_data="period:create")],
            [InlineKeyboardButton("✏️ Изменить текущий период", callback_data="period:edit")],
            [InlineKeyboardButton("📋 Посмотреть периоды", callback_data="period:list")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def period_edit_field_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Название", callback_data="periodfield:name")],
            [InlineKeyboardButton("Дата начала", callback_data="periodfield:start")],
            [InlineKeyboardButton("Дата конца", callback_data="periodfield:end")],
            [InlineKeyboardButton("Режим оплаты", callback_data="periodfield:payment_mode")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="pay:periods")],
        ]
    )


def period_payment_mode_keyboard(prefix):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("По часам", callback_data=f"{prefix}:{PAYMENT_MODE_HOURLY}")],
            [InlineKeyboardButton("Посменно", callback_data=f"{prefix}:{PAYMENT_MODE_SHIFT}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def lunch_keyboard(prefix, back_target=None):
    rows = [
        [InlineKeyboardButton("30 минут", callback_data=f"{prefix}:0.5")],
        [InlineKeyboardButton("1 час", callback_data=f"{prefix}:1")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"payback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def cleanup_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, очистить", callback_data="cleanup:yes")],
            [InlineKeyboardButton("Отмена", callback_data="pay:cancel")],
        ]
    )


def penalty_type_group_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Неверная отправка", callback_data="pntype:wrong_shipping")],
            [InlineKeyboardButton("Неверная инвентаризация", callback_data="pngrp:inventory")],
            [InlineKeyboardButton("Отчет позже срока", callback_data="pngrp:late_report")],
            [InlineKeyboardButton("Опоздание / невыход", callback_data="pngrp:lateness_absence")],
            [InlineKeyboardButton("Неверный пересчет расходников", callback_data="pntype:consumables_wrong_count")],
            [InlineKeyboardButton("Неверно положил товар на полку", callback_data="pntype:wrong_shelf")],
            [InlineKeyboardButton("Некачественная уборка", callback_data="pntype:poor_cleaning")],
            [InlineKeyboardButton("Офисные ключи", callback_data="pngrp:office_keys")],
            [InlineKeyboardButton("Неверное оприходование товара", callback_data="pngrp:receiving_errors")],
            [InlineKeyboardButton("Другое", callback_data="pntype:other")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def penalty_type_keyboard(group_id):
    group = PENALTY_TYPE_GROUPS.get(group_id)
    rows = []

    if group:
        for penalty_type_id in group["items"]:
            penalty_type = PENALTY_TYPES[penalty_type_id]
            rows.append(
                [InlineKeyboardButton(penalty_type["name"], callback_data=f"pntype:{penalty_type_id}")]
            )

    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="pngrp:back")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def penalty_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Подтвердить", callback_data="penalty:confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def penalty_photos_keyboard(has_photos=False):
    rows = []
    if has_photos:
        rows.append([InlineKeyboardButton("✅ Готово", callback_data="penaltyphotos:done")])
    rows.append([InlineKeyboardButton("Без фото", callback_data="penaltyphotos:skip")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def manager_wizard_choice_keyboard(rows):
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="mgrwiz:back")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


# ============================================================
# ОБЩИЕ ХЕЛПЕРЫ
# ============================================================


WORK_INTERVAL_RE = re.compile(
    r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*[-–—]\s*([01]?\d|2[0-3]):([0-5]\d)\s*$"
)


def parse_work_interval(text):
    """Возвращает нормализованный интервал и длительность с шагом 0,5 часа."""
    match = WORK_INTERVAL_RE.match(str(text or ""))
    if not match:
        return None
    start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    duration_minutes = end_total - start_total
    if duration_minutes <= 0:
        duration_minutes += 24 * 60
    if duration_minutes <= 0 or duration_minutes >= 24 * 60 or duration_minutes % 30:
        return None
    normalized = f"{start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d}"
    return normalized, duration_minutes / 60


def calculate_worked_hours(interval, lunch_hours):
    parsed = parse_work_interval(interval)
    if not parsed:
        return None
    _, duration = parsed
    worked_hours = duration - safe_float(lunch_hours)
    if worked_hours <= 0 or abs(worked_hours * 2 - round(worked_hours * 2)) > 0.0001:
        return None
    return worked_hours


def parse_positive_amount(text):
    value = safe_float(text)
    if value <= 0:
        return None
    return value


def current_employee_or_none(update):
    return find_employee_for_telegram_user(update.effective_user)


async def deny_unknown_user(update: Update):
    user = update.effective_user
    text = (
        "Я не нашел вас в справочнике сотрудников.\n\n"
        f"Ваш Telegram user_id: {user.id}\n"
        f"Ваш username: @{user.username if user.username else '-'}\n\n"
        "Передайте эти данные руководителю, чтобы он добавил вас в лист «Сотрудники»."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)


async def show_payroll_menu_message(target, employee):
    manager = is_manager(employee)
    text = "💰 Расчет ЗП"
    if employee:
        text += f"\n\nСотрудник: {employee['full_name']}"
        text += f"\nДолжности: {format_role_labels(employee)}"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(
            text,
            reply_markup=payroll_main_keyboard(
                manager=manager,
                warehouse_manager=is_warehouse_manager(employee),
            ),
        )
    else:
        await target.reply_text(
            text,
            reply_markup=payroll_main_keyboard(
                manager=manager,
                warehouse_manager=is_warehouse_manager(employee),
            ),
        )


async def payroll_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    await show_payroll_menu_message(query, employee)
    return ConversationHandler.END


async def payroll_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    employee = current_employee_or_none(update)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if employee:
            await show_payroll_menu_message(query, employee)
        else:
            await query.edit_message_text("Действие отменено.", reply_markup=build_main_menu_keyboard())
    else:
        if employee:
            await show_payroll_menu_message(update.message, employee)
        else:
            await update.message.reply_text("Действие отменено.", reply_markup=build_main_menu_keyboard())

    return ConversationHandler.END


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    employee = current_employee_or_none(update)

    text = (
        f"Telegram user_id: {user.id}\n"
        f"Username: @{user.username if user.username else '-'}"
    )

    if employee:
        text += (
            f"\n\nСотрудник: {employee['full_name']}\n"
            f"employee_id: {employee['employee_id']}\n"
            f"roles: {', '.join(employee.get('roles') or [employee['role']])}"
        )
    else:
        text += "\n\nВ листе «Сотрудники» вы пока не найдены."

    await update.message.reply_text(text)


def format_kpi_lines(kpi_items):
    if not kpi_items:
        return "—"

    lines = []

    for item in kpi_items:
        qty = safe_float(item.get("qty"))
        lines.append(f"{item.get('name')} — {money(qty)}")

    return "\n".join(lines)


def is_warehouse_manager(employee):
    return has_role(employee, "warehouse_manager")


def get_brand_managers():
    return [
        employee for employee in get_employees(include_inactive=False)
        if has_role(employee, "brand_manager") and str(employee.get("telegram_user_id", "")).strip()
    ]


def parse_report_date(value):
    return datetime.strptime(value, "%d.%m.%Y")


def report_tomorrow_date(report_date):
    return parse_report_date(report_date) + timedelta(days=1)


def tasks_for_report_plan(report_date, task_type):
    day = report_tomorrow_date(report_date).date()
    try:
        materialize_templates_for_date(day)
        tasks = get_tasks_by_date(day, include_cancelled=False)
    except Exception:
        logging.exception("Не удалось получить задачи на завтра для отчета")
        return []
    return [task for task in tasks if str(task.get("Тип задачи", "")).strip() == task_type]


def format_plan_tasks_for_report(tasks):
    if not tasks:
        return "—"
    lines = []
    for index, task in enumerate(tasks, start=1):
        description = str(task.get("Описание", "")).strip()
        deadline = str(task.get("Дедлайн", "")).strip()
        if deadline:
            description = f"{description} (дедлайн: {deadline})"
        lines.append(f"{index}. {description}")
    return "\n".join(lines)


MANAGER_VOLUME_STEPS = [
    ("sent_orders", "Отправлено заказов", "Сколько заказов отправлено за день?"),
    ("accepted_goods", "Принятых товаров", "Сколько товаров принято за день?"),
    ("posted_goods", "Оприходованных товаров", "Сколько товаров оприходовано?"),
    ("posted_returns", "Оприходованных возвратов", "Сколько возвратов оприходовано?"),
    ("stock_shipments", "Сток в магазины (количество по KPI)", "Какое количество стока отправлено в магазины (по KPI «Сток»)?"),
    (
        "no_shipments_work",
        "Если не было отправок, то что сделали за день",
        "Если отправок не было, что сделали за день? Если отправки были, напишите «не актуально».",
    ),
]

MANAGER_ERROR_STEPS = [
    ("shipping_error", "Ошибка в отправке", "Были ли ошибки в отправке?"),
    ("receiving_error", "Ошибка в оприходовании", "Были ли ошибки в оприходовании?"),
    ("discipline_violation", "Нарушение дисциплины/порядка", "Были ли нарушения дисциплины или порядка?"),
    ("sanction", "Была ли применена санкция? Если да, то какая", "Была ли применена санкция? Если да, то какая?"),
]

MANAGER_COMPLETION_STEPS = [
    ("all_tasks_done", "Были ли выполнены все задачи на день?", "Были ли выполнены все задачи на день?"),
    (
        "postponed_reason",
        "Если нет, то почему и на какой срок перенесена задача",
        "Если не все выполнено, почему и на какой срок перенесена задача? Если все выполнено, напишите «не актуально».",
    ),
    ("day_score", "Общая оценка дня", "Оцените день от 1 до 10."),
]


def manager_step_prompt(step):
    for steps in (MANAGER_VOLUME_STEPS, MANAGER_ERROR_STEPS, MANAGER_COMPLETION_STEPS):
        for key, _label, prompt in steps:
            if key == step:
                return prompt

    prompts = {
        "speed_task": "Блок «СКОРОСТЬ».\n\nКакую задачу описываем?",
        "speed_time": "Сколько времени ушло на эту задачу?",
        "speed_blockers": "Было ли что-то, что замедляло выполнение этой задачи?",
        "problem_description": "Блок «ПРОБЛЕМЫ».\n\nОпишите проблему.",
        "problem_reason": "Какая причина проблемы?",
        "problem_solution": "Какое решение принято для её решения?",
        "personal_plan_extra": "Напишите задачу, которую нужно добавить в личный план на завтра.",
        "warehouse_plan_extra": "Напишите задачу, которую нужно добавить в складской план на завтра.",
    }
    return prompts.get(step, "Введите ответ:")


def format_labeled_lines(steps, values):
    return "\n".join(
        f"• {label}: {str(values.get(key, '')).strip() or '—'}"
        for key, label, _prompt in steps
    )


def format_speed_entries(entries):
    if not entries:
        return "—"
    lines = []
    for index, entry in enumerate(entries, start=1):
        lines.extend(
            [
                f"{index}. Задача: {entry.get('task') or '—'}",
                f"   Какое количество времени ушло на задачу: {entry.get('time') or '—'}",
                f"   Было ли то, что замедляло выполнение задач: {entry.get('blockers') or '—'}",
            ]
        )
    return "\n".join(lines)


def format_problem_entries(entries):
    if not entries:
        return "—"
    lines = []
    for index, entry in enumerate(entries, start=1):
        lines.extend(
            [
                f"{index}. Описание проблем/ы: {entry.get('description') or '—'}",
                f"   Причина проблем/ы: {entry.get('reason') or '—'}",
                f"   Какое решение принято для её решения: {entry.get('solution') or '—'}",
            ]
        )
    return "\n".join(lines)


def format_plan_with_extra(auto_plan, extra_items):
    extra_items = [str(item).strip() for item in extra_items or [] if str(item).strip()]
    if not extra_items:
        return str(auto_plan or "—").strip() or "—"

    lines = []
    base = str(auto_plan or "").strip()
    if base and base != "—":
        lines.append(base)
        start_index = len(base.splitlines()) + 1
    else:
        start_index = 1
    for offset, item in enumerate(extra_items, start=start_index):
        lines.append(f"{offset}. {item}")
    return "\n".join(lines)


def build_manager_report_from_wizard(context):
    values = context.user_data.get("manager_wizard_values") or {}
    return {
        "volumes": format_labeled_lines(MANAGER_VOLUME_STEPS, values),
        "speed": format_speed_entries(context.user_data.get("manager_speed_entries") or []),
        "errors": format_labeled_lines(MANAGER_ERROR_STEPS, values),
        "problems": format_problem_entries(context.user_data.get("manager_problem_entries") or []),
        "personal_plan": context.user_data.get("manager_personal_plan") or "—",
        "warehouse_plan": context.user_data.get("manager_warehouse_plan") or "—",
        "completion": format_labeled_lines(MANAGER_COMPLETION_STEPS, values),
    }


def manager_report_block(report_model):
    manager_report = report_model.get("manager_report") or {}
    return "\n".join(
        [
            "",
            "1️⃣ ОБЪЁМЫ",
            manager_report.get("volumes") or "—",
            "",
            "2️⃣ СКОРОСТЬ",
            manager_report.get("speed") or "—",
            "",
            "3️⃣ ОШИБКИ",
            manager_report.get("errors") or "—",
            "",
            "4️⃣ ПРОБЛЕМЫ",
            manager_report.get("problems") or "—",
            "",
            "5️⃣ ЛИЧНЫЙ ПЛАН НА ЗАВТРА",
            manager_report.get("personal_plan") or "—",
            "",
            "6️⃣ СКЛАДСКОЙ ПЛАН НА ЗАВТРА",
            manager_report.get("warehouse_plan") or "—",
            "",
            "7️⃣ ВЫПОЛНЕНИЕ ПЛАНА И ОЦЕНКА ДНЯ",
            manager_report.get("completion") or "—",
        ]
    )


def format_manager_report_text(employee, report_date, manager_report):
    return "\n".join(
        [
            "🧭 Руководительский отчет",
            "",
            f"Руководитель: {employee['full_name']}",
            f"Дата: {report_date}",
            manager_report_block({"manager_report": manager_report}),
        ]
    )


def format_daily_report_text(report_model):
    employee = report_model["employee"]
    title = "📝 Личный отчет" if report_model.get("manager_report") else "📝 Ежедневный отчет"
    parts = [
        f"🗓 Дата: {report_model['date']}" if report_model.get("manager_report") else title,
    ]
    if report_model.get("manager_report"):
        parts.extend(["", title])

    parts.extend(
        [
            "",
            f"Сотрудник: {employee['full_name']}",
            *([] if report_model.get("manager_report") else [f"Дата: {report_model['date']}"]),
            *(
                [
                    "Тип смены: "
                    + shift_type_label(
                        report_model.get("shift_type") or SHIFT_TYPE_FULL
                    )
                ]
                if report_model.get("payment_mode") == PAYMENT_MODE_SHIFT
                else []
            ),
            f"Время работы: {report_model['interval']}",
            f"Обед: {money(report_model.get('lunch_hours', 0))} ч.",
            f"Отработано часов: {money(report_model['hours'])}",
            "",
            "Задачи:",
            report_model["tasks"] or "—",
            "",
            "KPI:",
            format_kpi_lines(report_model["kpi_items"]),
        ]
    )
    if report_model.get("manager_report"):
        parts.append(manager_report_block(report_model))
    return "\n".join(parts)


def report_belongs_to_telegram_user(report_model, telegram_user):
    employee = report_model.get("employee") or {}
    employee_user_id = str(employee.get("telegram_user_id", "")).strip()
    return bool(employee_user_id and employee_user_id == str(telegram_user.id))


async def send_daily_report_to_private_target(target, report_model, reply_markup):
    text = format_daily_report_text(report_model)
    if hasattr(target, "edit_message_text"):
        result = await target.edit_message_text(text, reply_markup=reply_markup)
        message = result if hasattr(result, "message_id") else target.message
    else:
        message = await target.reply_text(text, reply_markup=reply_markup)

    employee_user_id = int(report_model["employee"]["telegram_user_id"])
    chat_id = getattr(message, "chat_id", None) or employee_user_id
    message_id = getattr(message, "message_id", None)
    if not message_id:
        raise RuntimeError("Не удалось определить message_id отчета руководителя склада.")

    return {
        "chat_id": int(chat_id),
        "thread_id": "",
        "message_id": message_id,
    }


async def send_manager_report_to_recipients(
    context,
    employee,
    report_date,
    manager_report,
    own_target=None,
    own_reply_markup=None,
):
    text = format_manager_report_text(employee, report_date, manager_report)
    chunks = split_long_message(text)
    messages = []
    employee_user_id = str(employee.get("telegram_user_id", "")).strip()
    if not employee_user_id:
        raise RuntimeError("У руководителя склада не указан telegram_user_id.")

    brand_managers = [
        manager
        for manager in get_brand_managers()
        if str(manager.get("telegram_user_id", "")).strip() != employee_user_id
    ]
    if not brand_managers:
        raise RuntimeError("Не найден активный руководитель бренда с telegram_user_id.")

    if own_target is not None:
        if hasattr(own_target, "edit_message_text"):
            result = await own_target.edit_message_text(chunks[0], reply_markup=own_reply_markup)
            message = result if hasattr(result, "message_id") else getattr(own_target, "message", None)
        else:
            message = await own_target.reply_text(chunks[0], reply_markup=own_reply_markup)
        if message and getattr(message, "message_id", None):
            messages.append(
                {
                    "recipient": "warehouse_manager",
                    "chat_id": int(getattr(message, "chat_id", 0) or employee_user_id),
                    "message_id": message.message_id,
                }
            )
        for chunk in chunks[1:]:
            message = await context.bot.send_message(chat_id=int(employee_user_id), text=chunk)
            messages.append(
                {
                    "recipient": "warehouse_manager",
                    "chat_id": int(employee_user_id),
                    "message_id": message.message_id,
                }
            )
    else:
        for chunk in chunks:
            message = await context.bot.send_message(chat_id=int(employee_user_id), text=chunk)
            messages.append(
                {
                    "recipient": "warehouse_manager",
                    "chat_id": int(employee_user_id),
                    "message_id": message.message_id,
                }
            )

    delivered_brand_manager_ids = set()
    for brand_manager in brand_managers:
        recipient_id = str(brand_manager.get("telegram_user_id", "")).strip()
        if not recipient_id or recipient_id == employee_user_id or recipient_id in delivered_brand_manager_ids:
            continue
        delivered_brand_manager_ids.add(recipient_id)
        for chunk in chunks:
            message = await context.bot.send_message(chat_id=int(recipient_id), text=chunk)
            messages.append(
                {
                    "recipient": "brand_manager",
                    "chat_id": int(recipient_id),
                    "message_id": message.message_id,
                }
            )

    return messages


async def send_daily_report_to_topic(
    context: ContextTypes.DEFAULT_TYPE,
    report_model,
    private_target=None,
    private_reply_markup=None,
):
    employee = report_model.get("employee")
    if is_warehouse_manager(employee):
        telegram_user_id = str(employee.get("telegram_user_id", "")).strip()
        if not telegram_user_id:
            raise RuntimeError("У руководителя склада не указан telegram_user_id.")

        if private_target:
            return await send_daily_report_to_private_target(
                private_target,
                report_model,
                private_reply_markup,
            )

        message = await context.bot.send_message(
            chat_id=int(telegram_user_id),
            text=format_daily_report_text(report_model),
        )

        return {
            "chat_id": int(telegram_user_id),
            "thread_id": "",
            "message_id": message.message_id,
        }

    if not GROUP_CHAT_ID or not PAYROLL_REPORT_TOPIC_ID:
        return {"chat_id": "", "thread_id": "", "message_id": ""}

    message = await context.bot.send_message(
        chat_id=int(GROUP_CHAT_ID),
        message_thread_id=int(PAYROLL_REPORT_TOPIC_ID),
        text=format_daily_report_text(report_model),
    )
    return {
        "chat_id": int(GROUP_CHAT_ID),
        "thread_id": int(PAYROLL_REPORT_TOPIC_ID),
        "message_id": message.message_id,
    }


def format_penalty_topic_text(employee, penalty_date, penalty_category, penalty_type, comment, amount, created_by, photo_count=0):
    evidence_text = f"{photo_count} фото" if photo_count else "нет"
    return "\n".join(
        [
            "⚠️ Новый штраф",
            "",
            f"Сотрудник: {employee['full_name']}",
            f"Дата: {penalty_date}",
            f"Категория: {penalty_category}",
            f"Тип штрафа: {penalty_type}",
            f"Комментарий: {comment}",
            f"Сумма: {money(amount)}",
            f"Фото-доказательства: {evidence_text}",
            "",
            f"Назначил: {created_by}",
        ]
    )


def format_penalty_preview(employee, penalty_date, penalty_category, penalty_type, comment, amount, photo_count=0):
    evidence_text = f"{photo_count} фото" if photo_count else "нет"
    return "\n".join(
        [
            "Проверьте штраф:",
            "",
            f"Сотрудник: {employee['full_name']}",
            f"Дата: {penalty_date}",
            f"Категория: {penalty_category}",
            f"Тип штрафа: {penalty_type}",
            f"Комментарий: {comment}",
            f"Сумма: {money(amount)}",
            f"Фото-доказательства: {evidence_text}",
        ]
    )


async def send_penalty_to_topic(
    context: ContextTypes.DEFAULT_TYPE,
    employee,
    penalty_date,
    penalty_category,
    penalty_type,
    comment,
    amount,
    created_by,
    photo_file_ids=None,
):
    if not GROUP_CHAT_ID:
        return "GROUP_CHAT_ID не настроен, сообщение в тему «Штрафы» не отправлено."

    if not PAYROLL_PENALTIES_TOPIC_ID:
        return "PAYROLL_PENALTIES_TOPIC_ID не настроен, сообщение в тему «Штрафы» не отправлено."

    photo_file_ids = photo_file_ids or []
    text = format_penalty_topic_text(
        employee,
        penalty_date,
        penalty_category,
        penalty_type,
        comment,
        amount,
        created_by,
        photo_count=len(photo_file_ids),
    )
    common_kwargs = {
        "chat_id": int(GROUP_CHAT_ID),
        "message_thread_id": int(PAYROLL_PENALTIES_TOPIC_ID),
    }

    if not photo_file_ids:
        await context.bot.send_message(
            **common_kwargs,
            text=text,
        )
        return "Штраф отправлен в тему «Штрафы» ✅"

    if len(text) > 1000:
        text = text[:950] + "\n\n...текст обрезан."

    if len(photo_file_ids) == 1:
        await context.bot.send_photo(
            **common_kwargs,
            photo=photo_file_ids[0],
            caption=text,
        )
        return "Штраф отправлен в тему «Штрафы» ✅"

    first_chunk = True
    for chunk_start in range(0, len(photo_file_ids), 10):
        chunk = photo_file_ids[chunk_start:chunk_start + 10]
        media = []

        for index, photo_file_id in enumerate(chunk):
            if first_chunk and index == 0:
                media.append(InputMediaPhoto(media=photo_file_id, caption=text))
            else:
                media.append(InputMediaPhoto(media=photo_file_id))

        await context.bot.send_media_group(
            **common_kwargs,
            media=media,
        )
        first_chunk = False

    return "Штраф отправлен в тему «Штрафы» ✅"


def last_30_days_period(end_date):
    end_dt = datetime.strptime(end_date, "%d.%m.%Y")
    start_dt = end_dt - timedelta(days=30)
    return start_dt.strftime("%d.%m.%Y"), end_dt.strftime("%d.%m.%Y")


async def maybe_send_third_absence_notice(context, employee, penalty_date, created_by):
    auto_type = PENALTY_TYPES[PENALTY_AUTO_DISMISSAL_TYPE_ID]
    absence_type = PENALTY_TYPES[PENALTY_ABSENCE_NO_REASON_TYPE_ID]
    start_date, end_date = last_30_days_period(penalty_date)

    absence_count = count_employee_penalties_by_type(
        employee_id=employee["employee_id"],
        penalty_type=absence_type["name"],
        start_date=start_date,
        end_date=end_date,
    )

    if absence_count != 3:
        return None

    comment = (
        f"За последние 30 дней ({start_date} — {end_date}) у сотрудника "
        "зафиксирован третий невыход на смену без уважительной причины. "
        "Необходимо рассмотреть вопрос об увольнении."
    )

    append_penalty(
        employee,
        penalty_date,
        auto_type["category"],
        auto_type["name"],
        comment,
        0,
        created_by,
    )

    try:
        return await send_penalty_to_topic(
            context=context,
            employee=employee,
            penalty_date=penalty_date,
            penalty_category=auto_type["category"],
            penalty_type=auto_type["name"],
            comment=comment,
            amount=0,
            created_by=created_by,
        )
    except Exception as error:
        logging.exception("Не удалось отправить автоматическое уведомление о третьем невыходе")
        return f"Автоуведомление о третьем невыходе не отправлено ⚠️\nОшибка: {error}"


async def delete_old_report_message(context: ContextTypes.DEFAULT_TYPE, report_model):
    chat_id = report_model.get("telegram_chat_id")
    message_id = report_model.get("telegram_message_id")
    if not chat_id or not message_id:
        return False
    try:
        await context.bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
        return True
    except Exception:
        logging.exception("Не удалось удалить старое сообщение ежедневного отчета")
        return False


# ============================================================
# СОЗДАНИЕ ЕЖЕДНЕВНОГО ОТЧЕТА
# ============================================================


def employee_has_scheduled_shift(employee_id, report_date):
    try:
        report_day = datetime.strptime(report_date, "%d.%m.%Y").date()
        week_start = report_day - timedelta(days=report_day.weekday())
        _, _, schedule, _ = get_schedule_matrix(week_start)
        return bool(schedule.get(str(employee_id), {}).get(report_date))
    except Exception:
        logging.exception("Не удалось проверить смену для руководительского отчета")
        return False


async def manager_only_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    employee = current_employee_or_none(update)
    if not is_warehouse_manager(employee):
        await query.edit_message_text("Руководительский отчет доступен только руководителю склада.")
        return ConversationHandler.END

    context.user_data["employee_id"] = employee["employee_id"]
    context.user_data["manager_report_only"] = True
    await query.edit_message_text(
        "За какую дату сформировать руководительский отчет?",
        reply_markup=date_keyboard("mgrdate"),
    )
    return CREATE_MANAGER_DATE


async def manager_only_report_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_date = query.data.replace("mgrdate:", "", 1)
    employee = get_employee_by_id(context.user_data.get("employee_id"))
    if not employee or not is_warehouse_manager(employee):
        await query.edit_message_text("Руководитель склада не найден.")
        return ConversationHandler.END
    if find_manager_report_row(employee["employee_id"], report_date)[0] is not None:
        await query.edit_message_text(
            f"Руководительский отчет за {report_date} уже существует.",
            reply_markup=payroll_main_keyboard(
                manager=is_manager(employee),
                warehouse_manager=True,
            ),
        )
        return ConversationHandler.END

    if employee_has_scheduled_shift(employee["employee_id"], report_date):
        daily_report_exists = find_report_row(employee["employee_id"], report_date)[0] is not None
        if not daily_report_exists:
            await query.edit_message_text(
                "На эту дату у вас запланирована складская смена. "
                "Создайте ежедневный отчет — после складской части бот автоматически откроет руководительскую.",
                reply_markup=payroll_main_keyboard(
                    manager=is_manager(employee),
                    warehouse_manager=True,
                ),
            )
            return ConversationHandler.END

    context.user_data["report_date"] = report_date
    return await start_manager_report_wizard(query, context)


async def create_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    context.user_data["pay_action"] = "create_report"

    if is_manager(employee):
        await query.edit_message_text(
            "Выберите сотрудника для ежедневного отчета:",
            reply_markup=employees_keyboard("cremp"),
        )
        return CREATE_EMPLOYEE

    context.user_data["employee_id"] = employee["employee_id"]
    await query.edit_message_text(
        f"Сотрудник: {employee['full_name']}\n\nВыберите дату отчета:",
        reply_markup=date_keyboard("crdate", "create_employee"),
    )
    return CREATE_DATE


async def create_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("cremp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return CREATE_EMPLOYEE

    context.user_data["employee_id"] = employee_id
    await query.edit_message_text(
        f"Сотрудник: {employee['full_name']}\n\nВыберите дату отчета:",
        reply_markup=date_keyboard("crdate", "create_employee"),
    )
    return CREATE_DATE


async def create_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_date = query.data.replace("crdate:", "")
    employee_id = context.user_data.get("employee_id")
    employee = get_employee_by_id(employee_id)

    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return CREATE_DATE

    if find_report_row(employee_id, report_date)[0] is not None:
        await query.edit_message_text(
            f"Отчет сотрудника {employee['full_name']} за {report_date} уже существует.\n"
            "Используйте «Изменить отчет».",
            reply_markup=payroll_main_keyboard(manager=is_manager(current_employee_or_none(update))),
        )
        return ConversationHandler.END

    period = get_period_for_date(report_date)
    if not period:
        await query.edit_message_text(
            "Для выбранной даты не настроен расчетный период. Обратитесь к руководителю.",
            reply_markup=payroll_main_keyboard(manager=is_manager(current_employee_or_none(update))),
        )
        return ConversationHandler.END

    context.user_data["report_date"] = report_date
    context.user_data["report_period"] = period
    context.user_data["shift_type"] = ""
    if period.get("payment_mode") == PAYMENT_MODE_SHIFT:
        await query.edit_message_text(
            f"Сотрудник: {employee['full_name']}\nДата: {report_date}\n\n"
            "Выберите тип смены:",
            reply_markup=shift_type_keyboard("crshift", "create_date"),
        )
        return CREATE_SHIFT_TYPE

    await query.edit_message_text(
        f"Сотрудник: {employee['full_name']}\nДата: {report_date}\n\n"
        "Введите рабочий временной промежуток, например: 10:00-19:00",
        reply_markup=payroll_back_keyboard("create_date"),
    )
    return CREATE_INTERVAL


async def create_shift_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    shift_type = normalize_shift_type(query.data.replace("crshift:", ""))
    if not shift_type:
        await query.edit_message_text(
            "Выберите тип смены:",
            reply_markup=shift_type_keyboard("crshift", "create_date"),
        )
        return CREATE_SHIFT_TYPE
    context.user_data["shift_type"] = shift_type
    await query.edit_message_text(
        f"Тип смены: {shift_type_label(shift_type)}\n\n"
        "Введите фактический рабочий временной промежуток, например: 10:00-19:00",
        reply_markup=payroll_back_keyboard("create_shift_type"),
    )
    return CREATE_INTERVAL


async def create_interval_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_work_interval(update.message.text)
    if not parsed:
        period = context.user_data.get("report_period") or {}
        back_target = (
            "create_shift_type"
            if period.get("payment_mode") == PAYMENT_MODE_SHIFT
            else "create_date"
        )
        await update.message.reply_text(
            "Введите корректный промежуток с шагом 30 минут, например 10:00-19:00.",
            reply_markup=payroll_back_keyboard(back_target),
        )
        return CREATE_INTERVAL

    interval, _ = parsed
    context.user_data["interval"] = interval
    await update.message.reply_text(
        "Сколько времени занял обед?",
        reply_markup=lunch_keyboard("crlunch", "create_interval"),
    )
    return CREATE_LUNCH


async def create_lunch_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lunch_hours = safe_float(query.data.replace("crlunch:", ""))
    if lunch_hours not in {0.5, 1.0}:
        await query.edit_message_text(
            "Выберите время обеда:",
            reply_markup=lunch_keyboard("crlunch", "create_interval"),
        )
        return CREATE_LUNCH

    hours = calculate_worked_hours(context.user_data.get("interval"), lunch_hours)
    if hours is None:
        await query.edit_message_text(
            "После вычета обеда рабочее время должно быть больше нуля и кратно 0,5 часа. "
            "Введите рабочий промежуток заново, например 10:00-19:00.",
            reply_markup=payroll_back_keyboard(),
        )
        return CREATE_INTERVAL

    context.user_data["lunch_hours"] = lunch_hours
    context.user_data["hours"] = hours
    warning = ""
    period = context.user_data.get("report_period") or {}
    shift_type = normalize_shift_type(context.user_data.get("shift_type"))
    if period.get("payment_mode") == PAYMENT_MODE_SHIFT:
        expected_hours = 4 if shift_type == SHIFT_TYPE_HALF else 8
        if hours != expected_hours:
            warning = (
                f"⚠️ Фактически отработано {money(hours)} ч., "
                f"но {shift_type_label(shift_type)} оплачивается как {expected_hours} ч.\n\n"
            )
    await query.edit_message_text(
        warning
        + f"Отработано за вычетом обеда: {money(hours)} ч.\n\n"
        "Опишите выполненные за день задачи:",
        reply_markup=payroll_back_keyboard("create_lunch"),
    )
    return CREATE_TASKS


async def create_tasks_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = update.message.text.strip()
    if not tasks:
        await update.message.reply_text(
            "Описание задач не должно быть пустым. Введите задачи:",
            reply_markup=payroll_back_keyboard("create_lunch"),
        )
        return CREATE_TASKS

    context.user_data["tasks"] = tasks
    context.user_data["kpi_items"] = []
    await update.message.reply_text(
        "Выберите категорию KPI или нажмите «Завершить KPI»:",
        reply_markup=kpi_keyboard("crkpi", "create_tasks"),
    )
    return CREATE_KPI_SELECT


async def create_kpi_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kpi_id = query.data.replace("crkpi:", "")

    if kpi_id == "done":
        employee = get_employee_by_id(context.user_data.get("employee_id"))
        manager_report_exists = (
            employee
            and find_manager_report_row(employee["employee_id"], context.user_data.get("report_date"))[0] is not None
        )
        if is_warehouse_manager(employee) and not manager_report_exists:
            return await start_manager_report_wizard(query, context)
        return await finish_create_report(query, context, update.effective_user)

    kpi_items = get_kpi_items()
    selected = next((item for item in kpi_items if item["kpi_id"] == kpi_id), None)
    if not selected:
        await query.edit_message_text("KPI не найден. Выберите заново:", reply_markup=kpi_keyboard("crkpi", "create_tasks"))
        return CREATE_KPI_SELECT

    context.user_data["selected_kpi"] = selected
    await query.edit_message_text(
        f"KPI: {selected['name']}\n\nВведите количество:",
        reply_markup=payroll_back_keyboard("create_kpi"),
    )
    return CREATE_KPI_QTY


async def create_kpi_qty_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = parse_positive_amount(update.message.text)
    if qty is None:
        await update.message.reply_text("Введите количество числом больше 0:", reply_markup=payroll_back_keyboard("create_kpi"))
        return CREATE_KPI_QTY

    selected = context.user_data.get("selected_kpi")
    if not selected:
        await update.message.reply_text("KPI потерялся. Выберите категорию заново:", reply_markup=kpi_keyboard("crkpi", "create_tasks"))
        return CREATE_KPI_SELECT

    context.user_data.setdefault("kpi_items", []).append(
        {
            "kpi_id": selected["kpi_id"],
            "name": selected["name"],
            "rate": selected["rate"],
            "qty": qty,
            "sum": qty * selected["rate"],
        }
    )
    context.user_data.pop("selected_kpi", None)

    await update.message.reply_text(
        "KPI добавлен. Выберите еще один KPI или нажмите «Завершить KPI»:",
        reply_markup=kpi_keyboard("crkpi", "create_tasks"),
    )
    return CREATE_KPI_SELECT


async def payroll_create_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("payback:", "", 1)
    employee = get_employee_by_id(context.user_data.get("employee_id"))

    if target == "create_employee":
        current = current_employee_or_none(update)
        if not is_manager(current):
            context.user_data.clear()
            await query.edit_message_text("💰 Расчет ЗП", reply_markup=payroll_main_keyboard(manager=False))
            return ConversationHandler.END
        context.user_data.pop("employee_id", None)
        await query.edit_message_text("Выберите сотрудника для ежедневного отчета:", reply_markup=employees_keyboard("cremp"))
        return CREATE_EMPLOYEE
    if target == "create_date":
        await query.edit_message_text(
            f"Сотрудник: {employee['full_name'] if employee else '—'}\n\nВыберите дату отчета:",
            reply_markup=date_keyboard("crdate", "create_employee"),
        )
        return CREATE_DATE
    if target == "create_shift_type":
        await query.edit_message_text("Выберите тип смены:", reply_markup=shift_type_keyboard("crshift", "create_date"))
        return CREATE_SHIFT_TYPE
    if target == "create_interval":
        period = context.user_data.get("report_period") or {}
        back_target = "create_shift_type" if period.get("payment_mode") == PAYMENT_MODE_SHIFT else "create_date"
        await query.edit_message_text("Введите рабочий временной промежуток:", reply_markup=payroll_back_keyboard(back_target))
        return CREATE_INTERVAL
    if target == "create_lunch":
        await query.edit_message_text(
            "Сколько времени занял обед?",
            reply_markup=lunch_keyboard("crlunch", "create_interval"),
        )
        return CREATE_LUNCH
    if target == "create_tasks":
        await query.edit_message_text(
            "Опишите выполненные за день задачи:",
            reply_markup=payroll_back_keyboard("create_lunch"),
        )
        return CREATE_TASKS
    if target == "create_kpi":
        context.user_data.pop("selected_kpi", None)
        await query.edit_message_text("Выберите категорию KPI или нажмите «Завершить KPI»:", reply_markup=kpi_keyboard("crkpi", "create_tasks"))
        return CREATE_KPI_SELECT

    context.user_data.clear()
    await query.edit_message_text("💰 Расчет ЗП", reply_markup=payroll_main_keyboard(manager=is_manager(current_employee_or_none(update))))
    return ConversationHandler.END


MANAGER_WIZARD_STATE_KEYS = {
    "manager_wizard_values",
    "manager_speed_entries",
    "manager_problem_entries",
    "manager_personal_extra",
    "manager_warehouse_extra",
    "manager_wizard_step",
    "manager_wizard_screen",
    "manager_current_speed",
    "manager_current_problem",
    "manager_personal_plan",
    "manager_warehouse_plan",
    "auto_personal_plan",
    "auto_warehouse_plan",
}


def manager_wizard_nav_keyboard(context):
    rows = []
    if context.user_data.get("manager_wizard_history"):
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="mgrwiz:back")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def remember_manager_wizard_state(context):
    snapshot = {
        key: deepcopy(context.user_data[key])
        for key in MANAGER_WIZARD_STATE_KEYS
        if key in context.user_data
    }
    context.user_data.setdefault("manager_wizard_history", []).append(snapshot)


def restore_manager_wizard_state(context):
    history = context.user_data.get("manager_wizard_history") or []
    if not history:
        return False
    snapshot = history.pop()
    for key in MANAGER_WIZARD_STATE_KEYS:
        context.user_data.pop(key, None)
    context.user_data.update(snapshot)
    context.user_data["manager_wizard_history"] = history
    return True


async def send_manager_wizard_prompt(target, context: ContextTypes.DEFAULT_TYPE, text=None, reply_markup=None):
    text = text or manager_step_prompt(context.user_data.get("manager_wizard_step"))
    reply_markup = reply_markup or manager_wizard_nav_keyboard(context)
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=reply_markup)
    else:
        await target.reply_text(text, reply_markup=reply_markup)


async def start_manager_report_wizard(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_values"] = {}
    context.user_data["manager_speed_entries"] = []
    context.user_data["manager_problem_entries"] = []
    context.user_data["manager_personal_extra"] = []
    context.user_data["manager_warehouse_extra"] = []
    context.user_data["manager_wizard_history"] = []
    context.user_data["manager_wizard_step"] = MANAGER_VOLUME_STEPS[0][0]
    context.user_data["manager_wizard_screen"] = "prompt"
    return await ask_next_manager_step(target, context)


def manager_report_suggestion(context, step):
    report_date = context.user_data["report_date"]
    if step in {"no_shipments_work", "all_tasks_done"}:
        if step == "no_shipments_work":
            sent = context.user_data.get("manager_wizard_values", {}).get("sent_orders")
            if sent is not None and quantity(sent) > 0:
                return "Не актуально", "В отчете указаны отправки."
        tasks = warehouse_tasks_for_report(report_date)
        if not tasks:
            return None, "Нет выгруженных складских задач за эту дату. Заполните ответ вручную."
        if step == "no_shipments_work":
            completed = completed_tasks_text(tasks)
            if not completed:
                return None, "Нет складских задач с отметкой выполнения за эту дату. Заполните ответ вручную."
            return completed, "Выполненные задачи из темы склада. Можно принять список или ввести свой ответ."
        remaining = [task["Описание"] for task in tasks if task.get("Статус") != "done"]
        return ("Нет" if remaining else "Да", "По выгруженным складским задачам." + (
            "\nНе выполнены:\n" + "\n".join(f"• {name}" for name in remaining) if remaining else " Все выполнены."
        ))
    if step not in {"sent_orders", "posted_goods", "posted_returns", "stock_shipments"}:
        return None, ""
    draft = None
    if not context.user_data.get("manager_report_only"):
        employee = get_employee_by_id(context.user_data["employee_id"])
        draft = {"employee_id": employee["employee_id"], "ФИО": employee["full_name"],
                 "Дата": report_date, "KPI данные": kpi_to_json(context.user_data.get("kpi_items", []))}
    day = load_day_reports(report_date, draft=draft)
    if not day["reports"]:
        return None, report_coverage_text(day) + "\nСохраненных отчетов нет. Введите значение вручную."
    value = volume_values(day["reports"].values())[step]
    lines = [report_coverage_text(day), "", "По отчетам сотрудников:"]
    for row in day["reports"].values():
        lines.append(f"• {row.get('ФИО') or row['employee_id']}: {volume_values([row])[step]}")
    if step == "posted_goods":
        lines.append("Сумма количеств всех KPI упаковки.")
    return value, "\n".join(lines)


async def ask_next_manager_step(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "prompt"
    step = context.user_data.get("manager_wizard_step")
    prefix = ""
    if step in {item[0] for item in MANAGER_VOLUME_STEPS}:
        prefix = "Блок 1 «ОБЪЁМЫ».\n\n"
    elif step in {item[0] for item in MANAGER_ERROR_STEPS}:
        prefix = "Блок 3 «ОШИБКИ».\n\n"
    elif step in {item[0] for item in MANAGER_COMPLETION_STEPS}:
        prefix = "Блок 7 «ВЫПОЛНЕНИЕ ПЛАНА И ОЦЕНКА ДНЯ».\n\n"
    context.user_data.pop("manager_auto_suggestion", None)
    text = f"{prefix}{manager_step_prompt(step)}"
    rows = []
    auto_steps = {"sent_orders", "posted_goods", "posted_returns", "stock_shipments", "no_shipments_work", "all_tasks_done"}
    if step in auto_steps:
        try:
            value, explanation = manager_report_suggestion(context, step)
        except Exception:
            logging.exception("Не удалось рассчитать подсказку руководителю")
            value, explanation = None, "Не удалось загрузить данные. Можно обновить расчет или ввести ответ вручную."
        token = uuid.uuid4().hex[:8]
        context.user_data["manager_auto_suggestion"] = {"step": step, "token": token, "value": value}
        if explanation:
            parts = summary_chunks(explanation, limit=1500)
            text += "\n\n" + parts[0] + ("\n…" if len(parts) > 1 else "")
        if value is not None:
            parts = summary_chunks(value, limit=1400)
            preview = parts[0] + ("\n… (будет принят полный список)" if len(parts) > 1 else "")
            text += f"\n\nПредлагаемый ответ:\n{preview}"
            rows.append([InlineKeyboardButton("✅ Принять", callback_data=f"mgrwiz:auto:accept:{token}")])
        rows.append([InlineKeyboardButton("✏️ Ввести другое", callback_data=f"mgrwiz:auto:manual:{token}"),
                     InlineKeyboardButton("🔄 Обновить расчет", callback_data=f"mgrwiz:auto:refresh:{token}")])
    rows.extend(manager_wizard_nav_keyboard(context).inline_keyboard)
    await send_manager_wizard_prompt(target, context, text=text, reply_markup=InlineKeyboardMarkup(rows))
    return CREATE_MANAGER_VOLUMES


def next_step_after_linear(current_step, steps):
    keys = [item[0] for item in steps]
    index = keys.index(current_step)
    return keys[index + 1] if index + 1 < len(keys) else None


async def ask_speed_continue(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "speed_continue"
    count = len(context.user_data.get("manager_speed_entries") or [])
    await send_manager_wizard_prompt(
        target,
        context,
        text=f"Задача по скорости добавлена. Сейчас описано задач: {count}.",
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("➕ Добавить еще задачу", callback_data="mgrwiz:speed:add")],
                [InlineKeyboardButton("➡️ Перейти к ошибкам", callback_data="mgrwiz:speed:next")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def ask_problem_continue(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "problem_continue"
    count = len(context.user_data.get("manager_problem_entries") or [])
    await send_manager_wizard_prompt(
        target,
        context,
        text=f"Проблема добавлена. Сейчас описано проблем: {count}.",
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("➕ Добавить еще проблему", callback_data="mgrwiz:problem:add")],
                [InlineKeyboardButton("➡️ Перейти к личному плану", callback_data="mgrwiz:problem:next")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def ask_personal_plan_choice(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "personal_choice"
    plan = format_plan_tasks_for_report(tasks_for_report_plan(context.user_data["report_date"], TASK_TYPE_GENERAL))
    context.user_data["auto_personal_plan"] = plan
    await send_manager_wizard_prompt(
        target,
        context,
        text=(
            "Блок 5 «ЛИЧНЫЙ ПЛАН НА ЗАВТРА».\n\n"
            f"Автоплан:\n{plan}\n\n"
            "Можно оставить как есть, отметить выходной или добавить задачи по одной."
        ),
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("✅ Оставить автоплан", callback_data="mgrwiz:personal:keep")],
                [InlineKeyboardButton("➕ Добавить задачу", callback_data="mgrwiz:personal:add")],
                [InlineKeyboardButton("Не работаю завтра", callback_data="mgrwiz:personal:off")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def ask_personal_plan_continue(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "personal_continue"
    count = len(context.user_data.get("manager_personal_extra") or [])
    await send_manager_wizard_prompt(
        target,
        context,
        text=f"Задача добавлена в личный план. Дополнительно добавлено: {count}.",
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("➕ Добавить еще", callback_data="mgrwiz:personal:add")],
                [InlineKeyboardButton("➡️ Перейти к складскому плану", callback_data="mgrwiz:personal:next")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def ask_warehouse_plan_choice(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "warehouse_choice"
    plan = format_plan_tasks_for_report(tasks_for_report_plan(context.user_data["report_date"], TASK_TYPE_WAREHOUSE))
    context.user_data["auto_warehouse_plan"] = plan
    await send_manager_wizard_prompt(
        target,
        context,
        text=(
            "Блок 6 «СКЛАДСКОЙ ПЛАН НА ЗАВТРА».\n\n"
            f"Автоплан:\n{plan}\n\n"
            "Можно оставить как есть или добавить задачи по одной."
        ),
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("✅ Оставить автоплан", callback_data="mgrwiz:warehouse:keep")],
                [InlineKeyboardButton("➕ Добавить задачу", callback_data="mgrwiz:warehouse:add")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def ask_warehouse_plan_continue(target, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["manager_wizard_screen"] = "warehouse_continue"
    count = len(context.user_data.get("manager_warehouse_extra") or [])
    await send_manager_wizard_prompt(
        target,
        context,
        text=f"Задача добавлена в складской план. Дополнительно добавлено: {count}.",
        reply_markup=manager_wizard_choice_keyboard(
            [
                [InlineKeyboardButton("➕ Добавить еще", callback_data="mgrwiz:warehouse:add")],
                [InlineKeyboardButton("➡️ Перейти к оценке дня", callback_data="mgrwiz:warehouse:next")],
            ]
        ),
    )
    return CREATE_MANAGER_VOLUMES


async def render_manager_wizard_state(target, context):
    screen = context.user_data.get("manager_wizard_screen") or "prompt"
    renderers = {
        "speed_continue": ask_speed_continue,
        "problem_continue": ask_problem_continue,
        "personal_choice": ask_personal_plan_choice,
        "personal_continue": ask_personal_plan_continue,
        "warehouse_choice": ask_warehouse_plan_choice,
        "warehouse_continue": ask_warehouse_plan_continue,
    }
    renderer = renderers.get(screen)
    if renderer:
        return await renderer(target, context)
    return await ask_next_manager_step(target, context)


def validate_manager_wizard_value(step, value):
    if len(value) > 1500:
        return None, "Ответ слишком длинный. Ограничение — 1500 символов."
    numeric_volume_steps = {item[0] for item in MANAGER_VOLUME_STEPS[:-1]}
    if step in numeric_volume_steps:
        try:
            if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", value):
                raise ValueError("Invalid quantity")
            return quantity_text(value), None
        except (ValueError, InvalidOperation):
            return None, "Введите число 0 или больше."
    if step == "day_score":
        if not value.isdigit() or not 1 <= int(value) <= 10:
            return None, "Введите оценку целым числом от 1 до 10."
        return str(int(value)), None
    return value, None


async def manager_report_wizard_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if not value:
        await update.message.reply_text("Ответ не должен быть пустым. Введите значение:")
        return CREATE_MANAGER_VOLUMES

    step = context.user_data.get("manager_wizard_step")
    value, validation_error = validate_manager_wizard_value(step, value)
    if validation_error:
        await update.message.reply_text(validation_error, reply_markup=manager_wizard_nav_keyboard(context))
        return CREATE_MANAGER_VOLUMES

    return await accept_manager_wizard_value(update.message, context, update.effective_user, value)


async def accept_manager_wizard_value(target, context, telegram_user, value):
    step = context.user_data.get("manager_wizard_step")
    context.user_data.pop("manager_auto_suggestion", None)
    remember_manager_wizard_state(context)
    values = context.user_data.setdefault("manager_wizard_values", {})

    volume_keys = [item[0] for item in MANAGER_VOLUME_STEPS]
    error_keys = [item[0] for item in MANAGER_ERROR_STEPS]
    completion_keys = [item[0] for item in MANAGER_COMPLETION_STEPS]

    if step in volume_keys:
        values[step] = value
        next_step = next_step_after_linear(step, MANAGER_VOLUME_STEPS)
        if next_step:
            context.user_data["manager_wizard_step"] = next_step
            return await ask_next_manager_step(target, context)
        context.user_data["manager_wizard_step"] = "speed_task"
        return await ask_next_manager_step(target, context)

    if step == "speed_task":
        context.user_data["manager_current_speed"] = {"task": value}
        context.user_data["manager_wizard_step"] = "speed_time"
        return await ask_next_manager_step(target, context)

    if step == "speed_time":
        context.user_data.setdefault("manager_current_speed", {})["time"] = value
        context.user_data["manager_wizard_step"] = "speed_blockers"
        return await ask_next_manager_step(target, context)

    if step == "speed_blockers":
        current = context.user_data.pop("manager_current_speed", {})
        current["blockers"] = value
        context.user_data.setdefault("manager_speed_entries", []).append(current)
        return await ask_speed_continue(target, context)

    if step in error_keys:
        values[step] = value
        next_step = next_step_after_linear(step, MANAGER_ERROR_STEPS)
        if next_step:
            context.user_data["manager_wizard_step"] = next_step
            return await ask_next_manager_step(target, context)
        context.user_data["manager_wizard_step"] = "problem_description"
        return await ask_next_manager_step(target, context)

    if step == "problem_description":
        context.user_data["manager_current_problem"] = {"description": value}
        context.user_data["manager_wizard_step"] = "problem_reason"
        return await ask_next_manager_step(target, context)

    if step == "problem_reason":
        context.user_data.setdefault("manager_current_problem", {})["reason"] = value
        context.user_data["manager_wizard_step"] = "problem_solution"
        return await ask_next_manager_step(target, context)

    if step == "problem_solution":
        current = context.user_data.pop("manager_current_problem", {})
        current["solution"] = value
        context.user_data.setdefault("manager_problem_entries", []).append(current)
        return await ask_problem_continue(target, context)

    if step == "personal_plan_extra":
        context.user_data.setdefault("manager_personal_extra", []).append(value)
        return await ask_personal_plan_continue(target, context)

    if step == "warehouse_plan_extra":
        context.user_data.setdefault("manager_warehouse_extra", []).append(value)
        return await ask_warehouse_plan_continue(target, context)

    if step in completion_keys:
        values[step] = value
        next_step = next_step_after_linear(step, MANAGER_COMPLETION_STEPS)
        if next_step:
            context.user_data["manager_wizard_step"] = next_step
            return await ask_next_manager_step(target, context)
        context.user_data["manager_report"] = build_manager_report_from_wizard(context)
        if context.user_data.get("manager_report_only"):
            return await finish_manager_only_report(target, context, telegram_user)
        return await finish_create_report(target, context, telegram_user)

    await send_manager_wizard_prompt(target, context, text="Я потерял текущий шаг отчета. Начните создание отчета заново.")
    return ConversationHandler.END


async def manager_report_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.replace("mgrwiz:", "")

    if action.startswith("auto:"):
        parts = action.split(":")
        suggestion = context.user_data.get("manager_auto_suggestion") or {}
        if (len(parts) != 3 or parts[2] != suggestion.get("token")
                or suggestion.get("step") != context.user_data.get("manager_wizard_step")):
            return CREATE_MANAGER_VOLUMES
        if parts[1] == "refresh":
            return await ask_next_manager_step(query, context)
        if parts[1] == "manual":
            context.user_data.pop("manager_auto_suggestion", None)
            await send_manager_wizard_prompt(query, context, text=manager_step_prompt(suggestion["step"]) + "\n\nВведите свой ответ:")
            return CREATE_MANAGER_VOLUMES
        if parts[1] == "accept" and suggestion.get("value") is not None:
            return await accept_manager_wizard_value(query, context, update.effective_user, suggestion["value"])
        return CREATE_MANAGER_VOLUMES

    if action == "back":
        if not restore_manager_wizard_state(context):
            await query.edit_message_text(
                "Это первый шаг отчета. Введите ответ или отмените создание.",
                reply_markup=manager_wizard_nav_keyboard(context),
            )
            return CREATE_MANAGER_VOLUMES
        return await render_manager_wizard_state(query, context)

    remember_manager_wizard_state(context)

    if action == "speed:add":
        context.user_data["manager_wizard_step"] = "speed_task"
        return await ask_next_manager_step(query, context)
    if action == "speed:next":
        context.user_data["manager_wizard_step"] = MANAGER_ERROR_STEPS[0][0]
        return await ask_next_manager_step(query, context)

    if action == "problem:add":
        context.user_data["manager_wizard_step"] = "problem_description"
        return await ask_next_manager_step(query, context)
    if action == "problem:next":
        return await ask_personal_plan_choice(query, context)

    if action == "personal:keep":
        context.user_data["manager_personal_plan"] = context.user_data.get("auto_personal_plan") or "—"
        return await ask_warehouse_plan_choice(query, context)
    if action == "personal:off":
        context.user_data["manager_personal_plan"] = "Не работаю завтра"
        return await ask_warehouse_plan_choice(query, context)
    if action == "personal:add":
        context.user_data["manager_wizard_step"] = "personal_plan_extra"
        return await ask_next_manager_step(query, context)
    if action == "personal:next":
        context.user_data["manager_personal_plan"] = format_plan_with_extra(
            context.user_data.get("auto_personal_plan"),
            context.user_data.get("manager_personal_extra"),
        )
        return await ask_warehouse_plan_choice(query, context)

    if action == "warehouse:keep":
        context.user_data["manager_warehouse_plan"] = context.user_data.get("auto_warehouse_plan") or "—"
        context.user_data["manager_wizard_step"] = MANAGER_COMPLETION_STEPS[0][0]
        return await ask_next_manager_step(query, context)
    if action == "warehouse:add":
        context.user_data["manager_wizard_step"] = "warehouse_plan_extra"
        return await ask_next_manager_step(query, context)
    if action == "warehouse:next":
        context.user_data["manager_warehouse_plan"] = format_plan_with_extra(
            context.user_data.get("auto_warehouse_plan"),
            context.user_data.get("manager_warehouse_extra"),
        )
        context.user_data["manager_wizard_step"] = MANAGER_COMPLETION_STEPS[0][0]
        return await ask_next_manager_step(query, context)

    await query.edit_message_text("Неизвестное действие.", reply_markup=payroll_back_keyboard())
    return CREATE_MANAGER_VOLUMES


async def finish_manager_only_report(target, context, telegram_user):
    employee = get_employee_by_id(context.user_data.get("employee_id"))
    manager_report = context.user_data.get("manager_report") or build_manager_report_from_wizard(context)
    report_date = context.user_data.get("report_date")
    if not employee or not is_warehouse_manager(employee) or not report_date:
        await target.reply_text("Не удалось определить руководителя или дату отчета.")
        context.user_data.clear()
        return ConversationHandler.END

    menu = payroll_main_keyboard(
        manager=is_manager(employee),
        warehouse_manager=True,
    )
    try:
        telegram_messages = await send_manager_report_to_recipients(
            context,
            employee,
            report_date,
            manager_report,
            own_target=target,
            own_reply_markup=menu,
        )
        append_manager_report(employee, report_date, manager_report, telegram_messages)
        await refresh_daily_summary(context, report_date)
    except Exception as error:
        logging.exception("Ошибка создания отдельного руководительского отчета")
        message = f"Руководительский отчет не удалось сохранить/отправить ⚠️\nОшибка: {error}"
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(message, reply_markup=menu)
        else:
            await target.reply_text(message, reply_markup=menu)
    context.user_data.clear()
    return ConversationHandler.END


async def finish_create_report(target, context: ContextTypes.DEFAULT_TYPE, telegram_user):
    employee = get_employee_by_id(context.user_data.get("employee_id"))
    if not employee:
        await target.edit_message_text("Сотрудник не найден.")
        return ConversationHandler.END

    report_date = context.user_data["report_date"]
    interval = context.user_data["interval"]
    hours = context.user_data["hours"]
    shift_type = normalize_shift_type(context.user_data.get("shift_type"))
    period = context.user_data.get("report_period") or get_period_for_date(report_date)
    if period and period.get("payment_mode") == PAYMENT_MODE_SHIFT and not shift_type:
        kwargs = {
            "reply_markup": payroll_main_keyboard(
                manager=is_manager(find_employee_for_telegram_user(telegram_user))
            )
        }
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(
                "Тип смены не выбран. Начните создание отчета заново.",
                **kwargs,
            )
        else:
            await target.reply_text(
                "Тип смены не выбран. Начните создание отчета заново.",
                **kwargs,
            )
        context.user_data.clear()
        return ConversationHandler.END
    lunch_hours = safe_float(context.user_data.get("lunch_hours"))
    tasks = context.user_data["tasks"]
    kpi_items = context.user_data.get("kpi_items", [])

    manager_report = context.user_data.get("manager_report") or None
    report_model = {
        "date": report_date,
        "employee": employee,
        "interval": interval,
        "hours": hours,
        "shift_type": shift_type,
        "payment_mode": (
            period.get("payment_mode") if period else PAYMENT_MODE_HOURLY
        ),
        "lunch_hours": lunch_hours,
        "tasks": tasks,
        "kpi_items": kpi_items,
        "kpi_sum": calculate_kpi_sum(kpi_items),
        "manager_report": None,
        "telegram_chat_id": "",
        "telegram_message_id": "",
    }

    manager = is_manager(find_employee_for_telegram_user(telegram_user))
    delivered_to_private_target = (
        is_warehouse_manager(employee)
        and report_belongs_to_telegram_user(report_model, telegram_user)
    )

    report_saved = False
    try:
        telegram_data = await send_daily_report_to_topic(
            context,
            report_model,
            private_target=target if delivered_to_private_target else None,
            private_reply_markup=(
                payroll_main_keyboard(manager=manager)
                if delivered_to_private_target
                else None
            ),
        )
        append_daily_report(
            employee,
            report_date,
            interval,
            hours,
            tasks,
            kpi_items,
            telegram_data,
            shift_type=shift_type,
            lunch_hours=lunch_hours,
        )
        report_saved = True
        await refresh_daily_summary(context, report_date)
        if is_warehouse_manager(employee):
            status = "Складской отчет сохранен и отправлен в личные сообщения ✅"
        else:
            status = "Отчет сохранен и отправлен в тему ✅"
    except Exception as error:
        logging.exception("Ошибка создания ежедневного отчета")
        status = f"Отчет не удалось сохранить/отправить ⚠️\nОшибка: {error}"

    if report_saved and is_warehouse_manager(employee) and manager_report:
        try:
            telegram_messages = await send_manager_report_to_recipients(
                context,
                employee,
                report_date,
                manager_report,
            )
            append_manager_report(employee, report_date, manager_report, telegram_messages)
            status = "Складской и руководительский отчеты сохранены и отправлены отдельными сообщениями ✅"
        except Exception as error:
            logging.exception("Складской отчет сохранен, но руководительский отчет не отправлен")
            warning = f"Складской отчет сохранен, но руководительский отчет не отправлен ⚠️\nОшибка: {error}"
            try:
                await context.bot.send_message(chat_id=int(employee["telegram_user_id"]), text=warning)
            except Exception:
                logging.exception("Не удалось отправить предупреждение руководителю склада")

    if delivered_to_private_target and not status.startswith("Отчет не удалось"):
        context.user_data.clear()
        return ConversationHandler.END

    text = f"{format_daily_report_text(report_model)}\n\n{status}"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=payroll_main_keyboard(manager=manager))
    else:
        await target.reply_text(text, reply_markup=payroll_main_keyboard(manager=manager))
    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# ИЗМЕНЕНИЕ ЕЖЕДНЕВНОГО ОТЧЕТА
# ============================================================


async def edit_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    current_employee = current_employee_or_none(update)
    if not current_employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    context.user_data["pay_action"] = "edit_report"

    if is_manager(current_employee):
        await query.edit_message_text(
            "Выберите сотрудника, чей отчет нужно изменить:",
            reply_markup=employees_keyboard("edemp"),
        )
        return EDIT_EMPLOYEE

    context.user_data["employee_id"] = current_employee["employee_id"]
    await query.edit_message_text(
        f"Сотрудник: {current_employee['full_name']}\n\nВыберите дату отчета:",
        reply_markup=date_keyboard("eddate"),
    )
    return EDIT_DATE


async def edit_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("edemp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return EDIT_EMPLOYEE

    context.user_data["employee_id"] = employee_id
    await query.edit_message_text(
        f"Сотрудник: {employee['full_name']}\n\nВыберите дату отчета:",
        reply_markup=date_keyboard("eddate"),
    )
    return EDIT_DATE


async def edit_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    report_date = query.data.replace("eddate:", "")
    employee_id = context.user_data.get("employee_id")
    row_index, report_data = find_report_row(employee_id, report_date)

    if not report_data:
        await query.edit_message_text(
            "Отчет за эту дату не найден.",
            reply_markup=payroll_main_keyboard(manager=is_manager(current_employee_or_none(update))),
        )
        return ConversationHandler.END

    period = get_period_for_date(report_date)
    if not period:
        await query.edit_message_text(
            "Для выбранной даты не настроен расчетный период. Обратитесь к руководителю.",
            reply_markup=payroll_main_keyboard(manager=is_manager(current_employee_or_none(update))),
        )
        return ConversationHandler.END

    context.user_data["edit_row_index"] = row_index
    context.user_data["edit_report_data"] = report_data
    context.user_data["edit_original_date"] = report_date
    context.user_data["edit_period"] = period
    await query.edit_message_text(
        "Отчет найден. Что нужно изменить?",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("editfield:", "")

    if field == "finish":
        return await finish_edit_report(query, context, update.effective_user)

    if field == "delete":
        report_data = context.user_data.get("edit_report_data") or {}
        await query.edit_message_text(
            "Удалить ежедневный отчет без возможности восстановления?\n\n"
            f"{report_data.get('Дата', '')} — {report_data.get('ФИО', '')}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Да, удалить", callback_data="editdelete:confirm")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="editfield:back")],
                [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
            ]),
        )
        return EDIT_FIELD

    if field == "back":
        await query.edit_message_text(
            "Что нужно изменить?",
            reply_markup=edit_field_keyboard_for_context(context),
        )
        return EDIT_FIELD

    context.user_data["edit_field"] = field
    report_data = context.user_data.get("edit_report_data") or {}

    if field == "date":
        await query.edit_message_text(
            f"Текущая дата: {report_data.get('Дата', '—')}\n\nВыберите новую дату:",
            reply_markup=edit_report_date_keyboard(),
        )
        return EDIT_FIELD

    if field == "shift_type":
        period = context.user_data.get("edit_period") or {}
        if period.get("payment_mode") != PAYMENT_MODE_SHIFT:
            await query.edit_message_text(
                "Тип смены можно изменить только для посменного расчетного периода.",
                reply_markup=edit_field_keyboard_for_context(context),
            )
            return EDIT_FIELD
        await query.edit_message_text(
            "Выберите новый тип смены:",
            reply_markup=shift_type_keyboard("edshift"),
        )
        return EDIT_SHIFT_TYPE

    if field == "interval":
        current_value = report_data.get("Рабочий промежуток", "") or "—"
        await query.edit_message_text(
            "Текущий рабочий промежуток:\n"
            f"{current_value}\n\n"
            "Введите новый рабочий промежуток с шагом 30 минут:",
            reply_markup=payroll_back_keyboard(),
        )
        return EDIT_VALUE

    if field == "lunch":
        current_value = safe_float(report_data.get("Обед"))
        await query.edit_message_text(
            f"Текущий обед: {money(current_value)} ч.\n\n"
            "Выберите новое время обеда:",
            reply_markup=lunch_keyboard("editlunch"),
        )
        return EDIT_FIELD

    if field == "tasks":
        current_tasks = report_data.get("Задачи", "") or "—"
        await query.edit_message_text(
            "Текущие задачи:\n"
            f"{current_tasks}\n\n"
            "Выберите действие:",
            reply_markup=edit_mode_keyboard("edittasks"),
        )
        return EDIT_FIELD

    if field == "kpi":
        current_kpi_items = kpi_from_json(report_data.get("KPI данные", ""))
        await query.edit_message_text(
            "Текущий KPI:\n"
            f"{format_kpi_lines(current_kpi_items)}\n\n"
            "Выберите действие:",
            reply_markup=edit_mode_keyboard("editkpi"),
        )
        return EDIT_FIELD

    await query.edit_message_text(
        "Неизвестное поле.",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_report_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_date = query.data.replace("editnewdate:", "", 1)
    if new_date == "manual":
        context.user_data["edit_field"] = "date"
        await query.edit_message_text(
            "Введите новую дату отчета в формате ДД.ММ.ГГГГ:",
            reply_markup=payroll_back_keyboard(),
        )
        return EDIT_VALUE
    report_data = context.user_data.get("edit_report_data") or {}
    employee_id = report_data.get("employee_id")
    current_date = report_data.get("Дата", "")
    if new_date != current_date and find_report_row(employee_id, new_date)[0] is not None:
        await query.edit_message_text(
            "У этого сотрудника уже есть отчет за выбранную дату. Выберите другую дату:",
            reply_markup=edit_report_date_keyboard(),
        )
        return EDIT_FIELD
    period = get_period_for_date(new_date)
    if not period:
        await query.edit_message_text(
            "Для выбранной даты не настроен расчетный период. Выберите другую дату:",
            reply_markup=edit_report_date_keyboard(),
        )
        return EDIT_FIELD
    report_data["Дата"] = new_date
    report_data["Обновлено"] = now_str()
    context.user_data["edit_report_data"] = report_data
    context.user_data["edit_period"] = period
    await query.edit_message_text(
        f"Дата отчета изменена на {new_date}.\n\nВыберите ещё поле или завершите изменение.",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_report_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    row_index = context.user_data.get("edit_row_index")
    report_data = context.user_data.get("edit_report_data") or {}
    if not row_index or not report_data:
        await query.edit_message_text("Данные отчета потерялись.")
        return ConversationHandler.END
    deleted = delete_daily_report(row_index)
    if not deleted:
        await query.edit_message_text("Отчет уже удален или не найден.")
        context.user_data.clear()
        return ConversationHandler.END
    await delete_old_report_message(context, deleted)
    await refresh_daily_summary(context, deleted["date"])
    manager = is_manager(current_employee_or_none(update))
    context.user_data.clear()
    await query.edit_message_text(
        "Ежедневный отчет удален ✅",
        reply_markup=payroll_main_keyboard(manager=manager),
    )
    return ConversationHandler.END


async def edit_shift_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period = context.user_data.get("edit_period") or {}
    if period.get("payment_mode") != PAYMENT_MODE_SHIFT:
        await query.edit_message_text(
            "Тип смены можно изменить только для посменного расчетного периода.",
            reply_markup=edit_field_keyboard_for_context(context),
        )
        return EDIT_FIELD
    shift_type = normalize_shift_type(query.data.replace("edshift:", ""))
    if not shift_type:
        await query.edit_message_text(
            "Выберите тип смены:",
            reply_markup=shift_type_keyboard("edshift"),
        )
        return EDIT_SHIFT_TYPE
    report_data = context.user_data.get("edit_report_data") or {}
    report_data["Тип смены"] = shift_type
    report_data["Обновлено"] = now_str()
    context.user_data["edit_report_data"] = report_data
    model_preview = report_data_to_model(report_data)
    await query.edit_message_text(
        "Тип смены изменен. Текущая версия отчета:\n\n"
        f"{format_daily_report_text(model_preview)}\n\n"
        "Выберите ещё поле или нажмите «Завершить изменение».",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_tasks_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode = query.data.replace("edittasks:", "")
    if mode not in {"replace", "append"}:
        await query.edit_message_text(
            "Неизвестное действие.",
            reply_markup=edit_field_keyboard_for_context(context),
        )
        return EDIT_FIELD

    context.user_data["edit_field"] = "tasks"
    context.user_data["edit_mode"] = mode

    report_data = context.user_data.get("edit_report_data") or {}
    current_tasks = report_data.get("Задачи", "") or "—"

    if mode == "replace":
        text = (
            "Текущие задачи:\n"
            f"{current_tasks}\n\n"
            "Введите новый текст задач. Старый текст будет заменен полностью:"
        )
    else:
        text = (
            "Текущие задачи:\n"
            f"{current_tasks}\n\n"
            "Введите текст, который нужно добавить к текущим задачам:"
        )

    await query.edit_message_text(text, reply_markup=payroll_back_keyboard())
    return EDIT_VALUE


def add_or_replace_kpi_item(kpi_items, new_item):
    for item in kpi_items:
        if item.get("kpi_id") == new_item.get("kpi_id"):
            item["name"] = new_item["name"]
            item["rate"] = new_item["rate"]
            item["qty"] = new_item["qty"]
            item["sum"] = new_item["sum"]
            return kpi_items

    kpi_items.append(new_item)
    return kpi_items


async def edit_kpi_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode = query.data.replace("editkpi:", "")
    if mode not in {"replace", "append"}:
        await query.edit_message_text(
            "Неизвестное действие.",
            reply_markup=edit_field_keyboard_for_context(context),
        )
        return EDIT_FIELD

    report_data = context.user_data.get("edit_report_data") or {}
    old_kpi_items = kpi_from_json(report_data.get("KPI данные", ""))

    context.user_data["edit_kpi_mode"] = mode

    if mode == "replace":
        context.user_data["edit_new_kpi_items"] = []
        text = (
            "Текущий KPI:\n"
            f"{format_kpi_lines(old_kpi_items)}\n\n"
            "Выберите KPI заново. Старый KPI-блок будет заменен полностью.\n"
            "Когда закончите, нажмите «Завершить KPI»."
        )
    else:
        context.user_data["edit_new_kpi_items"] = [dict(item) for item in old_kpi_items]
        text = (
            "Текущий KPI:\n"
            f"{format_kpi_lines(old_kpi_items)}\n\n"
            "Выберите KPI, который нужно добавить или изменить.\n"
            "Если выбранный KPI уже есть в отчете, новое количество заменит старое по этой категории.\n"
            "Когда закончите, нажмите «Завершить KPI»."
        )

    await query.edit_message_text(text, reply_markup=kpi_keyboard("edkpi"))
    return EDIT_KPI_SELECT


async def edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    field = context.user_data.get("edit_field")
    report_data = context.user_data.get("edit_report_data") or {}

    if not value:
        await update.message.reply_text("Значение не должно быть пустым. Введите заново:")
        return EDIT_VALUE

    if field == "interval":
        parsed = parse_work_interval(value)
        if not parsed:
            await update.message.reply_text(
                "Введите корректный промежуток с шагом 30 минут, например 10:00-19:00."
            )
            return EDIT_VALUE
        interval, _ = parsed
        hours = calculate_worked_hours(interval, report_data.get("Обед", 0))
        if hours is None:
            await update.message.reply_text(
                "После вычета обеда рабочее время должно быть больше нуля и кратно 0,5 часа."
            )
            return EDIT_VALUE
        report_data["Рабочий промежуток"] = interval
        report_data["Отработано часов"] = hours
    elif field == "date":
        if not validate_date(value):
            await update.message.reply_text("Введите дату в формате ДД.ММ.ГГГГ:")
            return EDIT_VALUE
        current_date = report_data.get("Дата", "")
        employee_id = report_data.get("employee_id")
        if value != current_date and find_report_row(employee_id, value)[0] is not None:
            await update.message.reply_text("У сотрудника уже есть отчет за эту дату. Введите другую дату:")
            return EDIT_VALUE
        period = get_period_for_date(value)
        if not period:
            await update.message.reply_text("Для этой даты не настроен расчетный период. Введите другую дату:")
            return EDIT_VALUE
        report_data["Дата"] = value
        context.user_data["edit_period"] = period
    elif field == "tasks":
        mode = context.user_data.get("edit_mode", "replace")
        old_tasks = str(report_data.get("Задачи", "") or "").strip()

        if mode == "append" and old_tasks:
            report_data["Задачи"] = f"{old_tasks}\n{value}"
        else:
            report_data["Задачи"] = value

        context.user_data.pop("edit_mode", None)
    else:
        await update.message.reply_text("Неизвестное поле.")
        return EDIT_FIELD

    report_data["Обновлено"] = now_str()
    context.user_data["edit_report_data"] = report_data

    model_preview = report_data_to_model(report_data)
    warning = ""
    if field == "hours" and model_preview.get("payment_mode") == PAYMENT_MODE_SHIFT:
        expected_hours = (
            4 if model_preview.get("shift_type") == SHIFT_TYPE_HALF else 8
        )
        if model_preview["hours"] != expected_hours:
            warning = (
                f"⚠️ Фактически указано {money(model_preview['hours'])} ч., "
                f"но {shift_type_label(model_preview.get('shift_type') or SHIFT_TYPE_FULL)} "
                f"оплачивается как {expected_hours} ч.\n\n"
            )

    await update.message.reply_text(
        warning
        + "Изменение принято. Текущая версия отчета:\n\n"
        f"{format_daily_report_text(model_preview)}\n\n"
        "Выберите ещё поле или нажмите «Завершить изменение».",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_lunch_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lunch_hours = safe_float(query.data.replace("editlunch:", ""))
    report_data = context.user_data.get("edit_report_data") or {}
    hours = calculate_worked_hours(report_data.get("Рабочий промежуток", ""), lunch_hours)
    if lunch_hours not in {0.5, 1.0} or hours is None:
        await query.edit_message_text(
            "Не удалось рассчитать часы. Проверьте рабочий промежуток и выберите обед заново.",
            reply_markup=lunch_keyboard("editlunch"),
        )
        return EDIT_FIELD

    report_data["Обед"] = lunch_hours
    report_data["Отработано часов"] = hours
    report_data["Обновлено"] = now_str()
    context.user_data["edit_report_data"] = report_data
    model_preview = report_data_to_model(report_data)
    await query.edit_message_text(
        "Время обеда и отработанные часы обновлены. Текущая версия отчета:\n\n"
        f"{format_daily_report_text(model_preview)}\n\n"
        "Выберите ещё поле или нажмите «Завершить изменение».",
        reply_markup=edit_field_keyboard_for_context(context),
    )
    return EDIT_FIELD


async def edit_kpi_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kpi_id = query.data.replace("edkpi:", "")

    if kpi_id == "done":
        report_data = context.user_data.get("edit_report_data") or {}
        kpi_items = context.user_data.get("edit_new_kpi_items", [])
        report_data["KPI данные"] = kpi_to_json(kpi_items)
        report_data["KPI сумма"] = calculate_kpi_sum(kpi_items)
        report_data["Обновлено"] = now_str()
        context.user_data["edit_report_data"] = report_data
        context.user_data.pop("edit_kpi_mode", None)
        context.user_data.pop("selected_kpi", None)

        model_preview = report_data_to_model(report_data)

        await query.edit_message_text(
            "KPI обновлен. Текущая версия отчета:\n\n"
            f"{format_daily_report_text(model_preview)}\n\n"
            "Выберите ещё поле или нажмите «Завершить изменение».",
            reply_markup=edit_field_keyboard_for_context(context),
        )
        return EDIT_FIELD

    selected = next((item for item in get_kpi_items() if item["kpi_id"] == kpi_id), None)
    if not selected:
        await query.edit_message_text("KPI не найден. Выберите заново:", reply_markup=kpi_keyboard("edkpi"))
        return EDIT_KPI_SELECT

    context.user_data["selected_kpi"] = selected
    await query.edit_message_text(
        f"KPI: {selected['name']}\n\nВведите количество:",
        reply_markup=payroll_back_keyboard(),
    )
    return EDIT_KPI_QTY


async def edit_kpi_qty_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qty = parse_positive_amount(update.message.text)
    if qty is None:
        await update.message.reply_text("Введите количество числом больше 0:")
        return EDIT_KPI_QTY

    selected = context.user_data.get("selected_kpi")
    if not selected:
        await update.message.reply_text("KPI потерялся. Выберите категорию заново:", reply_markup=kpi_keyboard("edkpi"))
        return EDIT_KPI_SELECT

    new_item = {
        "kpi_id": selected["kpi_id"],
        "name": selected["name"],
        "rate": selected["rate"],
        "qty": qty,
        "sum": qty * selected["rate"],
    }

    kpi_items = context.user_data.setdefault("edit_new_kpi_items", [])
    context.user_data["edit_new_kpi_items"] = add_or_replace_kpi_item(kpi_items, new_item)
    context.user_data.pop("selected_kpi", None)

    await update.message.reply_text(
        "KPI добавлен/обновлен. Текущий KPI:\n"
        f"{format_kpi_lines(context.user_data['edit_new_kpi_items'])}\n\n"
        "Выберите ещё KPI или нажмите «Завершить KPI»: ",
        reply_markup=kpi_keyboard("edkpi"),
    )
    return EDIT_KPI_SELECT


async def finish_edit_report(query, context: ContextTypes.DEFAULT_TYPE, telegram_user):
    row_index = context.user_data.get("edit_row_index")
    report_data = context.user_data.get("edit_report_data")

    if not row_index or not report_data:
        await query.edit_message_text("Данные отчета потерялись.")
        return ConversationHandler.END

    old_model = report_data_to_model(report_data)
    await delete_old_report_message(context, old_model)

    model = report_data_to_model(report_data)
    model["kpi_sum"] = calculate_kpi_sum(model["kpi_items"])
    report_data["KPI сумма"] = model["kpi_sum"]
    report_data["Обновлено"] = now_str()

    manager = is_manager(find_employee_for_telegram_user(telegram_user))
    delivered_to_private_target = (
        is_warehouse_manager(model.get("employee"))
        and report_belongs_to_telegram_user(model, telegram_user)
    )

    try:
        telegram_data = await send_daily_report_to_topic(
            context,
            model,
            private_target=query if delivered_to_private_target else None,
            private_reply_markup=(
                payroll_main_keyboard(manager=manager)
                if delivered_to_private_target
                else None
            ),
        )
        report_data["telegram_chat_id"] = telegram_data.get("chat_id", "")
        report_data["telegram_thread_id"] = telegram_data.get("thread_id", "")
        report_data["telegram_message_id"] = telegram_data.get("message_id", "")
        update_daily_report(row_index, report_data)
        original_date = context.user_data.get("edit_original_date")
        if original_date and original_date != model["date"]:
            delete_daily_kpi_row(original_date, report_data.get("ФИО", ""))
            await refresh_daily_summary(context, original_date)
        await refresh_daily_summary(context, model["date"])
        if is_warehouse_manager(model.get("employee")):
            status = "Отчет обновлен и новое сообщение отправлено руководителю склада в личные сообщения ✅"
        else:
            status = "Отчет обновлен и новое сообщение отправлено в тему ✅"
    except Exception as error:
        logging.exception("Ошибка обновления отчета")
        status = f"Отчет не удалось обновить полностью ⚠️\nОшибка: {error}"

    if delivered_to_private_target and not status.startswith("Отчет не удалось"):
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text(
        f"{format_daily_report_text(model)}\n\n{status}",
        reply_markup=payroll_main_keyboard(manager=manager),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ПРОВЕРКА ЗП
# ============================================================


async def check_salary_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    if is_manager(employee):
        await query.edit_message_text(
            "Выберите сотрудника для проверки ЗП:",
            reply_markup=employees_keyboard("salemp"),
        )
        return SALARY_EMPLOYEE

    text = build_personal_salary_text(employee)
    await query.edit_message_text(text, reply_markup=payroll_main_keyboard(manager=False))
    return ConversationHandler.END


async def salary_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("salemp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_main_keyboard(manager=True))
        return ConversationHandler.END

    text = build_personal_salary_text(employee, show_bonus_details=True)
    await query.edit_message_text(text, reply_markup=payroll_main_keyboard(manager=True))
    return ConversationHandler.END


# ============================================================
# ПРЕМИАЛЬНЫЕ
# ============================================================


def format_bonus_line(bonus, index=None):
    prefix = f"{index}. " if index is not None else ""
    comment = str(bonus.get("comment", "") or "—")
    if len(comment) > 80:
        comment = comment[:77] + "..."
    return (
        f"{prefix}{bonus['date']} — {bonus['full_name']} — {money(bonus['amount'])}\n"
        f"Комментарий: {comment}\n"
        f"Назначил: {bonus.get('assigned_by', '—') or '—'}"
    )


def bonus_delete_keyboard(bonuses):
    rows = []
    for index, bonus in enumerate(bonuses, start=1):
        text = f"{index}. {bonus['date']} — {bonus['full_name']} — {money(bonus['amount'])}"
        rows.append([InlineKeyboardButton(text[:60], callback_data=f"bonusdel:{bonus['bonus_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="pay:bonuses")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def bonus_delete_confirm_keyboard(bonus_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, удалить", callback_data=f"bonusdelconfirm:{bonus_id}")],
            [InlineKeyboardButton("⬅️ Назад к списку", callback_data="bonus:delete")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


async def bonuses_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    await query.edit_message_text("Премиальные", reply_markup=bonuses_keyboard())
    return ConversationHandler.END


async def bonus_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    await query.edit_message_text("Выберите сотрудника для премиальных:", reply_markup=employees_keyboard("bnemp"))
    return BONUS_EMPLOYEE


async def bonus_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee_id = query.data.replace("bnemp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return BONUS_EMPLOYEE

    context.user_data["employee_id"] = employee_id
    await query.edit_message_text("Введите дату премии в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return BONUS_DATE


async def bonus_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bonus_date = update.message.text.strip()
    if not validate_date(bonus_date):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return BONUS_DATE

    context.user_data["bonus_date"] = bonus_date
    await update.message.reply_text("Введите сумму премии:", reply_markup=payroll_back_keyboard())
    return BONUS_AMOUNT


async def bonus_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_positive_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Введите сумму числом больше 0:")
        return BONUS_AMOUNT

    context.user_data["bonus_amount"] = amount
    await update.message.reply_text("Введите комментарий, за что премия:", reply_markup=payroll_back_keyboard())
    return BONUS_COMMENT


async def bonus_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if not comment:
        await update.message.reply_text("Комментарий не должен быть пустым. Введите комментарий:")
        return BONUS_COMMENT

    employee = get_employee_by_id(context.user_data.get("employee_id"))
    current_employee = current_employee_or_none(update)
    if not employee or not is_manager(current_employee):
        await update.message.reply_text("Недостаточно прав или сотрудник не найден.")
        context.user_data.clear()
        return ConversationHandler.END

    bonus_id = append_bonus(
        employee=employee,
        bonus_date=context.user_data["bonus_date"],
        comment=comment,
        amount=context.user_data["bonus_amount"],
        assigned_by=current_employee["full_name"],
    )

    await update.message.reply_text(
        "Премия добавлена ✅\n\n"
        f"ID: {bonus_id}\n"
        f"Сотрудник: {employee['full_name']}\n"
        f"Дата: {context.user_data['bonus_date']}\n"
        f"Сумма: {money(context.user_data['bonus_amount'])}\n"
        f"Комментарий: {comment}",
        reply_markup=payroll_main_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def bonus_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    bonuses = list_bonuses_in_period(limit=10)
    if not bonuses:
        await query.edit_message_text("Премиальных пока нет.", reply_markup=bonuses_keyboard())
        return ConversationHandler.END

    total = sum(bonus["amount"] for bonus in bonuses)
    lines = ["Последние 10 записей премиальных", f"Итого в списке: {money(total)}", ""]
    for index, bonus in enumerate(bonuses, start=1):
        lines.append(format_bonus_line(bonus, index=index))
        lines.append("")

    await query.edit_message_text("\n".join(lines).strip(), reply_markup=bonuses_keyboard())
    return ConversationHandler.END


async def bonus_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    bonuses = list_bonuses_in_period(limit=10)
    if not bonuses:
        await query.edit_message_text("Премиальных пока нет.", reply_markup=bonuses_keyboard())
        return ConversationHandler.END

    context.user_data["bonus_delete_items"] = {bonus["bonus_id"]: bonus for bonus in bonuses}
    await query.edit_message_text(
        "Выберите запись премиальных для удаления:",
        reply_markup=bonus_delete_keyboard(bonuses),
    )
    return BONUS_DELETE_SELECT


async def bonus_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bonus_id = query.data.replace("bonusdel:", "")
    bonus = (context.user_data.get("bonus_delete_items") or {}).get(bonus_id)
    if not bonus:
        await query.edit_message_text("Запись не найдена в текущем списке.", reply_markup=bonuses_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(
        "Удалить эту запись премиальных?\n\n" + format_bonus_line(bonus),
        reply_markup=bonus_delete_confirm_keyboard(bonus_id),
    )
    return BONUS_DELETE_SELECT


async def bonus_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    bonus_id = query.data.replace("bonusdelconfirm:", "")
    deleted = delete_bonus(bonus_id)

    if not deleted:
        await query.edit_message_text("Запись не найдена или уже удалена.", reply_markup=bonuses_keyboard())
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text(
        "Запись премиальных удалена ✅\n\n" + format_bonus_line(deleted),
        reply_markup=bonuses_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


def format_vacation_line(vacation, index=None):
    prefix = f"{index}. " if index else ""
    return (
        f"{prefix}{vacation['employee_name']}\n"
        f"{vacation['start_date']} — {vacation['end_date']} · {vacation['days']} дн.\n"
        f"Ставка: {money(vacation['hourly_rate'])}; отпускные: {money(vacation['amount'])}"
    )


def ensure_vacation_manager(update):
    return is_manager(current_employee_or_none(update))


async def vacations_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    await query.edit_message_text("🏖 Отпускные", reply_markup=vacations_keyboard())
    return ConversationHandler.END


async def vacation_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите сотрудника для отпуска:",
        reply_markup=employees_keyboard("vacemp"),
    )
    return VACATION_EMPLOYEE


async def vacation_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee = get_employee_by_id(query.data.replace("vacemp:", ""))
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    context.user_data["vacation_employee_id"] = employee["employee_id"]
    await query.edit_message_text(
        "Введите дату начала отпуска в формате ДД.ММ.ГГГГ:",
        reply_markup=payroll_back_keyboard(),
    )
    return VACATION_START


async def vacation_start_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    if not validate_date(value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return VACATION_START
    context.user_data["vacation_start"] = value
    await update.message.reply_text(
        "Введите дату окончания отпуска в формате ДД.ММ.ГГГГ:",
        reply_markup=payroll_back_keyboard(),
    )
    return VACATION_END


async def vacation_end_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    if not validate_date(value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return VACATION_END
    employee = get_employee_by_id(context.user_data.get("vacation_employee_id"))
    current_employee = current_employee_or_none(update)
    if not employee or not is_manager(current_employee):
        await update.message.reply_text("Недостаточно прав или сотрудник не найден.")
        context.user_data.clear()
        return ConversationHandler.END
    try:
        vacation = create_vacation(
            employee,
            context.user_data["vacation_start"],
            value,
            created_by=current_employee["full_name"],
        )
    except VacationValidationError as error:
        await update.message.reply_text(str(error), reply_markup=payroll_back_keyboard())
        return VACATION_END
    context.user_data.clear()
    await update.message.reply_text(
        "Отпускные добавлены ✅\n\n" + format_vacation_line(vacation),
        reply_markup=vacations_keyboard(),
    )
    return ConversationHandler.END


async def vacation_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    vacations = list_vacations()[-10:]
    if not vacations:
        await query.edit_message_text("Записей отпускных пока нет.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    lines = ["Последние записи отпускных", ""]
    for index, vacation in enumerate(vacations, start=1):
        lines.extend([format_vacation_line(vacation, index=index), ""])
    await query.edit_message_text("\n".join(lines).strip(), reply_markup=vacations_keyboard())
    return ConversationHandler.END


async def vacation_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    vacations = list_vacations()
    if not vacations:
        await query.edit_message_text("Записей отпускных пока нет.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите отпуск для изменения периода:",
        reply_markup=vacations_select_keyboard(vacations, "vacationedit"),
    )
    return VACATION_EDIT_SELECT


async def vacation_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vacation = get_vacation(query.data.replace("vacationedit:", ""))
    if not vacation:
        await query.edit_message_text("Запись не найдена.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    context.user_data["vacation_edit_id"] = vacation["vacation_id"]
    await query.edit_message_text(
        "Введите новую дату начала отпуска в формате ДД.ММ.ГГГГ:",
        reply_markup=payroll_back_keyboard(),
    )
    return VACATION_EDIT_START


async def vacation_edit_start_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    if not validate_date(value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return VACATION_EDIT_START
    context.user_data["vacation_edit_start"] = value
    await update.message.reply_text(
        "Введите новую дату окончания отпуска в формате ДД.ММ.ГГГГ:",
        reply_markup=payroll_back_keyboard(),
    )
    return VACATION_EDIT_END


async def vacation_edit_end_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    if not validate_date(value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return VACATION_EDIT_END
    if not ensure_vacation_manager(update):
        await update.message.reply_text("Недостаточно прав.")
        return ConversationHandler.END
    try:
        vacation = update_vacation_period(
            context.user_data.get("vacation_edit_id"),
            context.user_data["vacation_edit_start"],
            value,
        )
    except VacationValidationError as error:
        await update.message.reply_text(str(error), reply_markup=payroll_back_keyboard())
        return VACATION_EDIT_END
    context.user_data.clear()
    if not vacation:
        await update.message.reply_text("Запись не найдена.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(
        "Период отпуска обновлён ✅\n\n" + format_vacation_line(vacation),
        reply_markup=vacations_keyboard(),
    )
    return ConversationHandler.END


async def vacation_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    vacations = list_vacations()
    if not vacations:
        await query.edit_message_text("Записей отпускных пока нет.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите отпуск для удаления:",
        reply_markup=vacations_select_keyboard(vacations, "vacationdel"),
    )
    return VACATION_DELETE_SELECT


async def vacation_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    vacation = get_vacation(query.data.replace("vacationdel:", ""))
    if not vacation:
        await query.edit_message_text("Запись не найдена.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "Удалить эту запись?\n\n" + format_vacation_line(vacation),
        reply_markup=vacation_delete_confirm_keyboard(vacation["vacation_id"]),
    )
    return VACATION_DELETE_SELECT


async def vacation_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_vacation_manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    vacation = delete_vacation(query.data.replace("vacationdelconfirm:", ""))
    if not vacation:
        await query.edit_message_text("Запись не найдена.", reply_markup=vacations_keyboard())
        return ConversationHandler.END
    await query.edit_message_text(
        "Отпускные удалены ✅\n\n" + format_vacation_line(vacation),
        reply_markup=vacations_keyboard(),
    )
    return ConversationHandler.END


# ============================================================
# РАСХОДЫ
# ============================================================


def format_expense_line(expense, index=None):
    prefix = f"{index}. " if index is not None else ""
    comment = str(expense.get("comment", "") or "—")
    if len(comment) > 80:
        comment = comment[:77] + "..."
    return (
        f"{prefix}{expense['date']} — {expense['full_name']} — {money(expense['amount'])}\n"
        f"Комментарий: {comment}"
    )


def expense_delete_keyboard(expenses):
    rows = []
    for index, expense in enumerate(expenses, start=1):
        text = f"{index}. {expense['date']} — {expense['full_name']} — {money(expense['amount'])}"
        rows.append([InlineKeyboardButton(text[:60], callback_data=f"expdel:{expense['expense_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="pay:expenses")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")])
    return InlineKeyboardMarkup(rows)


def expense_delete_confirm_keyboard(expense_id):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Да, удалить", callback_data=f"expdelconfirm:{expense_id}")],
            [InlineKeyboardButton("⬅️ Назад к списку", callback_data="expense:delete")],
            [InlineKeyboardButton("❌ Отмена", callback_data="pay:cancel")],
        ]
    )


def expenses_for_current_period(employee):
    period = get_active_period()
    if not period:
        return None, []

    employee_id = None if is_manager(employee) else employee["employee_id"]
    expenses = list_expenses_in_period(period["start_date"], period["end_date"], employee_id=employee_id)
    return period, expenses


async def expenses_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    await query.edit_message_text("Расходы", reply_markup=expenses_keyboard())
    return ConversationHandler.END


async def expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    if is_manager(employee):
        await query.edit_message_text("Выберите сотрудника для расхода:", reply_markup=employees_keyboard("exemp"))
        return EXPENSE_EMPLOYEE

    context.user_data["employee_id"] = employee["employee_id"]
    await query.edit_message_text("Введите дату расхода в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return EXPENSE_DATE


async def expense_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("exemp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return EXPENSE_EMPLOYEE
    context.user_data["employee_id"] = employee_id
    await query.edit_message_text("Введите дату расхода в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return EXPENSE_DATE


async def expense_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expense_date = update.message.text.strip()
    if not validate_date(expense_date):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return EXPENSE_DATE
    context.user_data["expense_date"] = expense_date
    await update.message.reply_text("Введите комментарий к расходу:", reply_markup=payroll_back_keyboard())
    return EXPENSE_COMMENT


async def expense_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if not comment:
        await update.message.reply_text("Комментарий не должен быть пустым. Введите комментарий:")
        return EXPENSE_COMMENT
    context.user_data["expense_comment"] = comment
    await update.message.reply_text("Введите сумму расхода:", reply_markup=payroll_back_keyboard())
    return EXPENSE_AMOUNT


async def expense_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_positive_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Введите сумму числом больше 0:")
        return EXPENSE_AMOUNT

    employee = get_employee_by_id(context.user_data.get("employee_id"))
    current_employee = current_employee_or_none(update)
    append_expense(
        employee,
        context.user_data["expense_date"],
        context.user_data["expense_comment"],
        amount,
        current_employee["full_name"] if current_employee else str(update.effective_user.id),
    )
    await update.message.reply_text(
        f"Расход добавлен ✅\n\nСотрудник: {employee['full_name']}\nДата: {context.user_data['expense_date']}\nСумма: {money(amount)}",
        reply_markup=payroll_main_keyboard(manager=is_manager(current_employee)),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def expense_view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    period, expenses = expenses_for_current_period(employee)
    if not period:
        await query.edit_message_text(
            "Активный расчетный период не настроен.",
            reply_markup=expenses_keyboard(),
        )
        return ConversationHandler.END

    if not expenses:
        await query.edit_message_text(
            f"Расходов за период {period['start_date']} — {period['end_date']} нет.",
            reply_markup=expenses_keyboard(),
        )
        return ConversationHandler.END

    total = sum(expense["amount"] for expense in expenses)
    lines = [
        f"Расходы за период {period['start_date']} — {period['end_date']}",
        f"Итого: {money(total)}",
        "",
    ]
    for index, expense in enumerate(expenses, start=1):
        lines.append(format_expense_line(expense, index=index))
        lines.append("")

    text = "\n".join(lines).strip()
    if len(text) <= 3900:
        await query.edit_message_text(text, reply_markup=expenses_keyboard())
    else:
        await query.edit_message_text("Список расходов длинный. Отправляю частями...", reply_markup=expenses_keyboard())
        for chunk in split_long_message(text):
            await query.message.reply_text(chunk)
    return ConversationHandler.END


async def expense_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    if not employee:
        await deny_unknown_user(update)
        return ConversationHandler.END

    period, expenses = expenses_for_current_period(employee)
    if not period:
        await query.edit_message_text(
            "Активный расчетный период не настроен.",
            reply_markup=expenses_keyboard(),
        )
        return ConversationHandler.END

    if not expenses:
        await query.edit_message_text(
            f"Расходов за период {period['start_date']} — {period['end_date']} нет.",
            reply_markup=expenses_keyboard(),
        )
        return ConversationHandler.END

    context.user_data["expense_delete_allowed_employee_id"] = None if is_manager(employee) else employee["employee_id"]
    context.user_data["expense_delete_items"] = {expense["expense_id"]: expense for expense in expenses}
    await query.edit_message_text(
        f"Выберите расход для удаления за период {period['start_date']} — {period['end_date']}:",
        reply_markup=expense_delete_keyboard(expenses),
    )
    return EXPENSE_DELETE_SELECT


async def expense_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    expense_id = query.data.replace("expdel:", "")
    expense = (context.user_data.get("expense_delete_items") or {}).get(expense_id)
    if not expense:
        await query.edit_message_text("Расход не найден в текущем списке.", reply_markup=expenses_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(
        "Удалить этот расход?\n\n" + format_expense_line(expense),
        reply_markup=expense_delete_confirm_keyboard(expense_id),
    )
    return EXPENSE_DELETE_SELECT


async def expense_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    expense_id = query.data.replace("expdelconfirm:", "")
    employee_id = context.user_data.get("expense_delete_allowed_employee_id")
    deleted = delete_expense(expense_id, employee_id=employee_id)

    if not deleted:
        await query.edit_message_text("Расход не найден или уже удален.", reply_markup=expenses_keyboard())
        context.user_data.clear()
        return ConversationHandler.END

    await query.edit_message_text(
        "Расход удален ✅\n\n" + format_expense_line(deleted),
        reply_markup=expenses_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ШТРАФЫ
# ============================================================


async def penalty_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    await query.edit_message_text("Выберите сотрудника для штрафа:", reply_markup=employees_keyboard("pnemp"))
    return PENALTY_EMPLOYEE


async def penalty_employee_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    employee_id = query.data.replace("pnemp:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=payroll_back_keyboard())
        return PENALTY_EMPLOYEE

    context.user_data["employee_id"] = employee_id
    await query.edit_message_text("Введите дату штрафа в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return PENALTY_DATE


async def penalty_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_value = update.message.text.strip()
    if not validate_date(date_value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return PENALTY_DATE

    context.user_data["penalty_date"] = date_value
    await update.message.reply_text("Выберите тип штрафа:", reply_markup=penalty_type_group_keyboard())
    return PENALTY_TYPE_GROUP


async def penalty_type_group_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "pngrp:back":
        await query.edit_message_text("Выберите тип штрафа:", reply_markup=penalty_type_group_keyboard())
        return PENALTY_TYPE_GROUP

    if data.startswith("pngrp:"):
        group_id = data.replace("pngrp:", "")
        group = PENALTY_TYPE_GROUPS.get(group_id)
        if not group:
            await query.edit_message_text("Группа штрафов не найдена.", reply_markup=penalty_type_group_keyboard())
            return PENALTY_TYPE_GROUP

        await query.edit_message_text(
            f"Тип штрафа: {group['name']}\n\nВыберите конкретный вариант:",
            reply_markup=penalty_type_keyboard(group_id),
        )
        return PENALTY_TYPE

    if data.startswith("pntype:"):
        return await penalty_type_selected(update, context)

    await query.edit_message_text("Выберите тип штрафа:", reply_markup=penalty_type_group_keyboard())
    return PENALTY_TYPE_GROUP


async def penalty_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "pngrp:back":
        await query.edit_message_text("Выберите тип штрафа:", reply_markup=penalty_type_group_keyboard())
        return PENALTY_TYPE_GROUP

    penalty_type_id = data.replace("pntype:", "")
    penalty_type = PENALTY_TYPES.get(penalty_type_id)

    if not penalty_type:
        await query.edit_message_text("Тип штрафа не найден. Выберите заново:", reply_markup=penalty_type_group_keyboard())
        return PENALTY_TYPE_GROUP

    context.user_data["penalty_type_id"] = penalty_type_id
    context.user_data["penalty_category"] = penalty_type.get("category", "Другое")
    context.user_data["penalty_type_name"] = penalty_type["name"]
    context.user_data["penalty_manual_amount"] = bool(penalty_type.get("manual_amount"))

    if not penalty_type.get("manual_amount"):
        context.user_data["penalty_amount"] = safe_float(penalty_type.get("amount"))
    else:
        context.user_data.pop("penalty_amount", None)

    await query.edit_message_text(
        f"Тип штрафа: {penalty_type['name']}\n\nВведите комментарий с деталями штрафа:",
        reply_markup=payroll_back_keyboard(),
    )
    return PENALTY_COMMENT


async def penalty_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()
    if not comment:
        await update.message.reply_text("Комментарий не должен быть пустым. Введите комментарий:")
        return PENALTY_COMMENT

    context.user_data["penalty_comment"] = comment

    if context.user_data.get("penalty_manual_amount"):
        await update.message.reply_text("Введите сумму штрафа:", reply_markup=payroll_back_keyboard())
        return PENALTY_AMOUNT

    await update.message.reply_text(
        "Если есть фото-доказательства ошибки, отправьте их сюда.\n\n"
        "Можно отправить несколько фото по одному или нажать «Без фото».",
        reply_markup=penalty_photos_keyboard(has_photos=False),
    )
    return PENALTY_PHOTOS


async def penalty_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_positive_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Введите сумму числом больше 0:")
        return PENALTY_AMOUNT

    context.user_data["penalty_amount"] = amount

    await update.message.reply_text(
        "Если есть фото-доказательства ошибки, отправьте их сюда.\n\n"
        "Можно отправить несколько фото по одному или нажать «Без фото».",
        reply_markup=penalty_photos_keyboard(has_photos=False),
    )
    return PENALTY_PHOTOS


def build_penalty_preview_from_context(context):
    employee = get_employee_by_id(context.user_data.get("employee_id"))
    photo_file_ids = context.user_data.get("penalty_photo_file_ids", [])
    return format_penalty_preview(
        employee=employee,
        penalty_date=context.user_data["penalty_date"],
        penalty_category=context.user_data["penalty_category"],
        penalty_type=context.user_data["penalty_type_name"],
        comment=context.user_data["penalty_comment"],
        amount=context.user_data["penalty_amount"],
        photo_count=len(photo_file_ids),
    )


async def penalty_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "Отправьте фото-доказательство или нажмите «Без фото».",
            reply_markup=penalty_photos_keyboard(has_photos=bool(context.user_data.get("penalty_photo_file_ids"))),
        )
        return PENALTY_PHOTOS

    photo_file_ids = context.user_data.setdefault("penalty_photo_file_ids", [])
    photo_file_ids.append(update.message.photo[-1].file_id)

    await update.message.reply_text(
        f"Фото добавлено ✅ Всего фото: {len(photo_file_ids)}\n\n"
        "Отправьте еще фото или нажмите «Готово».",
        reply_markup=penalty_photos_keyboard(has_photos=True),
    )
    return PENALTY_PHOTOS


async def penalty_photos_finished(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "penaltyphotos:skip":
        context.user_data["penalty_photo_file_ids"] = []

    await query.edit_message_text(
        build_penalty_preview_from_context(context),
        reply_markup=penalty_confirm_keyboard(),
    )
    return PENALTY_CONFIRM


async def penalty_confirm_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee = get_employee_by_id(context.user_data.get("employee_id"))
    current_employee = current_employee_or_none(update)
    created_by = current_employee["full_name"] if current_employee else str(update.effective_user.id)
    penalty_date = context.user_data["penalty_date"]
    penalty_category = context.user_data["penalty_category"]
    penalty_type_id = context.user_data.get("penalty_type_id")
    penalty_type = context.user_data["penalty_type_name"]
    penalty_comment = context.user_data["penalty_comment"]
    amount = context.user_data["penalty_amount"]
    photo_file_ids = context.user_data.get("penalty_photo_file_ids", [])

    append_penalty(
        employee,
        penalty_date,
        penalty_category,
        penalty_type,
        penalty_comment,
        amount,
        created_by,
    )

    try:
        topic_status = await send_penalty_to_topic(
            context=context,
            employee=employee,
            penalty_date=penalty_date,
            penalty_category=penalty_category,
            penalty_type=penalty_type,
            comment=penalty_comment,
            amount=amount,
            created_by=created_by,
            photo_file_ids=photo_file_ids,
        )
    except Exception as error:
        logging.exception("Не удалось отправить штраф в тему Telegram")
        topic_status = f"Штраф записан в таблицу, но не отправлен в тему ⚠️\nОшибка: {error}"

    auto_status = None
    if penalty_type_id == PENALTY_ABSENCE_NO_REASON_TYPE_ID:
        auto_status = await maybe_send_third_absence_notice(
            context=context,
            employee=employee,
            penalty_date=penalty_date,
            created_by=created_by,
        )

    extra_status = f"\n\n{auto_status}" if auto_status else ""

    await query.edit_message_text(
        "Штраф добавлен ✅\n\n"
        f"Сотрудник: {employee['full_name']}\n"
        f"Дата: {penalty_date}\n"
        f"Категория: {penalty_category}\n"
        f"Тип штрафа: {penalty_type}\n"
        f"Комментарий: {penalty_comment}\n"
        f"Сумма: {money(amount)}\n\n"
        f"Фото-доказательства: {len(photo_file_ids) if photo_file_ids else 'нет'}\n\n"
        f"{topic_status}{extra_status}",
        reply_markup=payroll_main_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END

# ============================================================
# РАСЧЕТНЫЕ ПЕРИОДЫ
# ============================================================


async def periods_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    active = get_active_period()
    active_text = "Активный период не настроен."
    if active:
        active_text = (
            f"Активный период:\n{active['name']}\n{active['start_date']} — {active['end_date']}\n"
            f"Режим оплаты: {payment_mode_label(active.get('payment_mode'))}"
        )
    await query.edit_message_text(active_text, reply_markup=periods_keyboard())
    return ConversationHandler.END


async def period_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["period_action"] = "create"
    await query.edit_message_text("Введите название расчетного периода:", reply_markup=payroll_back_keyboard())
    return PERIOD_NAME


async def period_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Введите название:")
        return PERIOD_NAME
    context.user_data["period_name"] = name
    await update.message.reply_text("Введите дату начала периода в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return PERIOD_START


async def period_start_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_value = update.message.text.strip()
    if not validate_date(date_value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return PERIOD_START
    context.user_data["period_start"] = date_value
    await update.message.reply_text("Введите дату конца периода в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    return PERIOD_END


async def period_end_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_value = update.message.text.strip()
    if not validate_date(date_value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return PERIOD_END

    name = context.user_data["period_name"]
    start_date = context.user_data["period_start"]
    end_date = date_value

    context.user_data["period_end"] = end_date
    await update.message.reply_text(
        "Как будет производиться оплата в этом расчетном периоде?",
        reply_markup=period_payment_mode_keyboard("periodpay"),
    )
    return PERIOD_PAYMENT_MODE


async def period_payment_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    payment_mode = query.data.replace("periodpay:", "")
    if payment_mode not in {PAYMENT_MODE_HOURLY, PAYMENT_MODE_SHIFT}:
        await query.edit_message_text(
            "Неизвестный режим оплаты. Выберите заново:",
            reply_markup=period_payment_mode_keyboard("periodpay"),
        )
        return PERIOD_PAYMENT_MODE

    name = context.user_data["period_name"]
    start_date = context.user_data["period_start"]
    end_date = context.user_data["period_end"]

    create_active_period(name, start_date, end_date, current_employee["full_name"], payment_mode)
    await query.edit_message_text(
        f"Активный расчетный период создан ✅\n\n"
        f"{name}\n{start_date} — {end_date}\n"
        f"Режим оплаты: {payment_mode_label(payment_mode)}",
        reply_markup=payroll_main_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def period_edit_payment_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payment_mode = query.data.replace("periodeditpay:", "")
    if payment_mode not in {PAYMENT_MODE_HOURLY, PAYMENT_MODE_SHIFT}:
        await query.edit_message_text(
            "Неизвестный режим оплаты. Выберите заново:",
            reply_markup=period_payment_mode_keyboard("periodeditpay"),
        )
        return PERIOD_EDIT_VALUE

    updated = update_active_period(payment_mode=payment_mode)
    if not updated:
        await query.edit_message_text("Активный период не найден.", reply_markup=payroll_main_keyboard(manager=True))
        return ConversationHandler.END

    active = get_active_period()
    await query.edit_message_text(
        f"Период обновлен ✅\n\n"
        f"{active['name']}\n{active['start_date']} — {active['end_date']}\n"
        f"Режим оплаты: {payment_mode_label(active.get('payment_mode'))}",
        reply_markup=payroll_main_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def period_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    active = get_active_period()
    if not active:
        await query.edit_message_text("Активный период не настроен.", reply_markup=periods_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(
        f"Текущий период:\n{active['name']}\n{active['start_date']} — {active['end_date']}\n"
        f"Режим оплаты: {payment_mode_label(active.get('payment_mode'))}\n\nЧто изменить?",
        reply_markup=period_edit_field_keyboard(),
    )
    return PERIOD_EDIT_FIELD


async def period_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.replace("periodfield:", "")
    context.user_data["period_edit_field"] = field

    if field == "name":
        await query.edit_message_text("Введите новое название периода:", reply_markup=payroll_back_keyboard())
    elif field == "start":
        await query.edit_message_text("Введите новую дату начала в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    elif field == "end":
        await query.edit_message_text("Введите новую дату конца в формате ДД.ММ.ГГГГ:", reply_markup=payroll_back_keyboard())
    elif field == "payment_mode":
        await query.edit_message_text(
            "Выберите новый режим оплаты:",
            reply_markup=period_payment_mode_keyboard("periodeditpay"),
        )
        return PERIOD_EDIT_VALUE
    else:
        await query.edit_message_text("Неизвестное поле.", reply_markup=period_edit_field_keyboard())
        return PERIOD_EDIT_FIELD
    return PERIOD_EDIT_VALUE


async def period_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    field = context.user_data.get("period_edit_field")
    if not value:
        await update.message.reply_text("Значение не должно быть пустым. Введите заново:")
        return PERIOD_EDIT_VALUE

    if field in {"start", "end"} and not validate_date(value):
        await update.message.reply_text("Неверный формат даты. Введите ДД.ММ.ГГГГ:")
        return PERIOD_EDIT_VALUE

    kwargs = {}
    if field == "name":
        kwargs["name"] = value
    elif field == "start":
        kwargs["start_date"] = value
    elif field == "end":
        kwargs["end_date"] = value

    updated = update_active_period(**kwargs)
    if not updated:
        await update.message.reply_text("Активный период не найден.", reply_markup=payroll_main_keyboard(manager=True))
        return ConversationHandler.END

    active = get_active_period()
    await update.message.reply_text(
        f"Период обновлен ✅\n\n"
        f"{active['name']}\n{active['start_date']} — {active['end_date']}\n"
        f"Режим оплаты: {payment_mode_label(active.get('payment_mode'))}",
        reply_markup=payroll_main_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def period_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    periods = get_periods()
    if not periods:
        await query.edit_message_text("Расчетные периоды пока не созданы.", reply_markup=periods_keyboard())
        return ConversationHandler.END

    lines = ["Расчетные периоды:"]
    for period in periods[-15:]:
        status = "активный" if period["status"] == "active" else "закрытый"
        lines.append(
            f"\n{period['name']}\n{period['start_date']} — {period['end_date']}\n"
            f"Режим оплаты: {payment_mode_label(period.get('payment_mode'))}\n"
            f"Статус: {status}"
        )
    await query.edit_message_text("\n".join(lines), reply_markup=periods_keyboard())
    return ConversationHandler.END


# ============================================================
# РАСЧЕТ ЗП ЗА ПЕРИОД + PDF
# ============================================================




def split_long_message(text, limit=3900):
    return summary_chunks(text, limit=limit)


async def send_payroll_for_period(query, context: ContextTypes.DEFAULT_TYPE, period):
    text = build_full_payroll_text(period)

    if len(text) <= 3900:
        await query.edit_message_text(text, reply_markup=payroll_main_keyboard(manager=True))
    else:
        await query.edit_message_text(
            "Отчет получился длинным. Отправляю частями и PDF...",
            reply_markup=payroll_main_keyboard(manager=True),
        )
        for chunk in split_long_message(text):
            await context.bot.send_message(chat_id=query.message.chat_id, text=chunk)

    try:
        filename = f"payroll_{period['start_date'].replace('.', '-')}_{period['end_date'].replace('.', '-')}.pdf"
        pdf_path = create_payroll_pdf(text, filename=filename)
        with open(pdf_path, "rb") as file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file,
                filename=filename,
                caption="PDF ведомость ЗП",
            )
    except Exception as error:
        logging.exception("Не удалось создать PDF ведомость")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"PDF не удалось создать ⚠️\nОшибка: {error}",
        )


async def calculate_period_payroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    periods = get_periods()
    if not periods:
        await query.edit_message_text(
            "Расчетные периоды не настроены. Создайте период в разделе «Расчетные периоды».",
            reply_markup=payroll_main_keyboard(manager=True),
        )
        return ConversationHandler.END

    context.user_data["payroll_periods"] = {period["period_id"]: period for period in periods}
    await query.edit_message_text(
        "Выберите расчетный период для расчета ЗП:",
        reply_markup=payroll_periods_keyboard(periods),
    )
    return PAYROLL_PERIOD_SELECT


async def payroll_period_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    period_id = query.data.replace("payperiod:", "")
    period = (context.user_data.get("payroll_periods") or {}).get(period_id)
    if not period:
        period = next((item for item in get_periods() if item["period_id"] == period_id), None)

    if not period:
        await query.edit_message_text(
            "Расчетный период не найден. Попробуйте выбрать период заново.",
            reply_markup=payroll_main_keyboard(manager=True),
        )
        context.user_data.clear()
        return ConversationHandler.END

    await send_payroll_for_period(query, context, period)
    context.user_data.clear()
    return ConversationHandler.END


# ============================================================
# ОЧИСТКА СТАРЫХ ДАННЫХ
# ============================================================


async def cleanup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current_employee = current_employee_or_none(update)
    if not is_manager(current_employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    await query.edit_message_text(
        "Удалить данные старше 1 года из разделов «Ежедневные отчеты», «Расходы», «Штрафы», "
        "«Премиальные», «Дополнительные начисления»?\n\n"
        "Справочники сотрудников, KPI и расчетные периоды не будут удалены.",
        reply_markup=cleanup_confirm_keyboard(),
    )
    return CLEANUP_CONFIRM


async def cleanup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    result = cleanup_old_operational_data(days=365)
    lines = ["Очистка завершена ✅"]
    for sheet_name, count in result.items():
        lines.append(f"{sheet_name}: удалено строк — {count}")

    await query.edit_message_text("\n".join(lines), reply_markup=payroll_main_keyboard(manager=True))
    return ConversationHandler.END


# ============================================================
# HANDLERS
# ============================================================


def get_payroll_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(create_report_start, pattern=r"^pay:create_report$"),
            CallbackQueryHandler(manager_only_report_start, pattern=r"^pay:manager_report$"),
            CallbackQueryHandler(edit_report_start, pattern=r"^pay:edit_report$"),
            CallbackQueryHandler(check_salary_start, pattern=r"^pay:check_salary$"),
            CallbackQueryHandler(expenses_menu_start, pattern=r"^pay:expenses$"),
            CallbackQueryHandler(expense_start, pattern=r"^(pay:add_expense|expense:add)$"),
            CallbackQueryHandler(expense_view_start, pattern=r"^expense:view$"),
            CallbackQueryHandler(expense_delete_start, pattern=r"^expense:delete$"),
            CallbackQueryHandler(penalty_start, pattern=r"^pay:add_penalty$"),
            CallbackQueryHandler(bonuses_menu_start, pattern=r"^pay:bonuses$"),
            CallbackQueryHandler(bonus_start, pattern=r"^bonus:add$"),
            CallbackQueryHandler(bonus_view_start, pattern=r"^bonus:view$"),
            CallbackQueryHandler(bonus_delete_start, pattern=r"^bonus:delete$"),
            CallbackQueryHandler(vacations_menu_start, pattern=r"^pay:vacations$"),
            CallbackQueryHandler(vacation_create_start, pattern=r"^vacation:add$"),
            CallbackQueryHandler(vacation_view_start, pattern=r"^vacation:view$"),
            CallbackQueryHandler(vacation_edit_start, pattern=r"^vacation:edit$"),
            CallbackQueryHandler(vacation_delete_start, pattern=r"^vacation:delete$"),
            CallbackQueryHandler(periods_menu_start, pattern=r"^pay:periods$"),
            CallbackQueryHandler(period_create_start, pattern=r"^period:create$"),
            CallbackQueryHandler(period_edit_start, pattern=r"^period:edit$"),
            CallbackQueryHandler(period_list, pattern=r"^period:list$"),
            CallbackQueryHandler(calculate_period_payroll, pattern=r"^pay:calculate_period$"),
            CallbackQueryHandler(cleanup_start, pattern=r"^pay:cleanup$"),
        ],
        states={
            CREATE_MANAGER_DATE: [
                CallbackQueryHandler(manager_only_report_date_selected, pattern=r"^mgrdate:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_EMPLOYEE: [
                CallbackQueryHandler(create_employee_selected, pattern=r"^cremp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_DATE: [
                CallbackQueryHandler(create_date_selected, pattern=r"^crdate:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_SHIFT_TYPE: [
                CallbackQueryHandler(create_shift_type_selected, pattern=r"^crshift:(full|half)$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_INTERVAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_interval_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_LUNCH: [
                CallbackQueryHandler(create_lunch_selected, pattern=r"^crlunch:(0\.5|1)$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_TASKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_tasks_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_KPI_SELECT: [
                CallbackQueryHandler(create_kpi_selected, pattern=r"^crkpi:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_KPI_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_kpi_qty_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CREATE_MANAGER_VOLUMES: [
                CallbackQueryHandler(manager_report_wizard_callback, pattern=r"^mgrwiz:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, manager_report_wizard_text_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_EMPLOYEE: [
                CallbackQueryHandler(edit_employee_selected, pattern=r"^edemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_DATE: [
                CallbackQueryHandler(edit_date_selected, pattern=r"^eddate:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_FIELD: [
                CallbackQueryHandler(edit_report_date_selected, pattern=r"^editnewdate:"),
                CallbackQueryHandler(edit_report_delete_confirmed, pattern=r"^editdelete:confirm$"),
                CallbackQueryHandler(edit_tasks_mode_selected, pattern=r"^edittasks:"),
                CallbackQueryHandler(edit_kpi_mode_selected, pattern=r"^editkpi:"),
                CallbackQueryHandler(edit_lunch_selected, pattern=r"^editlunch:(0\.5|1)$"),
                CallbackQueryHandler(edit_field_selected, pattern=r"^editfield:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_SHIFT_TYPE: [
                CallbackQueryHandler(edit_shift_type_selected, pattern=r"^edshift:(full|half)$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_KPI_SELECT: [
                CallbackQueryHandler(edit_kpi_selected, pattern=r"^edkpi:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EDIT_KPI_QTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_kpi_qty_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            SALARY_EMPLOYEE: [
                CallbackQueryHandler(salary_employee_selected, pattern=r"^salemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EXPENSE_EMPLOYEE: [
                CallbackQueryHandler(expense_employee_selected, pattern=r"^exemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EXPENSE_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_date_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EXPENSE_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_comment_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EXPENSE_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, expense_amount_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            EXPENSE_DELETE_SELECT: [
                CallbackQueryHandler(expense_delete_selected, pattern=r"^expdel:"),
                CallbackQueryHandler(expense_delete_confirmed, pattern=r"^expdelconfirm:"),
                CallbackQueryHandler(expense_delete_start, pattern=r"^expense:delete$"),
                CallbackQueryHandler(expenses_menu_start, pattern=r"^pay:expenses$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            BONUS_EMPLOYEE: [
                CallbackQueryHandler(bonus_employee_selected, pattern=r"^bnemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            BONUS_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_date_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            BONUS_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_amount_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            BONUS_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bonus_comment_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            BONUS_DELETE_SELECT: [
                CallbackQueryHandler(bonus_delete_selected, pattern=r"^bonusdel:"),
                CallbackQueryHandler(bonus_delete_confirmed, pattern=r"^bonusdelconfirm:"),
                CallbackQueryHandler(bonus_delete_start, pattern=r"^bonus:delete$"),
                CallbackQueryHandler(bonuses_menu_start, pattern=r"^pay:bonuses$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_EMPLOYEE: [
                CallbackQueryHandler(vacation_employee_selected, pattern=r"^vacemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_start_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_end_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_EDIT_SELECT: [
                CallbackQueryHandler(vacation_edit_selected, pattern=r"^vacationedit:"),
                CallbackQueryHandler(vacation_edit_start, pattern=r"^vacation:edit$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_EDIT_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_edit_start_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_EDIT_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vacation_edit_end_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            VACATION_DELETE_SELECT: [
                CallbackQueryHandler(vacation_delete_selected, pattern=r"^vacationdel:"),
                CallbackQueryHandler(vacation_delete_confirmed, pattern=r"^vacationdelconfirm:"),
                CallbackQueryHandler(vacation_delete_start, pattern=r"^vacation:delete$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_EMPLOYEE: [
                CallbackQueryHandler(penalty_employee_selected, pattern=r"^pnemp:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, penalty_date_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_TYPE_GROUP: [
                CallbackQueryHandler(penalty_type_group_selected, pattern=r"^(pngrp|pntype):"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_TYPE: [
                CallbackQueryHandler(penalty_type_selected, pattern=r"^(pntype|pngrp):"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, penalty_comment_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, penalty_amount_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_PHOTOS: [
                MessageHandler(filters.PHOTO, penalty_photo_received),
                CallbackQueryHandler(penalty_photos_finished, pattern=r"^penaltyphotos:(done|skip)$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PENALTY_CONFIRM: [
                CallbackQueryHandler(penalty_confirm_received, pattern=r"^penalty:confirm$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PAYROLL_PERIOD_SELECT: [
                CallbackQueryHandler(payroll_period_selected, pattern=r"^payperiod:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, period_name_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, period_start_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, period_end_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_PAYMENT_MODE: [
                CallbackQueryHandler(period_payment_mode_selected, pattern=r"^periodpay:"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_EDIT_FIELD: [
                CallbackQueryHandler(period_edit_field_selected, pattern=r"^periodfield:"),
                CallbackQueryHandler(periods_menu_start, pattern=r"^pay:periods$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            PERIOD_EDIT_VALUE: [
                CallbackQueryHandler(period_edit_payment_mode_selected, pattern=r"^periodeditpay:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, period_edit_value_received),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
            CLEANUP_CONFIRM: [
                CallbackQueryHandler(cleanup_confirm, pattern=r"^cleanup:yes$"),
                CallbackQueryHandler(payroll_cancel, pattern=r"^pay:cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(
                payroll_create_back,
                pattern=r"^payback:create_(employee|date|shift_type|interval|lunch|tasks|kpi)$",
            ),
            CommandHandler("cancel", payroll_cancel),
        ],
    )


def get_payroll_handlers():
    from modules.payroll.kpi_handlers import get_kpi_management_handler
    from modules.payroll.driver_handlers import get_drivers_handler

    return [
        CommandHandler("whoami", whoami),
        CallbackQueryHandler(payroll_menu, pattern=r"^section:payroll$"),
        get_payroll_conversation_handler(),
        get_kpi_management_handler(),
        get_drivers_handler(),
    ]
