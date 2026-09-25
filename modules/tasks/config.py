from datetime import timedelta

from modules.employees.roles import has_any_role


TASK_TYPE_WAREHOUSE = "warehouse"
TASK_TYPE_GENERAL = "general"

TASK_TYPE_LABELS = {
    TASK_TYPE_WAREHOUSE: "Складская",
    TASK_TYPE_GENERAL: "Нескладская",
}

TASK_STATUS_ACTIVE = "active"
TASK_STATUS_DONE = "done"
TASK_STATUS_CANCELLED = "cancelled"

TASK_STATUS_LABELS = {
    TASK_STATUS_ACTIVE: "невыполнено",
    TASK_STATUS_DONE: "выполнено",
    TASK_STATUS_CANCELLED: "отменено",
}

TASK_SOURCE_MANUAL = "manual"
TASK_SOURCE_TEMPLATE = "template"

ASSIGNEE_MODE_WORKING_TODAY = "working_today"
ASSIGNEE_MODE_SPECIFIC = "specific"
ASSIGNEE_MODE_NONE = "none"

TASK_MANAGER_ROLES = {"warehouse_manager", "brand_manager"}
WAREHOUSE_MANAGER_ROLE = "warehouse_manager"

TASK_DEADLINES = [f"{hour:02d}:00" for hour in range(11, 24)]
NO_DEADLINE_TEXT = "Без дедлайна"

WEEKDAY_NAMES = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

DEFAULT_WEEKLY_TASK_TEMPLATES = [
    {"weekday": 0, "task_type": TASK_TYPE_WAREHOUSE, "description": "Пересчет расходников", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 0, "task_type": TASK_TYPE_WAREHOUSE, "description": "Отправки", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 0, "task_type": TASK_TYPE_GENERAL, "description": "Составить план закупок на расходники, согласовать его и запросить счета на оплату", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 1, "task_type": TASK_TYPE_GENERAL, "description": "Работа с ЧЗ (Ошибки при отправках, возврат / вывод ЧЗ с рума)", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 1, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 2, "task_type": TASK_TYPE_WAREHOUSE, "description": "Забрать возвраты СДЭК и разобрать их", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 2, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 2, "task_type": TASK_TYPE_GENERAL, "description": "Работа с ЧЗ (Ошибки при отправках, возврат / вывод ЧЗ с рума)", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 3, "task_type": TASK_TYPE_WAREHOUSE, "description": "Отправки", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 3, "task_type": TASK_TYPE_WAREHOUSE, "description": "Инвентаризация в шоу-румах", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 3, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 4, "task_type": TASK_TYPE_WAREHOUSE, "description": "Инвентаризация", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 4, "task_type": TASK_TYPE_GENERAL, "description": "Проверить расписание на следующую неделю", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 4, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 4, "task_type": TASK_TYPE_GENERAL, "description": "Работа с ЧЗ (Ошибки при отправках, возврат / вывод ЧЗ с рума)", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 5, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 6, "task_type": TASK_TYPE_WAREHOUSE, "description": "Генеральная уборка", "assignee_mode": ASSIGNEE_MODE_WORKING_TODAY, "deadline": ""},
    {"weekday": 6, "task_type": TASK_TYPE_GENERAL, "description": "Контроль производства расходников", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
    {"weekday": 6, "task_type": TASK_TYPE_GENERAL, "description": "Работа с ЧЗ (Ошибки при отправках, возврат / вывод ЧЗ с рума)", "assignee_mode": ASSIGNEE_MODE_NONE, "deadline": ""},
]


def is_tasks_manager(employee):
    return has_any_role(employee, TASK_MANAGER_ROLES)


def get_week_start_for_date(value):
    return value - timedelta(days=value.weekday())
