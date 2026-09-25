# ============================================================
# НАСТРОЙКИ МОДУЛЯ ЗП
# ============================================================
# Здесь хранятся стартовые справочники сотрудников и KPI.
# При первом запуске модуль создаст/заполнит справочники в БД.
# После этого ставки, роли и user_id можно редактировать в справочнике «Сотрудники».

PAYROLL_EMPLOYEES = []  # Сотрудники Loaded Mind добавляются через бот.

PAYROLL_KPI = [
    {"kpi_id": "kpi001", "name": "Оприходование 1 слой / шарфы / ремни", "rate": 15, "is_active": True},
    {"kpi_id": "kpi002", "name": "Оприходование 2 слой", "rate": 20, "is_active": True},
    {"kpi_id": "kpi003", "name": "Оприходование 3 слой", "rate": 25, "is_active": True},
    {"kpi_id": "kpi004", "name": "Упаковка больших сумок", "rate": 15, "is_active": True},
    {"kpi_id": "kpi005", "name": "Упаковка маленьких сумок", "rate": 10, "is_active": True},
    {"kpi_id": "kpi006", "name": "Упаковка Ремни", "rate": 15, "is_active": True},
    {"kpi_id": "kpi007", "name": "Отправка", "rate": 25, "is_active": True},
    {"kpi_id": "kpi008", "name": "Сток", "rate": 15, "is_active": True},
    {"kpi_id": "kpi009", "name": "УПД для стока", "rate": 1000, "is_active": True},
    {"kpi_id": "kpi010", "name": "Пресс", "rate": 100, "is_active": True},
    {"kpi_id": "kpi011", "name": "Возврат", "rate": 15, "is_active": True},
    {"kpi_id": "kpi012", "name": "Инвент", "rate": 1000, "is_active": True},
    {"kpi_id": "kpi_7fea48c199", "name": "Сток Ламода", "rate": 50, "is_active": True},
    {"kpi_id": "kpi_cce2533d6b", "name": "Оприходование неликвида", "rate": 10, "is_active": True},
    {"kpi_id": "kpi_db8cf71af8", "name": "Подготовка расходников для производства", "rate": 5, "is_active": True},
]

# Лист для отдельной аналитики по KPI за день.
# Важно: порядок колонок здесь совпадает с порядком в справочнике KPI за день.
KPI_DAILY_COLUMNS = [
    "Упаковка 1 Cлой",
    "Упаковка 2 Слой",
    "Упаковка 3 Слой",
    "Упаковка больших сумок",
    "Упаковка маленьких сумок",
    "Упаковка Ремни",
    "Отправка",
    "Сток",
    "УПД для стока",
    "Пресс",
    "Возврат",
    "Инвент",
    "Сток Ламода",
    "Оприходование неликвида",
    "Подготовка расходников для производства",
]

# Соответствие KPI из справочника колонкам листа «KPI за день».
KPI_DAILY_COLUMN_BY_KPI_ID = {
    "kpi001": "Упаковка 1 Cлой",
    "kpi002": "Упаковка 2 Слой",
    "kpi003": "Упаковка 3 Слой",
    "kpi004": "Упаковка больших сумок",
    "kpi005": "Упаковка маленьких сумок",
    "kpi006": "Упаковка Ремни",
    "kpi007": "Отправка",
    "kpi008": "Сток",
    "kpi009": "УПД для стока",
    "kpi010": "Пресс",
    "kpi011": "Возврат",
    "kpi012": "Инвент",
    "kpi_7fea48c199": "Сток Ламода",
    "kpi_cce2533d6b": "Оприходование неликвида",
    "kpi_db8cf71af8": "Подготовка расходников для производства",
}

# Разбивка окладной части для красивой ведомости.
# Суммы здесь указываются уже за один расчетный период, то есть за половину месяца.
SALARY_FIXED_PARTS = {}

PENALTY_BONUS_EMPLOYEE_ID = ""
PENALTY_BONUS_RATE = 0.4


# ============================================================
# ТИПЫ ШТРАФОВ
# ============================================================
# manual_amount=True означает, что сумму штрафа нужно ввести вручную.
# needs_comment=True означает, что бот обязательно запросит комментарий с деталями штрафа.
# show_in_menu=False означает, что штраф не показывается кнопкой в боте и создаётся автоматически.

PENALTY_TYPES = {
    "wrong_shipping": {
        "category": "Неверная отправка",
        "name": "Неверная отправка",
        "amount": 1000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Неверная инвентаризация
    "inventory_critical_1_3": {
        "category": "Неверная инвентаризация",
        "name": "Неверная инвентаризация: 1–3 критические ошибки",
        "amount": 750,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "inventory_critical_4_5": {
        "category": "Неверная инвентаризация",
        "name": "Неверная инвентаризация: 4–5 критических ошибок",
        "amount": 1500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "inventory_critical_6_plus": {
        "category": "Неверная инвентаризация",
        "name": "Неверная инвентаризация: 6+ критических ошибок",
        "amount": 3000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "inventory_noncritical_4_plus": {
        "category": "Неверная инвентаризация",
        "name": "Неверная инвентаризация: 4+ некритических ошибок",
        "amount": None,
        "manual_amount": True,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Отчеты позже срока
    "late_warehouse_report": {
        "category": "Отчет позже срока",
        "name": "Отчет склада позже срока",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "late_receiving_report": {
        "category": "Отчет позже срока",
        "name": "Отчет приемки позже срока",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "late_yandex_pass_report": {
        "category": "Отчет позже срока",
        "name": "Отчет Яндекс/пропуска позже срока",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Опоздания и невыходы
    # «Опоздание до 10 минут — 0 руб.» не добавлено намеренно, чтобы не фиксировать
    # небольшие опоздания, на которые могли повлиять разные обстоятельства.
    "late_up_to_30": {
        "category": "Опоздание / невыход",
        "name": "Опоздание до 30 минут",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "late_30_to_2h": {
        "category": "Опоздание / невыход",
        "name": "Опоздание от 30 минут до 2 часов",
        "amount": 1000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "late_more_2h_with_warning": {
        "category": "Опоздание / невыход",
        "name": "Опоздание более 2 часов с предупреждением минимум за час",
        "amount": 1500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "late_more_2h_without_warning": {
        "category": "Опоздание / невыход",
        "name": "Опоздание более 2 часов без предупреждения минимум за час",
        "amount": 2000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "absence_no_reason": {
        "category": "Опоздание / невыход",
        "name": "Невыход без уважительной причины",
        "amount": 2500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "absence_valid_reason": {
        "category": "Опоздание / невыход",
        "name": "Невыход при наличии уважительной причины",
        "amount": None,
        "manual_amount": True,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "absence_no_reason_third_month": {
        "category": "Опоздание / невыход",
        "name": "Третий невыход без уважительной причины за месяц — вопрос об увольнении",
        "amount": 0,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": False,
    },

    # Расходники
    "consumables_wrong_count": {
        "category": "Неверный пересчет расходников",
        "name": "Неверный подсчет расходников, повлекший остановку оприходования",
        "amount": 1500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Полки
    "wrong_shelf": {
        "category": "Неверно положил товар на полку",
        "name": "Неверно положил товар на полку",
        "amount": None,
        "manual_amount": True,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Уборка
    "poor_cleaning": {
        "category": "Некачественная уборка",
        "name": "Некачественная уборка",
        "amount": None,
        "manual_amount": True,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Офисные ключи
    "keys_delivered_before_shift": {
        "category": "Офисные ключи",
        "name": "Унес офисные ключи, но довез до начала смены",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "keys_missing_during_shift": {
        "category": "Офисные ключи",
        "name": "Офисных ключей нет на месте в рабочее время",
        "amount": 1000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },

    # Оприходование товара
    "receiving_wrong_packaging": {
        "category": "Неверное оприходование товара",
        "name": "Неверная упаковка товара",
        "amount": 500,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "receiving_no_number_sign": {
        "category": "Неверное оприходование товара",
        "name": "Нет номерного знака на оприходованных товарах",
        "amount": 750,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },
    "receiving_wrong_marking_chz": {
        "category": "Неверное оприходование товара",
        "name": "Неверная маркировка товара / неверное ЧЗ",
        "amount": 1000,
        "manual_amount": False,
        "needs_comment": True,
        "show_in_menu": True,
    },

    "other": {
        "category": "Другое",
        "name": "Другое",
        "amount": None,
        "manual_amount": True,
        "needs_comment": True,
        "show_in_menu": True,
    },
}

PENALTY_AUTO_DISMISSAL_TYPE_ID = "absence_no_reason_third_month"
PENALTY_ABSENCE_NO_REASON_TYPE_ID = "absence_no_reason"

PENALTY_TYPE_GROUPS = {
    "inventory": {
        "name": "Неверная инвентаризация",
        "items": [
            "inventory_critical_1_3",
            "inventory_critical_4_5",
            "inventory_critical_6_plus",
            "inventory_noncritical_4_plus",
        ],
    },
    "late_report": {
        "name": "Отчет позже срока",
        "items": [
            "late_warehouse_report",
            "late_receiving_report",
            "late_yandex_pass_report",
        ],
    },
    "lateness_absence": {
        "name": "Опоздание / невыход",
        "items": [
            "late_up_to_30",
            "late_30_to_2h",
            "late_more_2h_with_warning",
            "late_more_2h_without_warning",
            "absence_no_reason",
            "absence_valid_reason",
        ],
    },
    "office_keys": {
        "name": "Офисные ключи",
        "items": [
            "keys_delivered_before_shift",
            "keys_missing_during_shift",
        ],
    },
    "receiving_errors": {
        "name": "Неверное оприходование товара",
        "items": [
            "receiving_wrong_packaging",
            "receiving_no_number_sign",
            "receiving_wrong_marking_chz",
        ],
    },
}


def get_penalty_category_name(penalty_type_id):
    penalty_type = PENALTY_TYPES.get(penalty_type_id, {})
    return penalty_type.get("category", "Другое")


def find_penalty_category_by_type_name(type_name):
    type_name = str(type_name or "").strip()

    for penalty_type in PENALTY_TYPES.values():
        if penalty_type.get("name") == type_name:
            return penalty_type.get("category", "Другое")

    return "Другое"


MANAGER_ROLES = {"warehouse_manager", "brand_manager", "admin"}
EMPLOYEE_ROLES = {"warehouse_employee", "warehouse_manager", "brand_manager", "admin"}


def normalize_username(username):
    return (username or "").strip().lstrip("@").lower()
