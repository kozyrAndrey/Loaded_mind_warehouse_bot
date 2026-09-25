import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from modules.employees.roles import has_role

from modules.payroll.additional_pay import (
    AdditionalPayValidationError,
    TREND_ISLAND_POSITION_TYPE,
    TREND_ISLAND_UNIT_RATE,
    TREND_ISLAND_WEEKLY_RATE,
    append_trend_island_payment,
    calculate_trend_island_pay,
    can_manage_additional_pay,
    delete_additional_payment,
    get_additional_payment,
    list_additional_payments,
    recent_completed_weeks,
    update_trend_island_payment,
)
from modules.payroll.google_sheets import (
    find_employee_for_telegram_user,
    get_employees,
    money,
)


MSK_TZ = ZoneInfo("Europe/Moscow")

(
    ADDITIONAL_WEEK,
    ADDITIONAL_QUANTITY,
    ADDITIONAL_ERRORS,
    ADDITIONAL_ERROR_COMMENT,
    ADDITIONAL_ERROR_PENALTY,
    ADDITIONAL_COMMENT,
    ADDITIONAL_CONFIRM,
    ADDITIONAL_EDIT_SELECT,
    ADDITIONAL_DELETE_SELECT,
) = range(600, 609)


def current_employee(update):
    return find_employee_for_telegram_user(update.effective_user)


def get_warehouse_manager():
    managers = [
        employee
        for employee in get_employees(include_inactive=False)
        if has_role(employee, "warehouse_manager")
    ]
    return managers[0] if len(managers) == 1 else None


def additional_pay_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Назначить Trend Island", callback_data="addpay:add")],
            [
                InlineKeyboardButton("✏️ Изменить", callback_data="addpay:edit"),
                InlineKeyboardButton("🗑 Удалить", callback_data="addpay:delete"),
            ],
            [InlineKeyboardButton("📋 Последние начисления", callback_data="addpay:view")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def cancel_keyboard(back_to_menu=False):
    rows = []
    if back_to_menu:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="pay:additional_pay")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")])
    return InlineKeyboardMarkup(rows)


def week_keyboard(base_date=None, extra_week_start=None):
    weeks = recent_completed_weeks(base_date=base_date, count=8)
    if extra_week_start:
        extra_start = datetime.strptime(str(extra_week_start), "%d.%m.%Y").date()
        if all(week_start != extra_start for week_start, _ in weeks):
            weeks.insert(0, (extra_start, extra_start + timedelta(days=6)))
    rows = []
    for week_start, week_end in weeks:
        rows.append(
            [
                InlineKeyboardButton(
                    f"{week_start.strftime('%d.%m.%Y')} — {week_end.strftime('%d.%m.%Y')}",
                    callback_data=f"addpayweek:{week_start.strftime('%d.%m.%Y')}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")])
    return InlineKeyboardMarkup(rows)


def errors_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Ошибок нет", callback_data="addpayerrors:no"),
                InlineKeyboardButton("Есть ошибки", callback_data="addpayerrors:yes"),
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")],
        ]
    )


def optional_comment_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Без комментария", callback_data="addpaycomment:skip")],
            [InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")],
        ]
    )


def confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Сохранить", callback_data="addpayconfirm:save")],
            [InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")],
        ]
    )


def record_select_keyboard(items, prefix):
    rows = []
    for item in items:
        label = (
            f"{item['week_start']} · {item['quantity']} пост. · "
            f"{money(item['total_amount'])} ₽"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"{prefix}:{item['additional_pay_id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="pay:additional_pay")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")])
    return InlineKeyboardMarkup(rows)


def delete_confirm_keyboard(additional_pay_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Да, удалить",
                    callback_data=f"addpaydeleteconfirm:{additional_pay_id}",
                )
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="addpay:delete")],
            [InlineKeyboardButton("❌ Отмена", callback_data="addpay:cancel")],
        ]
    )


def format_record(item, title="Дополнительное начисление"):
    errors = "нет"
    if item.get("has_errors"):
        errors = short_text(item.get("error_comment"), 500) or "есть"

    lines = [
        title,
        "",
        f"Позиция: {item['position_name']}",
        f"Получатель: {item['full_name']}",
        f"Неделя: {item['week_start']} — {item['week_end']}",
        f"Поставок: {item['quantity']}",
        f"Начислено до штрафа: {money(item['gross_amount'])} ₽",
        f"Ошибки: {errors}",
    ]
    if item.get("error_penalty"):
        lines.append(f"Штраф за ошибки: {money(item['error_penalty'])} ₽")
    lines.append(f"Итого: {money(item['total_amount'])} ₽")
    if item.get("comment"):
        lines.append(f"Комментарий: {short_text(item['comment'], 500)}")
    if item.get("assigned_by"):
        lines.append(f"Назначил: {item['assigned_by']}")
    return "\n".join(lines)


def short_text(value, limit):
    value = str(value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def format_record_summary(item, index):
    line = (
        f"{index}. {item['week_start']} — {item['week_end']} · "
        f"{item['quantity']} поставк. · {money(item['total_amount'])} ₽"
    )
    if item.get("error_penalty"):
        line += f"\nШтраф за ошибки: {money(item['error_penalty'])} ₽"
    if item.get("comment"):
        line += f"\nКомментарий: {short_text(item['comment'], 100)}"
    return line


def format_warehouse_manager_notification(item, updated=False):
    action = "обновлено" if updated else "назначено"
    errors = "нет"
    if item.get("has_errors"):
        errors = short_text(item.get("error_comment"), 1000) or "есть"

    formula = f"{item['quantity']} × {money(item['unit_rate'])} ₽"
    if item.get("weekly_rate"):
        formula += f" + {money(item['weekly_rate'])} ₽ за неделю"
    if item.get("error_penalty"):
        formula += f" − {money(item['error_penalty'])} ₽ штраф"
    formula += f" = {money(item['total_amount'])} ₽"

    lines = [
        f"💰 Дополнительное начисление {action}",
        "",
        "Позиция: Trend Island",
        f"Период: {item['week_start']} — {item['week_end']}",
        f"Собрано поставок: {item['quantity']}",
        f"Начислено до штрафа: {money(item['gross_amount'])} ₽",
        f"Ошибки: {errors}",
    ]
    if item.get("error_penalty"):
        lines.append(f"Штраф за ошибки: {money(item['error_penalty'])} ₽")
    lines.extend(
        [
            f"Итого начислено: {money(item['total_amount'])} ₽",
            "",
            f"Расчёт: {formula}",
        ]
    )
    if item.get("comment"):
        lines.append(f"Комментарий: {short_text(item['comment'], 1000)}")
    if item.get("assigned_by"):
        lines.append(f"Назначил: {item['assigned_by']}")
    return "\n".join(lines)


async def notify_warehouse_manager(context, employee, item, updated=False):
    telegram_user_id = str((employee or {}).get("telegram_user_id", "")).strip()
    if not telegram_user_id:
        return "У руководителя склада не указан Telegram user_id. Личный отчёт не отправлен."
    try:
        await context.bot.send_message(
            chat_id=int(telegram_user_id),
            text=format_warehouse_manager_notification(item, updated=updated),
        )
        return ""
    except Exception:
        logging.exception(
            "Не удалось отправить руководителю склада отчёт о начислении Trend Island"
        )
        return "Личный отчёт руководителю склада не отправлен из-за ошибки Telegram."


def draft_preview(draft, employee):
    calculation = calculate_trend_island_pay(
        draft["quantity"],
        draft.get("error_penalty", 0),
    )
    week_start = datetime.strptime(draft["week_start"], "%d.%m.%Y").date()
    week_end = week_start + timedelta(days=6)
    formula_parts = [
        f"{calculation['quantity']} × {money(TREND_ISLAND_UNIT_RATE)} ₽"
    ]
    if calculation["quantity"]:
        formula_parts.append(f"+ {money(TREND_ISLAND_WEEKLY_RATE)} ₽")
    if calculation["error_penalty"]:
        formula_parts.append(f"− {money(calculation['error_penalty'])} ₽")

    errors = "нет"
    if draft.get("has_errors"):
        errors = short_text(draft.get("error_comment"), 1000) or "есть"

    lines = [
        "Проверьте начисление:",
        "",
        "Позиция: Trend Island",
        f"Получатель: {employee['full_name']}",
        f"Неделя: {draft['week_start']} — {week_end.strftime('%d.%m.%Y')}",
        f"Поставок: {calculation['quantity']}",
        f"Ошибки: {errors}",
    ]
    if calculation["error_penalty"]:
        lines.append(f"Штраф за ошибки: {money(calculation['error_penalty'])} ₽")
    if draft.get("comment"):
        lines.append(f"Комментарий: {short_text(draft['comment'], 1000)}")
    lines.extend(
        [
            "",
            " ".join(formula_parts) + f" = {money(calculation['total_amount'])} ₽",
        ]
    )
    return "\n".join(lines)


async def deny_access(target):
    await target.edit_message_text(
        "⛔️ Дополнительные начисления доступны только руководителю бренда и администратору."
    )


async def additional_pay_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    await query.edit_message_text(
        "➕ Дополнительные начисления\n\nДоступная позиция: Trend Island",
        reply_markup=additional_pay_menu_keyboard(),
    )
    return ConversationHandler.END


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    if not get_warehouse_manager():
        await query.edit_message_text(
            "Не удалось однозначно определить активного руководителя склада. "
            "В справочнике должен быть ровно один активный сотрудник с ролью warehouse_manager.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END

    context.user_data["additional_pay_draft"] = {"mode": "create"}
    await query.edit_message_text(
        "Выберите завершённую неделю Trend Island:",
        reply_markup=week_keyboard(base_date=datetime.now(MSK_TZ).date()),
    )
    return ADDITIONAL_WEEK


async def week_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("additional_pay_draft") or {}
    draft["week_start"] = query.data.replace("addpayweek:", "", 1)
    context.user_data["additional_pay_draft"] = draft
    await query.edit_message_text(
        "Введите количество собранных поставок целым числом от 0:",
        reply_markup=cancel_keyboard(),
    )
    return ADDITIONAL_QUANTITY


async def quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_value = str(update.message.text or "").strip()
    try:
        quantity = int(raw_value)
    except ValueError:
        quantity = -1
    if quantity < 0 or str(quantity) != raw_value:
        await update.message.reply_text(
            "Введите целое количество поставок от 0:",
            reply_markup=cancel_keyboard(),
        )
        return ADDITIONAL_QUANTITY

    draft = context.user_data.get("additional_pay_draft") or {}
    draft["quantity"] = quantity
    context.user_data["additional_pay_draft"] = draft
    if quantity == 0:
        draft.update(
            {
                "has_errors": False,
                "error_comment": "",
                "error_penalty": 0,
            }
        )
        await update.message.reply_text(
            "За эту неделю поставок не было. Добавьте необязательный комментарий:",
            reply_markup=optional_comment_keyboard(),
        )
        return ADDITIONAL_COMMENT

    await update.message.reply_text(
        "Были ли ошибки в поставках?",
        reply_markup=errors_keyboard(),
    )
    return ADDITIONAL_ERRORS


async def errors_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    has_errors = query.data.endswith(":yes")
    draft = context.user_data.get("additional_pay_draft") or {}
    draft["has_errors"] = has_errors
    if not has_errors:
        draft["error_comment"] = ""
        draft["error_penalty"] = 0
    context.user_data["additional_pay_draft"] = draft

    if has_errors:
        await query.edit_message_text(
            "Опишите ошибки:",
            reply_markup=cancel_keyboard(),
        )
        return ADDITIONAL_ERROR_COMMENT

    await query.edit_message_text(
        "Добавьте необязательный комментарий к начислению:",
        reply_markup=optional_comment_keyboard(),
    )
    return ADDITIONAL_COMMENT


async def error_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = str(update.message.text or "").strip()
    if not comment:
        await update.message.reply_text("Описание ошибок не должно быть пустым:")
        return ADDITIONAL_ERROR_COMMENT
    draft = context.user_data.get("additional_pay_draft") or {}
    draft["error_comment"] = comment
    context.user_data["additional_pay_draft"] = draft
    gross = calculate_trend_island_pay(draft["quantity"])["gross_amount"]
    await update.message.reply_text(
        f"Введите штраф за ошибки больше 0 ₽ и не более {money(gross)} ₽:",
        reply_markup=cancel_keyboard(),
    )
    return ADDITIONAL_ERROR_PENALTY


async def error_penalty_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_value = str(update.message.text or "").strip().replace(",", ".")
    try:
        penalty = float(raw_value)
    except ValueError:
        penalty = 0
    draft = context.user_data.get("additional_pay_draft") or {}
    try:
        if penalty <= 0:
            raise AdditionalPayValidationError("Штраф должен быть больше 0 ₽.")
        calculate_trend_island_pay(draft["quantity"], penalty)
    except AdditionalPayValidationError as error:
        await update.message.reply_text(str(error), reply_markup=cancel_keyboard())
        return ADDITIONAL_ERROR_PENALTY

    draft["error_penalty"] = penalty
    context.user_data["additional_pay_draft"] = draft
    await update.message.reply_text(
        "Добавьте необязательный комментарий к начислению:",
        reply_markup=optional_comment_keyboard(),
    )
    return ADDITIONAL_COMMENT


async def comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.get("additional_pay_draft") or {}
    draft["comment"] = str(update.message.text or "").strip()
    context.user_data["additional_pay_draft"] = draft
    return await show_confirmation(update.message, context)


async def comment_skipped(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get("additional_pay_draft") or {}
    draft["comment"] = ""
    context.user_data["additional_pay_draft"] = draft
    return await show_confirmation(query, context)


async def show_confirmation(target, context):
    employee = get_warehouse_manager()
    if not employee:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text("Руководитель склада не найден.")
        else:
            await target.reply_text("Руководитель склада не найден.")
        return ConversationHandler.END
    text = draft_preview(context.user_data["additional_pay_draft"], employee)
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=confirm_keyboard())
    else:
        await target.reply_text(text, reply_markup=confirm_keyboard())
    return ADDITIONAL_CONFIRM


async def save_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    assigning_employee = current_employee(update)
    if not can_manage_additional_pay(assigning_employee):
        await deny_access(query)
        return ConversationHandler.END

    employee = get_warehouse_manager()
    draft = context.user_data.get("additional_pay_draft") or {}
    try:
        kwargs = {
            "employee": employee,
            "week_start": draft["week_start"],
            "quantity": draft["quantity"],
            "has_errors": draft.get("has_errors", False),
            "error_comment": draft.get("error_comment", ""),
            "error_penalty": draft.get("error_penalty", 0),
            "comment": draft.get("comment", ""),
            "assigned_by": assigning_employee["full_name"],
        }
        if draft.get("mode") == "edit":
            item = update_trend_island_payment(draft["additional_pay_id"], **kwargs)
        else:
            item = append_trend_island_payment(**kwargs)
    except (AdditionalPayValidationError, KeyError) as error:
        await query.edit_message_text(
            f"Не удалось сохранить начисление: {error}",
            reply_markup=additional_pay_menu_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    if not item:
        await query.edit_message_text(
            "Запись не найдена или уже удалена.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    updated = draft.get("mode") == "edit"
    delivery_warning = await notify_warehouse_manager(
        context,
        employee,
        item,
        updated=updated,
    )
    action = "обновлено" if updated else "добавлено"
    result_text = format_record(item, title=f"Начисление {action} ✅")
    if delivery_warning:
        result_text += f"\n\n⚠️ {delivery_warning}"
    else:
        result_text += "\n\nЛичный отчёт отправлен руководителю склада ✅"
    await query.edit_message_text(
        result_text,
        reply_markup=additional_pay_menu_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def view_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    items = list_additional_payments(position_type=TREND_ISLAND_POSITION_TYPE, limit=10)
    if not items:
        await query.edit_message_text(
            "Дополнительных начислений пока нет.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END
    text = "Последние начисления Trend Island\n\n" + "\n\n".join(
        format_record_summary(item, index)
        for index, item in enumerate(items, 1)
    )
    await query.edit_message_text(text, reply_markup=additional_pay_menu_keyboard())
    return ConversationHandler.END


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    items = list_additional_payments(position_type=TREND_ISLAND_POSITION_TYPE, limit=10)
    if not items:
        await query.edit_message_text(
            "Дополнительных начислений пока нет.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите начисление. Его данные нужно будет ввести заново:",
        reply_markup=record_select_keyboard(items, "addpayedit"),
    )
    return ADDITIONAL_EDIT_SELECT


async def edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    additional_pay_id = query.data.replace("addpayedit:", "", 1)
    item = get_additional_payment(additional_pay_id)
    if not item:
        await query.edit_message_text(
            "Запись не найдена.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END
    context.user_data["additional_pay_draft"] = {
        "mode": "edit",
        "additional_pay_id": additional_pay_id,
    }
    await query.edit_message_text(
        format_record(item, title="Текущее начисление")
        + "\n\nВыберите неделю для обновлённой записи:",
        reply_markup=week_keyboard(
            base_date=datetime.now(MSK_TZ).date(),
            extra_week_start=item["week_start"],
        ),
    )
    return ADDITIONAL_WEEK


async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    items = list_additional_payments(position_type=TREND_ISLAND_POSITION_TYPE, limit=10)
    if not items:
        await query.edit_message_text(
            "Дополнительных начислений пока нет.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите начисление для удаления:",
        reply_markup=record_select_keyboard(items, "addpaydelete"),
    )
    return ADDITIONAL_DELETE_SELECT


async def delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    additional_pay_id = query.data.replace("addpaydelete:", "", 1)
    item = get_additional_payment(additional_pay_id)
    if not item:
        await query.edit_message_text(
            "Запись не найдена.",
            reply_markup=additional_pay_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        format_record(item, title="Удалить это начисление?"),
        reply_markup=delete_confirm_keyboard(additional_pay_id),
    )
    return ADDITIONAL_DELETE_SELECT


async def delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not can_manage_additional_pay(current_employee(update)):
        await deny_access(query)
        return ConversationHandler.END
    item = delete_additional_payment(
        query.data.replace("addpaydeleteconfirm:", "", 1)
    )
    if not item:
        text = "Запись не найдена или уже удалена."
    else:
        text = format_record(item, title="Начисление удалено ✅")
    await query.edit_message_text(text, reply_markup=additional_pay_menu_keyboard())
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    target = update.callback_query or update.message
    if update.callback_query:
        await update.callback_query.answer()
        await target.edit_message_text(
            "Действие отменено.",
            reply_markup=additional_pay_menu_keyboard(),
        )
    else:
        await target.reply_text(
            "Действие отменено.",
            reply_markup=additional_pay_menu_keyboard(),
        )
    return ConversationHandler.END


def get_additional_pay_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(additional_pay_menu_start, pattern=r"^pay:additional_pay$"),
            CallbackQueryHandler(add_start, pattern=r"^addpay:add$"),
            CallbackQueryHandler(view_start, pattern=r"^addpay:view$"),
            CallbackQueryHandler(edit_start, pattern=r"^addpay:edit$"),
            CallbackQueryHandler(delete_start, pattern=r"^addpay:delete$"),
        ],
        states={
            ADDITIONAL_WEEK: [
                CallbackQueryHandler(week_selected, pattern=r"^addpayweek:"),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_received),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_ERRORS: [
                CallbackQueryHandler(errors_selected, pattern=r"^addpayerrors:(yes|no)$"),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_ERROR_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, error_comment_received),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_ERROR_PENALTY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, error_penalty_received),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, comment_received),
                CallbackQueryHandler(comment_skipped, pattern=r"^addpaycomment:skip$"),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_CONFIRM: [
                CallbackQueryHandler(save_confirmed, pattern=r"^addpayconfirm:save$"),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_EDIT_SELECT: [
                CallbackQueryHandler(edit_selected, pattern=r"^addpayedit:"),
                CallbackQueryHandler(edit_start, pattern=r"^addpay:edit$"),
                CallbackQueryHandler(
                    additional_pay_menu_start,
                    pattern=r"^pay:additional_pay$",
                ),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
            ADDITIONAL_DELETE_SELECT: [
                CallbackQueryHandler(delete_selected, pattern=r"^addpaydelete:(?!confirm)"),
                CallbackQueryHandler(delete_confirmed, pattern=r"^addpaydeleteconfirm:"),
                CallbackQueryHandler(delete_start, pattern=r"^addpay:delete$"),
                CallbackQueryHandler(
                    additional_pay_menu_start,
                    pattern=r"^pay:additional_pay$",
                ),
                CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern=r"^addpay:cancel$")],
    )
