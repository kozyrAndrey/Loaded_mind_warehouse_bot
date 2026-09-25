from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from modules.payroll.drivers import (
    DriverValidationError,
    add_driver_payment,
    add_driver_write_off,
    create_driver,
    delete_driver_payment,
    delete_driver_write_off,
    get_driver,
    get_driver_payments,
    get_driver_write_offs,
    get_drivers,
)
from modules.payroll.google_sheets import money, validate_date


(DRIVER_SELECT, DRIVER_NAME, DRIVER_PHONE, DRIVER_VEHICLE, PAYMENT_DATE, PAYMENT_AMOUNT, PAYMENT_COMMENT,
 PAYMENT_DELETE, WRITE_OFF_DELETE) = range(600, 609)


def drivers_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Добавить оплату", callback_data="driverpay:add")],
        [InlineKeyboardButton("➖ Добавить списание", callback_data="driverwoff:add")],
        [InlineKeyboardButton("➕ Создать водителя", callback_data="driver:add")],
        [InlineKeyboardButton("👀 Последние оплаты", callback_data="driverpay:list")],
        [InlineKeyboardButton("👀 Последние списания", callback_data="driverwoff:list")],
        [InlineKeyboardButton("🗑 Удалить оплату", callback_data="driverpay:delete")],
        [InlineKeyboardButton("🗑 Удалить списание", callback_data="driverwoff:delete")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
    ])


def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="driver:cancel")]])


def driver_select_keyboard():
    rows = [[InlineKeyboardButton(d["full_name"][:55], callback_data=f"driverselect:{d['driver_id']}")]
            for d in get_drivers()]
    rows.append([InlineKeyboardButton("➕ Создать нового водителя", callback_data="driverselect:new")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="driver:cancel")])
    return InlineKeyboardMarkup(rows)


def _manager(update):
    from modules.payroll.handlers import current_employee_or_none, is_manager
    employee = current_employee_or_none(update)
    return employee if is_manager(employee) else None


async def drivers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text(
        "🚚 Водители\n\nЗдесь хранится справочник водителей, оплаты и списания.",
        reply_markup=drivers_menu_keyboard(),
    )
    return ConversationHandler.END


async def payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["driver_flow"] = "payment"
    await query.edit_message_text("Выберите водителя или создайте нового:", reply_markup=driver_select_keyboard())
    return DRIVER_SELECT


async def write_off_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["driver_flow"] = "write_off"
    await query.edit_message_text(
        "Выберите водителя, которому часть суммы уже выдали:",
        reply_markup=driver_select_keyboard(),
    )
    return DRIVER_SELECT


async def driver_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["driver_flow"] = "create"
    await query.edit_message_text("Введите ФИО или название водителя:", reply_markup=cancel_keyboard())
    return DRIVER_NAME


async def driver_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("driverselect:", "")
    if value == "new":
        context.user_data.setdefault("driver_flow", "payment")
        await query.edit_message_text("Введите ФИО или название водителя:", reply_markup=cancel_keyboard())
        return DRIVER_NAME
    driver = get_driver(value)
    if not driver:
        await query.edit_message_text("Водитель не найден.", reply_markup=drivers_menu_keyboard())
        return ConversationHandler.END
    context.user_data["driver_id"] = driver["driver_id"]
    action = "списания" if context.user_data.get("driver_flow") == "write_off" else "оплаты"
    await query.edit_message_text(
        f"Введите дату {action} в формате ДД.ММ.ГГГГ:",
        reply_markup=cancel_keyboard(),
    )
    return PAYMENT_DATE


async def driver_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Укажите имя водителя:")
        return DRIVER_NAME
    context.user_data["driver_name"] = name
    await update.message.reply_text("Введите телефон или «-», если он не нужен:", reply_markup=cancel_keyboard())
    return DRIVER_PHONE


async def driver_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = (update.message.text or "").strip()
    context.user_data["driver_phone"] = "" if phone == "-" else phone
    await update.message.reply_text(
        "Введите номер машины или «-», если он пока неизвестен:",
        reply_markup=cancel_keyboard(),
    )
    return DRIVER_VEHICLE


async def driver_vehicle_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manager = _manager(update)
    if not manager:
        await update.message.reply_text("Недостаточно прав.")
        return ConversationHandler.END
    vehicle_number = (update.message.text or "").strip()
    try:
        driver = create_driver(
            context.user_data.get("driver_name"),
            phone=context.user_data.get("driver_phone", ""),
            vehicle_number="" if vehicle_number == "-" else vehicle_number,
            created_by=manager["full_name"],
        )
    except DriverValidationError as error:
        await update.message.reply_text(str(error), reply_markup=drivers_menu_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    if context.user_data.get("driver_flow") in {"payment", "write_off"}:
        context.user_data["driver_id"] = driver["driver_id"]
        action = "списания" if context.user_data.get("driver_flow") == "write_off" else "оплаты"
        await update.message.reply_text(
            f"Водитель создан ✅\n\nВведите дату {action} в формате ДД.ММ.ГГГГ:",
            reply_markup=cancel_keyboard(),
        )
        return PAYMENT_DATE
    context.user_data.clear()
    vehicle_text = f"\nМашина: {driver['vehicle_number']}" if driver.get("vehicle_number") else ""
    await update.message.reply_text(
        f"Водитель «{driver['full_name']}» создан ✅{vehicle_text}",
        reply_markup=drivers_menu_keyboard(),
    )
    return ConversationHandler.END


async def payment_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    if not validate_date(value):
        await update.message.reply_text("Неверная дата. Введите её в формате ДД.ММ.ГГГГ:")
        return PAYMENT_DATE
    context.user_data["driver_payment_date"] = value
    action = "списания" if context.user_data.get("driver_flow") == "write_off" else "оплаты"
    await update.message.reply_text(f"Введите сумму {action}:", reply_markup=cancel_keyboard())
    return PAYMENT_AMOUNT


async def payment_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float((update.message.text or "").strip().replace(" ", "").replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Введите положительную сумму числом:")
        return PAYMENT_AMOUNT
    context.user_data["driver_payment_amount"] = amount
    await update.message.reply_text("Введите комментарий или «-», если он не нужен:", reply_markup=cancel_keyboard())
    return PAYMENT_COMMENT


async def payment_comment_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manager = _manager(update)
    driver = get_driver(context.user_data.get("driver_id"))
    if not manager or not driver:
        await update.message.reply_text("Недостаточно прав или водитель не найден.")
        return ConversationHandler.END
    comment = (update.message.text or "").strip()
    is_write_off = context.user_data.get("driver_flow") == "write_off"
    save = add_driver_write_off if is_write_off else add_driver_payment
    item = save(
        driver,
        context.user_data["driver_payment_date"],
        context.user_data["driver_payment_amount"],
        comment="" if comment == "-" else comment,
        created_by=manager["full_name"],
    )
    context.user_data.clear()
    result_text = "Списание добавлено" if is_write_off else "Оплата добавлена"
    await update.message.reply_text(
        f"{result_text} ✅\n\n{item['date']} — {item['driver_name']} — {money(item['amount'])} ₽"
        + (f"\n{item['comment']}" if item["comment"] else ""),
        reply_markup=drivers_menu_keyboard(),
    )
    return ConversationHandler.END


def _payment_label(item):
    vehicle = f" · {item['vehicle_number']}" if item.get("vehicle_number") else ""
    return f"{item['date']} — {item['driver_name']}{vehicle} — {money(item['amount'])} ₽"


async def payments_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = get_driver_payments()[-15:]
    text = "Последние оплаты водителям:\n\n" + ("\n\n".join(
        _payment_label(item) + (f"\n{item['comment']}" if item["comment"] else "") for item in reversed(items)
    ) if items else "Оплат пока нет.")
    await query.edit_message_text(text, reply_markup=drivers_menu_keyboard())
    return ConversationHandler.END


async def write_offs_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = get_driver_write_offs()[-15:]
    text = "Последние списания водителям:\n\n" + ("\n\n".join(
        _payment_label(item) + (f"\n{item['comment']}" if item["comment"] else "") for item in reversed(items)
    ) if items else "Списаний пока нет.")
    await query.edit_message_text(text, reply_markup=drivers_menu_keyboard())
    return ConversationHandler.END


async def payment_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = list(reversed(get_driver_payments()[-15:]))
    if not items:
        await query.edit_message_text("Оплат пока нет.", reply_markup=drivers_menu_keyboard())
        return ConversationHandler.END
    rows = [[InlineKeyboardButton(_payment_label(item)[:60], callback_data=f"driverpaydel:{item['driver_payment_id']}")] for item in items]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="driver:cancel")])
    await query.edit_message_text("Выберите оплату для удаления:", reply_markup=InlineKeyboardMarkup(rows))
    return PAYMENT_DELETE


async def payment_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    deleted = delete_driver_payment(query.data.replace("driverpaydel:", ""))
    await query.edit_message_text("Оплата удалена ✅" if deleted else "Оплата не найдена.", reply_markup=drivers_menu_keyboard())
    return ConversationHandler.END


async def write_off_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = list(reversed(get_driver_write_offs()[-15:]))
    if not items:
        await query.edit_message_text("Списаний пока нет.", reply_markup=drivers_menu_keyboard())
        return ConversationHandler.END
    rows = [[
        InlineKeyboardButton(
            _payment_label(item)[:60],
            callback_data=f"driverwoffdel:{item['driver_write_off_id']}",
        )
    ] for item in items]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="driver:cancel")])
    await query.edit_message_text("Выберите списание для удаления:", reply_markup=InlineKeyboardMarkup(rows))
    return WRITE_OFF_DELETE


async def write_off_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _manager(update):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    deleted = delete_driver_write_off(query.data.replace("driverwoffdel:", ""))
    await query.edit_message_text(
        "Списание удалено ✅" if deleted else "Списание не найдено.",
        reply_markup=drivers_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Действие отменено.", reply_markup=drivers_menu_keyboard())
    return ConversationHandler.END


def get_drivers_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(drivers_menu, pattern=r"^pay:drivers$"),
            CallbackQueryHandler(payment_start, pattern=r"^driverpay:add$"),
            CallbackQueryHandler(write_off_start, pattern=r"^driverwoff:add$"),
            CallbackQueryHandler(driver_create_start, pattern=r"^driver:add$"),
            CallbackQueryHandler(payments_list, pattern=r"^driverpay:list$"),
            CallbackQueryHandler(write_offs_list, pattern=r"^driverwoff:list$"),
            CallbackQueryHandler(payment_delete_start, pattern=r"^driverpay:delete$"),
            CallbackQueryHandler(write_off_delete_start, pattern=r"^driverwoff:delete$"),
        ],
        states={
            DRIVER_SELECT: [CallbackQueryHandler(driver_selected, pattern=r"^driverselect:")],
            DRIVER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_name_received)],
            DRIVER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_phone_received)],
            DRIVER_VEHICLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, driver_vehicle_received)],
            PAYMENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_date_received)],
            PAYMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount_received)],
            PAYMENT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_comment_received)],
            PAYMENT_DELETE: [CallbackQueryHandler(payment_delete_selected, pattern=r"^driverpaydel:")],
            WRITE_OFF_DELETE: [CallbackQueryHandler(write_off_delete_selected, pattern=r"^driverwoffdel:")],
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern=r"^driver:cancel$")],
        name="payroll_drivers",
    )
