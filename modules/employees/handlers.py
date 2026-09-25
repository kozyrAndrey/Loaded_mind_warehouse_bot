from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from core.keyboards import build_employees_menu_keyboard
from modules.employees.roles import (
    ROLE_LABELS,
    ROLE_ORDER,
    employee_roles,
    format_role_labels,
    has_any_role,
    primary_role,
)
from modules.payroll.google_sheets import (
    append_employee,
    get_employee_by_id,
    get_employees,
    is_manager,
    money,
    safe_float,
    set_employee_active,
    find_employee_for_telegram_user,
    update_employee_fields,
)


(
    EMP_ADD_NAME,
    EMP_ADD_PHONE,
    EMP_ADD_TG_ID,
    EMP_ADD_USERNAME,
    EMP_ADD_ROLE,
    EMP_ADD_HOURLY_RATE,
    EMP_ADD_FIXED_SALARY,
    EMP_ADD_COMMON_FUND,
    EMP_FIRE_SELECT,
    EMP_FIRE_CONFIRM,
    EMP_EDIT_SELECT,
    EMP_EDIT_FIELD,
    EMP_EDIT_VALUE,
) = range(1500, 1513)


EMPLOYEE_ROLES = [(role, ROLE_LABELS[role]) for role in ROLE_ORDER]

EDIT_FIELDS = [
    ("full_name", "ФИО"),
    ("phone", "Телефон"),
    ("telegram_user_id", "Telegram user_id"),
    ("telegram_username", "Telegram username"),
    ("role", "Должности"),
    ("hourly_rate", "Часовая ставка"),
    ("fixed_salary", "Оклад"),
    ("include_in_common_fund", "Участие в общем фонде"),
    ("is_active", "Активность"),
]


def current_employee(update: Update):
    return find_employee_for_telegram_user(update.effective_user)


def ensure_manager(update: Update):
    return is_manager(current_employee(update))


def can_manage_privileged(update):
    return has_any_role(current_employee(update), {"admin", "warehouse_manager"})


def privileged_target(employee):
    return has_any_role(employee, {"admin", "warehouse_manager"})


def cancel_keyboard(back_target=None):
    rows = []
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"empback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")])
    return InlineKeyboardMarkup(rows)


def roles_keyboard(selected_roles=None, back_target=None):
    selected_roles = set(selected_roles or [])
    rows = [
        [
            InlineKeyboardButton(
                f"{'✅' if role in selected_roles else '⬜️'} {label}",
                callback_data=f"emprole:toggle:{role}",
            )
        ]
        for role, label in EMPLOYEE_ROLES
    ]
    rows.append([InlineKeyboardButton("✅ Сохранить должности", callback_data="emprole:done")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"empback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")])
    return InlineKeyboardMarkup(rows)


def common_fund_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Да", callback_data="empfund:yes")],
        [InlineKeyboardButton("Нет", callback_data="empfund:no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="empback:fixed_salary")],
        [InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")],
    ])


def active_employees_keyboard(prefix):
    rows = []
    for employee in get_employees(include_inactive=False):
        rows.append([InlineKeyboardButton(employee["full_name"], callback_data=f"{prefix}:{employee['employee_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")])
    return InlineKeyboardMarkup(rows)


def employees_keyboard(prefix, include_inactive=False):
    rows = []
    for employee in get_employees(include_inactive=include_inactive):
        status = "" if employee.get("is_active") else " 🚫"
        rows.append([InlineKeyboardButton(f"{employee['full_name']}{status}", callback_data=f"{prefix}:{employee['employee_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")])
    return InlineKeyboardMarkup(rows)


def edit_fields_keyboard():
    rows = [[InlineKeyboardButton(label, callback_data=f"empeditfield:{field}")] for field, label in EDIT_FIELDS]
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="empedit:done")])
    rows.append([InlineKeyboardButton("⬅️ Назад к списку", callback_data="empback:edit_select")])
    return InlineKeyboardMarkup(rows)


def bool_edit_keyboard(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Да", callback_data=f"{prefix}:yes")],
        [InlineKeyboardButton("Нет", callback_data=f"{prefix}:no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="empback:edit_fields")],
        [InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")],
    ])


def employee_card(employee):
    username = employee.get("telegram_username") or ""
    if username:
        username = f"@{username}"
    else:
        username = "—"

    return (
        f"ФИО: {employee['full_name']}\n"
        f"Телефон: {employee.get('phone') or '—'}\n"
        f"Telegram user_id: {employee.get('telegram_user_id') or '—'}\n"
        f"Telegram username: {username}\n"
        f"Должности: {format_role_labels(employee)}\n"
        f"Часовая ставка: {money(employee.get('hourly_rate', 0))}\n"
        f"Оклад: {money(employee.get('fixed_salary', 0))}\n"
        f"Общий фонд: {'да' if employee.get('include_in_common_fund') else 'нет'}\n"
        f"Активен: {'да' if employee.get('is_active') else 'нет'}"
    )


async def employees_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление сотрудниками доступно только руководителям.")
        return ConversationHandler.END

    context.user_data["employee_add"] = {}
    await query.edit_message_text("Введите ФИО сотрудника:", reply_markup=cancel_keyboard())
    return EMP_ADD_NAME


async def employee_add_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = (update.message.text or "").strip()
    if not full_name:
        await update.message.reply_text("Введите непустое ФИО:", reply_markup=cancel_keyboard())
        return EMP_ADD_NAME

    context.user_data["employee_add"]["full_name"] = full_name
    await update.message.reply_text(
        "Введите номер телефона сотрудника или «-», если пока неизвестен:",
        reply_markup=cancel_keyboard("name"),
    )
    return EMP_ADD_PHONE


async def employee_add_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    context.user_data["employee_add"]["phone"] = "" if value == "-" else value
    await update.message.reply_text(
        "Введите Telegram user_id сотрудника или «-», если пока неизвестен:",
        reply_markup=cancel_keyboard("phone"),
    )
    return EMP_ADD_TG_ID


async def employee_add_tg_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    context.user_data["employee_add"]["telegram_user_id"] = "" if value == "-" else value
    await update.message.reply_text(
        "Введите Telegram username без @ или «-», если пока неизвестен:",
        reply_markup=cancel_keyboard("tg_id"),
    )
    return EMP_ADD_USERNAME


async def employee_add_username_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = (update.message.text or "").strip()
    context.user_data["employee_add"]["telegram_username"] = "" if value == "-" else value
    context.user_data["employee_add"]["roles"] = []
    await update.message.reply_text(
        "Выберите одну или несколько должностей:",
        reply_markup=roles_keyboard([], "username"),
    )
    return EMP_ADD_ROLE


async def employee_add_role_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data.replace("emprole:", "", 1)
    selected = context.user_data.setdefault("employee_add", {}).setdefault("roles", [])

    if action == "done":
        if not selected:
            await query.answer("Выберите хотя бы одну должность.", show_alert=True)
            return EMP_ADD_ROLE
        await query.answer()
        context.user_data["employee_add"]["role"] = primary_role(selected)
        await query.edit_message_text("Введите часовую ставку, например 437.5:", reply_markup=cancel_keyboard("role"))
        return EMP_ADD_HOURLY_RATE

    role = action.replace("toggle:", "", 1)
    if role not in {role_key for role_key, _label in EMPLOYEE_ROLES}:
        await query.answer("Неизвестная должность.", show_alert=True)
        return EMP_ADD_ROLE
    if role in {"admin", "warehouse_manager"} and not can_manage_privileged(update):
        await query.answer("Эту роль назначает администратор или руководитель склада.", show_alert=True)
        return EMP_ADD_ROLE

    await query.answer()
    if role in selected:
        selected.remove(role)
    else:
        selected.append(role)
    await query.edit_message_reply_markup(reply_markup=roles_keyboard(selected, "username"))
    return EMP_ADD_ROLE


async def employee_add_hourly_rate_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["employee_add"]["hourly_rate"] = safe_float(update.message.text)
    await update.message.reply_text("Введите фиксированный оклад или 0:", reply_markup=cancel_keyboard("hourly_rate"))
    return EMP_ADD_FIXED_SALARY


async def employee_add_fixed_salary_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["employee_add"]["fixed_salary"] = safe_float(update.message.text)
    await update.message.reply_text("Участвует в общем фонде?", reply_markup=common_fund_keyboard())
    return EMP_ADD_COMMON_FUND


async def employee_add_common_fund_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    include_in_common_fund = query.data.endswith(":yes")
    data = context.user_data.get("employee_add") or {}
    employee = append_employee(
        full_name=data.get("full_name", ""),
        phone=data.get("phone", ""),
        telegram_user_id=data.get("telegram_user_id", ""),
        telegram_username=data.get("telegram_username", ""),
        role=data.get("role", "warehouse_employee"),
        roles=data.get("roles"),
        hourly_rate=data.get("hourly_rate", 0),
        fixed_salary=data.get("fixed_salary", 0),
        include_in_common_fund=include_in_common_fund,
    )
    context.user_data.pop("employee_add", None)

    await query.edit_message_text(
        "Сотрудник добавлен ✅\n\n"
        f"ФИО: {employee['full_name']}\n"
        f"Телефон: {employee.get('phone') or '—'}\n"
        f"Должности: {format_role_labels(employee)}\n"
        f"Часовая ставка: {money(employee['hourly_rate'])}\n"
        f"Оклад: {money(employee['fixed_salary'])}",
        reply_markup=build_employees_menu_keyboard(),
    )
    return ConversationHandler.END


async def employees_fire_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление сотрудниками доступно только руководителям.")
        return ConversationHandler.END

    await query.edit_message_text("Выберите сотрудника:", reply_markup=active_employees_keyboard("empfire"))
    return EMP_FIRE_SELECT


async def employee_fire_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee_id = query.data.replace("empfire:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END
    if privileged_target(employee) and not can_manage_privileged(update):
        await query.edit_message_text("⛔️ Недостаточно прав для увольнения этого сотрудника.")
        return ConversationHandler.END

    context.user_data["employee_fire_id"] = employee_id
    await query.edit_message_text(
        f"Уволить сотрудника {employee['full_name']}?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, уволить", callback_data="empfireconfirm:yes")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="empback:fire_select")],
            [InlineKeyboardButton("❌ Отмена", callback_data="emp:cancel")],
        ]),
    )
    return EMP_FIRE_CONFIRM


async def employee_fire_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee_id = context.user_data.get("employee_fire_id")
    if privileged_target(get_employee_by_id(employee_id)) and not can_manage_privileged(update):
        await query.edit_message_text("⛔️ Недостаточно прав для увольнения этого сотрудника.")
        return ConversationHandler.END
    employee = set_employee_active(employee_id, False)
    context.user_data.pop("employee_fire_id", None)

    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END

    await query.edit_message_text(
        f"Сотрудник {employee['full_name']} уволен ✅",
        reply_markup=build_employees_menu_keyboard(),
    )
    return ConversationHandler.END


async def employees_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление сотрудниками доступно только руководителям.")
        return ConversationHandler.END

    await query.edit_message_text("Выберите сотрудника для редактирования:", reply_markup=employees_keyboard("empedit", True))
    return EMP_EDIT_SELECT


async def employee_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee_id = query.data.replace("empedit:", "")
    employee = get_employee_by_id(employee_id)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END
    if privileged_target(employee) and not can_manage_privileged(update):
        await query.edit_message_text("⛔️ Недостаточно прав для изменения этого сотрудника.")
        return ConversationHandler.END

    context.user_data["employee_edit_id"] = employee_id
    await query.edit_message_text(
        "Что изменить?\n\n" + employee_card(employee),
        reply_markup=edit_fields_keyboard(),
    )
    return EMP_EDIT_FIELD


async def employee_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field = query.data.replace("empeditfield:", "")
    if field not in {field_key for field_key, _label in EDIT_FIELDS}:
        await query.edit_message_text("Неизвестное поле.", reply_markup=edit_fields_keyboard())
        return EMP_EDIT_FIELD

    context.user_data["employee_edit_field"] = field

    if field == "role":
        employee = get_employee_by_id(context.user_data.get("employee_edit_id"))
        selected = employee_roles(employee)
        context.user_data["employee_edit_roles"] = list(selected)
        await query.edit_message_text(
            "Выберите одну или несколько должностей:",
            reply_markup=roles_keyboard(selected, "edit_fields"),
        )
        return EMP_EDIT_VALUE

    if field == "include_in_common_fund":
        await query.edit_message_text("Участвует в общем фонде?", reply_markup=bool_edit_keyboard("empeditbool"))
        return EMP_EDIT_VALUE

    if field == "is_active":
        await query.edit_message_text("Сотрудник активен?", reply_markup=bool_edit_keyboard("empeditbool"))
        return EMP_EDIT_VALUE

    prompts = {
        "full_name": "Введите новое ФИО:",
        "phone": "Введите новый телефон или «-», чтобы очистить:",
        "telegram_user_id": "Введите новый Telegram user_id или «-», чтобы очистить:",
        "telegram_username": "Введите новый Telegram username без @ или «-», чтобы очистить:",
        "hourly_rate": "Введите новую часовую ставку:",
        "fixed_salary": "Введите новый оклад:",
    }
    await query.edit_message_text(prompts[field], reply_markup=cancel_keyboard("edit_fields"))
    return EMP_EDIT_VALUE


async def employee_edit_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    employee_id = context.user_data.get("employee_edit_id")
    field = context.user_data.get("employee_edit_field")
    value = (update.message.text or "").strip()

    if not employee_id or not field:
        await update.message.reply_text("Не удалось понять, что редактируем.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END

    if value == "-":
        value = ""

    employee = update_employee_fields(employee_id, **{field: value})
    if not employee:
        await update.message.reply_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END

    context.user_data.pop("employee_edit_field", None)
    await update.message.reply_text(
        "Сохранено ✅\n\nЧто изменить дальше?\n\n" + employee_card(employee),
        reply_markup=edit_fields_keyboard(),
    )
    return EMP_EDIT_FIELD


async def employee_edit_role_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    employee_id = context.user_data.get("employee_edit_id")
    action = query.data.replace("emprole:", "", 1)
    selected = context.user_data.setdefault("employee_edit_roles", [])

    if action != "done":
        role = action.replace("toggle:", "", 1)
        if role not in {role_key for role_key, _label in EMPLOYEE_ROLES}:
            await query.answer("Неизвестная должность.", show_alert=True)
            return EMP_EDIT_VALUE
        if role in {"admin", "warehouse_manager"} and not can_manage_privileged(update):
            await query.answer("Эту роль назначает администратор или руководитель склада.", show_alert=True)
            return EMP_EDIT_VALUE
        await query.answer()
        if role in selected:
            selected.remove(role)
        else:
            selected.append(role)
        await query.edit_message_reply_markup(reply_markup=roles_keyboard(selected, "edit_fields"))
        return EMP_EDIT_VALUE

    if not selected:
        await query.answer("Выберите хотя бы одну должность.", show_alert=True)
        return EMP_EDIT_VALUE

    await query.answer()
    employee = update_employee_fields(employee_id, roles=selected)
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END

    context.user_data.pop("employee_edit_field", None)
    context.user_data.pop("employee_edit_roles", None)
    await query.edit_message_text(
        "Сохранено ✅\n\nЧто изменить дальше?\n\n" + employee_card(employee),
        reply_markup=edit_fields_keyboard(),
    )
    return EMP_EDIT_FIELD


async def employee_edit_bool_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employee_id = context.user_data.get("employee_edit_id")
    field = context.user_data.get("employee_edit_field")
    value = query.data.endswith(":yes")

    employee = update_employee_fields(employee_id, **{field: value})
    if not employee:
        await query.edit_message_text("Сотрудник не найден.", reply_markup=build_employees_menu_keyboard())
        return ConversationHandler.END

    context.user_data.pop("employee_edit_field", None)
    await query.edit_message_text(
        "Сохранено ✅\n\nЧто изменить дальше?\n\n" + employee_card(employee),
        reply_markup=edit_fields_keyboard(),
    )
    return EMP_EDIT_FIELD


async def employee_edit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("employee_edit_id", None)
    context.user_data.pop("employee_edit_field", None)
    await query.edit_message_text("Редактирование завершено ✅", reply_markup=build_employees_menu_keyboard())
    return ConversationHandler.END


async def employees_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("empback:", "", 1)

    add_prompts = {
        "name": (EMP_ADD_NAME, "Введите ФИО сотрудника:", cancel_keyboard()),
        "phone": (EMP_ADD_PHONE, "Введите номер телефона или «-»:", cancel_keyboard("name")),
        "tg_id": (EMP_ADD_TG_ID, "Введите Telegram user_id или «-»:", cancel_keyboard("phone")),
        "username": (EMP_ADD_USERNAME, "Введите Telegram username без @ или «-»:", cancel_keyboard("tg_id")),
        "role": (
            EMP_ADD_ROLE,
            "Выберите одну или несколько должностей:",
            roles_keyboard((context.user_data.get("employee_add") or {}).get("roles"), "username"),
        ),
        "hourly_rate": (EMP_ADD_HOURLY_RATE, "Введите часовую ставку:", cancel_keyboard("role")),
        "fixed_salary": (EMP_ADD_FIXED_SALARY, "Введите фиксированный оклад или 0:", cancel_keyboard("hourly_rate")),
    }
    if target in add_prompts:
        state, text, markup = add_prompts[target]
        await query.edit_message_text(text, reply_markup=markup)
        return state
    if target == "fire_select":
        context.user_data.pop("employee_fire_id", None)
        await query.edit_message_text("Выберите сотрудника:", reply_markup=active_employees_keyboard("empfire"))
        return EMP_FIRE_SELECT
    if target == "edit_select":
        context.user_data.pop("employee_edit_id", None)
        context.user_data.pop("employee_edit_field", None)
        context.user_data.pop("employee_edit_roles", None)
        await query.edit_message_text("Выберите сотрудника для редактирования:", reply_markup=employees_keyboard("empedit", True))
        return EMP_EDIT_SELECT
    if target == "edit_fields":
        context.user_data.pop("employee_edit_field", None)
        employee = get_employee_by_id(context.user_data.get("employee_edit_id"))
        if not employee:
            return await employees_cancel(update, context)
        await query.edit_message_text("Что изменить?\n\n" + employee_card(employee), reply_markup=edit_fields_keyboard())
        return EMP_EDIT_FIELD
    return await employees_cancel(update, context)


async def employees_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("employee_add", None)
    context.user_data.pop("employee_fire_id", None)
    context.user_data.pop("employee_edit_id", None)
    context.user_data.pop("employee_edit_field", None)
    context.user_data.pop("employee_edit_roles", None)
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Действие отменено.", reply_markup=build_employees_menu_keyboard())
    elif update.message:
        await update.message.reply_text("Действие отменено.", reply_markup=build_employees_menu_keyboard())
    return ConversationHandler.END


def get_employee_handlers():
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(employees_add_start, pattern=r"^emp:add$"),
            CallbackQueryHandler(employees_edit_start, pattern=r"^emp:edit$"),
            CallbackQueryHandler(employees_fire_start, pattern=r"^emp:fire$"),
        ],
        states={
            EMP_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_name_received)],
            EMP_ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_phone_received)],
            EMP_ADD_TG_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_tg_id_received)],
            EMP_ADD_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_username_received)],
            EMP_ADD_ROLE: [CallbackQueryHandler(employee_add_role_selected, pattern=r"^emprole:")],
            EMP_ADD_HOURLY_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_hourly_rate_received)],
            EMP_ADD_FIXED_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_add_fixed_salary_received)],
            EMP_ADD_COMMON_FUND: [CallbackQueryHandler(employee_add_common_fund_selected, pattern=r"^empfund:")],
            EMP_FIRE_SELECT: [CallbackQueryHandler(employee_fire_selected, pattern=r"^empfire:")],
            EMP_FIRE_CONFIRM: [CallbackQueryHandler(employee_fire_confirmed, pattern=r"^empfireconfirm:yes$")],
            EMP_EDIT_SELECT: [CallbackQueryHandler(employee_edit_selected, pattern=r"^empedit:")],
            EMP_EDIT_FIELD: [
                CallbackQueryHandler(employee_edit_done, pattern=r"^empedit:done$"),
                CallbackQueryHandler(employee_edit_field_selected, pattern=r"^empeditfield:"),
            ],
            EMP_EDIT_VALUE: [
                CallbackQueryHandler(employee_edit_role_selected, pattern=r"^emprole:"),
                CallbackQueryHandler(employee_edit_bool_selected, pattern=r"^empeditbool:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, employee_edit_text_received),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(employees_back, pattern=r"^empback:(name|phone|tg_id|username|role|hourly_rate|fixed_salary|fire_select|edit_select|edit_fields)$"),
            CallbackQueryHandler(employees_cancel, pattern=r"^emp:cancel$"),
        ],
        name="employees_management",
        persistent=False,
    )
    return [conversation]
