from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from modules.receiving.products import CATEGORIES, SIZES
from modules.employees.roles import has_any_role


REPLY_MENU = {
    "📦 Приёмка": "receiving",
    "↩️ Возвраты": "returns",
    "🧾 Расходники": "consumables",
    "🏷 Маркировка / ЧЗ": "marking",
    "💰 ЗП и KPI": "payroll",
    "📅 Расписание": "schedule",
    "👥 Сотрудники": "employees",
    "⚙️ Управление ботом": "admin",
}


def build_reply_main_keyboard(employee, enabled_modules=None):
    if enabled_modules is None:
        from core.module_control import enabled_module_keys
        enabled_modules = enabled_module_keys()
    manager = has_any_role(employee, {"warehouse_manager", "admin"})
    staff_manager = has_any_role(employee, {"warehouse_manager", "brand_manager", "admin"})
    labels = []
    for label, module_key in REPLY_MENU.items():
        if module_key != "admin" and module_key not in enabled_modules:
            continue
        if module_key in {"schedule", "admin"} and not manager:
            continue
        if module_key == "employees" and not staff_manager:
            continue
        labels.append(label)
    rows = [[KeyboardButton(label)] for label in labels]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def build_start_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚀 Старт", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# ГЛАВНОЕ МЕНЮ: ВЫБОР РАЗДЕЛА
# ============================================================

def build_main_menu_keyboard(recruitment_tester=False, manager=False, admin=False, enabled_modules=None):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 Главное меню", callback_data="menu:start")]]
    )


def build_marking_menu_keyboard(manager=False):
    keyboard = [
        [InlineKeyboardButton("🔎 Поиск товара / кода", callback_data="marking:search")],
        [InlineKeyboardButton("🏷 Дубликат ЧЗ 58×40", callback_data="marking:duplicate_chz")],
        [InlineKeyboardButton("🏷 Дубликат ЧЗ 75×120", callback_data="marking:duplicate_chz_75x120")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_employees_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить сотрудника", callback_data="emp:add")],
        [InlineKeyboardButton("✏️ Изменить сотрудника", callback_data="emp:edit")],
        [InlineKeyboardButton("🚫 Уволить сотрудника", callback_data="emp:fire")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_products_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить товар", callback_data="prodadmin:add")],
        [InlineKeyboardButton("✏️ Изменить товар", callback_data="prodadmin:edit")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_receiving_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Оприходовать товар", callback_data="menu:add")],
        [InlineKeyboardButton("📋 Последние записи", callback_data="menu:last")],
        [InlineKeyboardButton("📤 Выгрузка отчета", callback_data="report:choose_date")],
        [InlineKeyboardButton("🗑 Удалить запись", callback_data="recvdel:choose")],
        [InlineKeyboardButton("🧹 Удалить отчет из темы", callback_data="recvrepdel:choose")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_receiving_report_type_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Новая поставка", callback_data="recvtype:new_supply")],
        [InlineKeyboardButton("📦 Неликвид", callback_data="recvtype:illiquid")],
        [InlineKeyboardButton("🚫 Отбракованный товар", callback_data="recvtype:rejected")],
        [InlineKeyboardButton("📋 Последние записи", callback_data="lmrecv:last")],
        [InlineKeyboardButton("📤 Выгрузить отчёт", callback_data="lmrecv:report")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_returns_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚚 СДЭК", callback_data="menu:return:cdek")],
        [InlineKeyboardButton("🏬 Шоу-рум", callback_data="menu:return:showroom")],
        [InlineKeyboardButton("✏️ Изменить запись", callback_data="retadmin:edit")],
        [InlineKeyboardButton("🗑 Удалить запись", callback_data="retadmin:delete")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_consumables_menu_keyboard(manager=False):
    keyboard = [
        [InlineKeyboardButton("📥 Приемка расходника", callback_data="cons:receipt_menu")],
        [InlineKeyboardButton("📄 Остатки (PDF)", callback_data="cons:stock")],
        [InlineKeyboardButton("🔢 Пересчет расходников", callback_data="cons:module_counting")],
    ]
    if manager:
        keyboard.extend(
            [
                [InlineKeyboardButton("➕ Добавить расходник в учет", callback_data="cons:add_item")],
                [InlineKeyboardButton("🗑 Удалить расходник из учета", callback_data="cons:delete_item")],
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")])
    return InlineKeyboardMarkup(keyboard)


def build_consumables_receipt_menu_keyboard(manager=False):
    keyboard = [[InlineKeyboardButton("➕ Принять расходник", callback_data="cons:receipt")]]
    if manager:
        keyboard.extend(
            [
                [InlineKeyboardButton("✏️ Изменить приемку", callback_data="cons:receipt_edit")],
                [InlineKeyboardButton("🗑 Удалить приемку", callback_data="cons:receipt_delete")],
            ]
        )
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:consumables")])
    return InlineKeyboardMarkup(keyboard)


def build_consumables_supplies_menu_keyboard(manager=False):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Принять расходники", callback_data="cons:receipt")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:consumables")],
        ]
    )


def build_consumables_suppliers_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ Добавить поставщика", callback_data="cons:add_supplier")],
        [InlineKeyboardButton("✏️ Изменить поставщика", callback_data="cons:edit_supplier")],
        [InlineKeyboardButton("🚫 Удалить поставщика", callback_data="cons:delete_supplier")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="cons:module_supplies")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_consumables_counting_menu_keyboard(manager=False):
    if manager:
        keyboard = [
            [InlineKeyboardButton("🔢 Пересчет расходников", callback_data="cons:inventory_count")],
            [InlineKeyboardButton("🔎 Проверка пересчета", callback_data="cons:inventory_review")],
            [InlineKeyboardButton("📄 PDF пересчета", callback_data="cons:inventory_pdf")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:consumables")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🔢 Пересчет расходников", callback_data="cons:inventory_count")],
            [InlineKeyboardButton("📄 PDF пересчета", callback_data="cons:inventory_pdf")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="section:consumables")],
        ]
    return InlineKeyboardMarkup(keyboard)


def build_report_date_keyboard():
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    today_text = today.strftime("%d.%m.%Y")
    yesterday_text = yesterday.strftime("%d.%m.%Y")

    keyboard = [
        [InlineKeyboardButton(today_text, callback_data=f"report:date:{today_text}")],
        [InlineKeyboardButton(yesterday_text, callback_data=f"report:date:{yesterday_text}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")],
    ]

    return InlineKeyboardMarkup(keyboard)


def build_incoming_date_keyboard():
    today = datetime.now()
    yesterday = today - timedelta(days=1)

    today_text = today.strftime("%d.%m.%Y")
    yesterday_text = yesterday.strftime("%d.%m.%Y")

    keyboard = [
        [InlineKeyboardButton(today_text, callback_data=f"incdate:{today_text}")],
        [InlineKeyboardButton(yesterday_text, callback_data=f"incdate:{yesterday_text}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")],
    ]

    return InlineKeyboardMarkup(keyboard)

# ============================================================
# ОПРИХОДОВАНИЕ: ГРУППА → МОДЕЛЬ → ЦВЕТ → РАЗМЕР
# ============================================================

def build_category_keyboard(back_callback="menu:start", back_text="⬅️ Главное меню"):
    keyboard = []

    for category_id, category_data in CATEGORIES.items():
        keyboard.append(
            [InlineKeyboardButton(category_data["name"], callback_data=f"cat:{category_id}")]
        )

    keyboard.append([InlineKeyboardButton(back_text, callback_data=back_callback)])
    return InlineKeyboardMarkup(keyboard)


def build_models_keyboard(category_id, home_callback="menu:start", home_text="🏠 Главное меню"):
    keyboard = []
    models = CATEGORIES[category_id]["models"]

    for model_id, model_data in models.items():
        keyboard.append(
            [InlineKeyboardButton(model_data["name"], callback_data=f"model:{model_id}")]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад к группам", callback_data="back:categories")])
    keyboard.append([InlineKeyboardButton(home_text, callback_data=home_callback)])
    return InlineKeyboardMarkup(keyboard)


def build_product_colors_keyboard(category_id, model_id, home_callback="menu:start", home_text="🏠 Главное меню"):
    keyboard = []
    variants = CATEGORIES[category_id]["models"][model_id]["variants"]

    for variant_data in variants.values():
        color = variant_data["color"]
        text = "Выбрать" if color == "ONE COLOR" else color

        keyboard.append(
            [InlineKeyboardButton(text, callback_data=f"prod:{variant_data['id']}")]
        )

    keyboard.append([InlineKeyboardButton("⬅️ Назад к моделям", callback_data="back:models")])
    keyboard.append([InlineKeyboardButton(home_text, callback_data=home_callback)])
    return InlineKeyboardMarkup(keyboard)


def build_sizes_keyboard(home_callback="menu:start", home_text="🏠 Главное меню"):
    keyboard = []

    row = []
    for size in SIZES:
        row.append(InlineKeyboardButton(size, callback_data=f"size:{size}"))

        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⬅️ Назад к цветам", callback_data="back:colors")])
    keyboard.append([InlineKeyboardButton(home_text, callback_data=home_callback)])
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# КНОПКИ ДЛЯ ВОЗВРАТОВ
# ============================================================

def build_return_nav_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
                InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
            ]
        ]
    )


def build_return_category_keyboard():
    keyboard = []

    for category_id, category_data in CATEGORIES.items():
        keyboard.append(
            [InlineKeyboardButton(category_data["name"], callback_data=f"retcat:{category_id}")]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
            InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_return_models_keyboard(category_id):
    keyboard = []
    models = CATEGORIES[category_id]["models"]

    for model_id, model_data in models.items():
        keyboard.append(
            [InlineKeyboardButton(model_data["name"], callback_data=f"retmodel:{model_id}")]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
            InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_return_product_colors_keyboard(category_id, model_id):
    keyboard = []
    variants = CATEGORIES[category_id]["models"][model_id]["variants"]

    for variant_data in variants.values():
        color = variant_data["color"]
        text = "Выбрать" if color == "ONE COLOR" else color

        keyboard.append(
            [InlineKeyboardButton(text, callback_data=f"retprod:{variant_data['id']}")]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
            InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def build_return_sizes_keyboard():
    keyboard = []

    row = []
    for size in SIZES:
        row.append(InlineKeyboardButton(size, callback_data=f"retsize:{size}"))

        if len(row) == 3:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="ret:back"),
            InlineKeyboardButton("❌ Отмена", callback_data="ret:cancel"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)
