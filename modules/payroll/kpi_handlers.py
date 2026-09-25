from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from modules.payroll.google_sheets import (
    append_kpi,
    find_employee_for_telegram_user,
    get_kpi_by_id,
    get_kpi_items,
    is_manager,
    money,
    safe_float,
    set_kpi_active,
    update_kpi_fields,
)


(
    KPI_MANAGE_MENU,
    KPI_ADD_NAME,
    KPI_ADD_RATE,
    KPI_EDIT_SELECT,
    KPI_EDIT_FIELD,
    KPI_EDIT_VALUE,
    KPI_DELETE_SELECT,
    KPI_DELETE_CONFIRM,
) = range(3300, 3308)


def current_employee(update: Update):
    return find_employee_for_telegram_user(update.effective_user)


def ensure_manager(update: Update):
    return is_manager(current_employee(update))


def kpi_management_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Добавить позицию", callback_data="kpimgr:add")],
            [InlineKeyboardButton("✏️ Изменить позицию", callback_data="kpimgr:edit")],
            [InlineKeyboardButton("🗑 Удалить позицию", callback_data="kpimgr:delete")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:payroll")],
        ]
    )


def cancel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data="kpimgr:cancel")]]
    )


def kpi_items_keyboard(prefix, active_only=False):
    rows = []
    for item in get_kpi_items(active_only=active_only):
        status = "" if item["is_active"] else " 🚫"
        label = f"{item['name']} — {money(item['rate'])} ₽{status}"
        rows.append(
            [InlineKeyboardButton(label[:60], callback_data=f"{prefix}:{item['kpi_id']}")]
        )
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="kpimgr:cancel")])
    return InlineKeyboardMarkup(rows)


def kpi_edit_fields_keyboard(item):
    active_label = "🚫 Отключить" if item["is_active"] else "✅ Восстановить"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Название", callback_data="kpimgrfield:name")],
            [InlineKeyboardButton("Ставка", callback_data="kpimgrfield:rate")],
            [InlineKeyboardButton(active_label, callback_data="kpimgrfield:toggle")],
            [InlineKeyboardButton("✅ Готово", callback_data="kpimgrfield:done")],
            [InlineKeyboardButton("❌ Отмена", callback_data="kpimgr:cancel")],
        ]
    )


def kpi_card(item):
    return (
        f"Название: {item['name']}\n"
        f"Ставка: {money(item['rate'])} ₽\n"
        f"Статус: {'активна' if item['is_active'] else 'удалена'}"
    )


def kpi_management_text():
    items = get_kpi_items(active_only=False)
    if not items:
        return "⚙️ Позиции KPI\n\nПока нет ни одной позиции."

    lines = ["⚙️ Позиции KPI", ""]
    for index, item in enumerate(items, start=1):
        status = "" if item["is_active"] else " — удалена"
        lines.append(f"{index}. {item['name']} — {money(item['rate'])} ₽{status}")
    return "\n".join(lines)


async def deny_manager_access(query):
    await query.edit_message_text("⛔️ Управление KPI доступно только руководителям.")


async def kpi_management_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    context.user_data.pop("kpi_add", None)
    context.user_data.pop("kpi_edit_id", None)
    context.user_data.pop("kpi_edit_field", None)
    context.user_data.pop("kpi_delete_id", None)
    await query.edit_message_text(
        kpi_management_text(),
        reply_markup=kpi_management_keyboard(),
    )
    return KPI_MANAGE_MENU


async def kpi_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    context.user_data["kpi_add"] = {}
    await query.edit_message_text("Введите название новой позиции KPI:", reply_markup=cancel_keyboard())
    return KPI_ADD_NAME


async def kpi_add_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым:", reply_markup=cancel_keyboard())
        return KPI_ADD_NAME

    context.user_data.setdefault("kpi_add", {})["name"] = name
    await update.message.reply_text(
        "Введите ставку за одну единицу KPI в рублях:",
        reply_markup=cancel_keyboard(),
    )
    return KPI_ADD_RATE


async def kpi_add_rate_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_manager(update):
        await update.message.reply_text("⛔️ Управление KPI доступно только руководителям.")
        return ConversationHandler.END

    rate = safe_float(update.message.text)
    if rate <= 0:
        await update.message.reply_text("Введите ставку числом больше нуля:", reply_markup=cancel_keyboard())
        return KPI_ADD_RATE

    data = context.user_data.get("kpi_add") or {}
    try:
        item = append_kpi(data.get("name", ""), rate)
    except ValueError as error:
        await update.message.reply_text(str(error), reply_markup=kpi_management_keyboard())
        context.user_data.pop("kpi_add", None)
        return KPI_MANAGE_MENU

    context.user_data.pop("kpi_add", None)
    await update.message.reply_text(
        "Позиция KPI добавлена ✅\n\n" + kpi_card(item),
        reply_markup=kpi_management_keyboard(),
    )
    return KPI_MANAGE_MENU


async def kpi_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    items = get_kpi_items(active_only=False)
    if not items:
        await query.edit_message_text("Нет позиций для изменения.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    await query.edit_message_text(
        "Выберите позицию KPI для изменения:",
        reply_markup=kpi_items_keyboard("kpimgredit", active_only=False),
    )
    return KPI_EDIT_SELECT


async def kpi_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item = get_kpi_by_id(query.data.replace("kpimgredit:", ""))
    if not item:
        await query.edit_message_text("Позиция KPI не найдена.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    context.user_data["kpi_edit_id"] = item["kpi_id"]
    await query.edit_message_text(
        "Что изменить?\n\n" + kpi_card(item),
        reply_markup=kpi_edit_fields_keyboard(item),
    )
    return KPI_EDIT_FIELD


async def kpi_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    action = query.data.replace("kpimgrfield:", "")
    kpi_id = context.user_data.get("kpi_edit_id")
    item = get_kpi_by_id(kpi_id)
    if not item:
        await query.edit_message_text("Позиция KPI не найдена.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    if action == "done":
        context.user_data.pop("kpi_edit_id", None)
        context.user_data.pop("kpi_edit_field", None)
        await query.edit_message_text(
            "Редактирование завершено ✅",
            reply_markup=kpi_management_keyboard(),
        )
        return KPI_MANAGE_MENU

    if action == "toggle":
        item = set_kpi_active(kpi_id, not item["is_active"])
        await query.edit_message_text(
            "Статус изменён ✅\n\n" + kpi_card(item),
            reply_markup=kpi_edit_fields_keyboard(item),
        )
        return KPI_EDIT_FIELD

    if action not in {"name", "rate"}:
        await query.edit_message_text(
            "Неизвестное поле.\n\n" + kpi_card(item),
            reply_markup=kpi_edit_fields_keyboard(item),
        )
        return KPI_EDIT_FIELD

    context.user_data["kpi_edit_field"] = action
    prompt = "Введите новое название KPI:" if action == "name" else "Введите новую ставку в рублях:"
    await query.edit_message_text(prompt, reply_markup=cancel_keyboard())
    return KPI_EDIT_VALUE


async def kpi_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_manager(update):
        await update.message.reply_text("⛔️ Управление KPI доступно только руководителям.")
        return ConversationHandler.END

    kpi_id = context.user_data.get("kpi_edit_id")
    field = context.user_data.get("kpi_edit_field")
    if field not in {"name", "rate"}:
        await update.message.reply_text(
            "Не удалось определить поле для изменения.",
            reply_markup=kpi_management_keyboard(),
        )
        return KPI_MANAGE_MENU

    value = (update.message.text or "").strip()
    if field == "rate":
        value = safe_float(value)
        if value <= 0:
            await update.message.reply_text("Введите ставку числом больше нуля:", reply_markup=cancel_keyboard())
            return KPI_EDIT_VALUE
    elif field == "name" and not value:
        await update.message.reply_text("Название не должно быть пустым:", reply_markup=cancel_keyboard())
        return KPI_EDIT_VALUE

    try:
        item = update_kpi_fields(kpi_id, **{field: value})
    except ValueError as error:
        await update.message.reply_text(str(error), reply_markup=cancel_keyboard())
        return KPI_EDIT_VALUE

    if not item:
        await update.message.reply_text("Позиция KPI не найдена.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    context.user_data.pop("kpi_edit_field", None)
    await update.message.reply_text(
        "Сохранено ✅\n\nЧто изменить дальше?\n\n" + kpi_card(item),
        reply_markup=kpi_edit_fields_keyboard(item),
    )
    return KPI_EDIT_FIELD


async def kpi_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    if not get_kpi_items(active_only=True):
        await query.edit_message_text("Нет активных позиций для удаления.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    await query.edit_message_text(
        "Выберите позицию KPI для удаления:",
        reply_markup=kpi_items_keyboard("kpimgrdelete", active_only=True),
    )
    return KPI_DELETE_SELECT


async def kpi_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item = get_kpi_by_id(query.data.replace("kpimgrdelete:", ""))
    if not item or not item["is_active"]:
        await query.edit_message_text("Активная позиция KPI не найдена.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    context.user_data["kpi_delete_id"] = item["kpi_id"]
    await query.edit_message_text(
        f"Удалить позицию «{item['name']}»?\n\n"
        "Она исчезнет из выбора в новых отчётах. Данные старых отчётов сохранятся.",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Да, удалить", callback_data="kpimgrdeleteconfirm:yes")],
                [InlineKeyboardButton("❌ Отмена", callback_data="kpimgr:cancel")],
            ]
        ),
    )
    return KPI_DELETE_CONFIRM


async def kpi_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await deny_manager_access(query)
        return ConversationHandler.END

    item = set_kpi_active(context.user_data.get("kpi_delete_id"), False)
    context.user_data.pop("kpi_delete_id", None)
    if not item:
        await query.edit_message_text("Позиция KPI не найдена.", reply_markup=kpi_management_keyboard())
        return KPI_MANAGE_MENU

    await query.edit_message_text(
        f"Позиция «{item['name']}» удалена ✅",
        reply_markup=kpi_management_keyboard(),
    )
    return KPI_MANAGE_MENU


async def kpi_management_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("kpi_add", None)
    context.user_data.pop("kpi_edit_id", None)
    context.user_data.pop("kpi_edit_field", None)
    context.user_data.pop("kpi_delete_id", None)
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Действие отменено.", reply_markup=kpi_management_keyboard())
    return KPI_MANAGE_MENU


def get_kpi_management_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(kpi_management_start, pattern=r"^pay:kpi_management$")
        ],
        states={
            KPI_MANAGE_MENU: [
                CallbackQueryHandler(kpi_add_start, pattern=r"^kpimgr:add$"),
                CallbackQueryHandler(kpi_edit_start, pattern=r"^kpimgr:edit$"),
                CallbackQueryHandler(kpi_delete_start, pattern=r"^kpimgr:delete$"),
            ],
            KPI_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, kpi_add_name_received)],
            KPI_ADD_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, kpi_add_rate_received)],
            KPI_EDIT_SELECT: [CallbackQueryHandler(kpi_edit_selected, pattern=r"^kpimgredit:")],
            KPI_EDIT_FIELD: [
                CallbackQueryHandler(kpi_edit_field_selected, pattern=r"^kpimgrfield:")
            ],
            KPI_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kpi_edit_value_received)
            ],
            KPI_DELETE_SELECT: [
                CallbackQueryHandler(kpi_delete_selected, pattern=r"^kpimgrdelete:")
            ],
            KPI_DELETE_CONFIRM: [
                CallbackQueryHandler(
                    kpi_delete_confirmed,
                    pattern=r"^kpimgrdeleteconfirm:yes$",
                )
            ],
        },
        fallbacks=[
            CallbackQueryHandler(kpi_management_cancel, pattern=r"^kpimgr:cancel$")
        ],
        name="kpi_management",
        persistent=False,
    )
