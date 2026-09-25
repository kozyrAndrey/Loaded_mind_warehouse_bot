import json


ROLE_ORDER = (
    "warehouse_employee",
    "warehouse_manager",
    "brand_manager",
    "admin",
)

ROLE_LABELS = {
    "warehouse_employee": "Сотрудник склада",
    "warehouse_manager": "Руководитель склада",
    "brand_manager": "Руководитель бренда",
    "admin": "Администратор",
}


def normalize_roles(value, fallback_role=""):
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                value = raw
        if isinstance(value, str):
            value = value.replace(";", ",").split(",")

    roles = []
    for role in value or []:
        normalized = str(role or "").strip()
        if normalized in ROLE_LABELS and normalized not in roles:
            roles.append(normalized)

    fallback = str(fallback_role or "").strip()
    if fallback in ROLE_LABELS and fallback not in roles:
        roles.insert(0, fallback)

    return sorted(roles, key=lambda role: ROLE_ORDER.index(role))


def employee_roles(employee):
    if not employee:
        return []
    return normalize_roles(employee.get("roles"), employee.get("role"))


def has_role(employee, role):
    return str(role or "").strip() in employee_roles(employee)


def has_any_role(employee, roles):
    return bool(set(employee_roles(employee)) & set(roles or []))


def roles_to_storage(roles, fallback_role=""):
    return ",".join(normalize_roles(roles, fallback_role))


def primary_role(roles, fallback="warehouse_employee"):
    normalized = normalize_roles(roles, fallback)
    if not normalized:
        return fallback
    # Сохраняем наиболее привычную должность как legacy-role. Дополнительные
    # полномочия всегда берутся из полного списка roles.
    for preferred in (
        "warehouse_manager",
        "brand_manager",
        "warehouse_employee",
        "admin",
    ):
        if preferred in normalized:
            return preferred
    return normalized[0]


def format_role_labels(employee_or_roles):
    roles = (
        employee_roles(employee_or_roles)
        if isinstance(employee_or_roles, dict)
        else normalize_roles(employee_or_roles)
    )
    return ", ".join(ROLE_LABELS[role] for role in roles) or "—"
