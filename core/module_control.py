import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from modules.storage.postgres import Base, get_engine, session_scope


logger = logging.getLogger(__name__)


# Единый реестр управляемых разделов. Порядок используется в админ-панели.
MODULES = OrderedDict(
    (
        ("payroll", "💰 Расчет ЗП"),
        ("schedule", "📅 Расписание"),
        ("consumables", "🧾 Расходники"),
        ("marking", "🏷 Маркировка"),
        ("employees", "👥 Сотрудники"),
        ("receiving", "📦 Оприходование"),
        ("returns", "↩️ Возвраты"),
    )
)


class BotModuleSetting(Base):
    __tablename__ = "bot_module_settings"

    module_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(100))
    updated_by_name: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )


class BotModuleAudit(Base):
    __tablename__ = "bot_module_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_by_user_id: Mapped[str | None] = mapped_column(String(100))
    changed_by_name: Mapped[str | None] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


_CACHE_TTL_SECONDS = 5
_cache_lock = threading.Lock()
_cached_states = None
_cache_expires_at = 0.0


def init_module_control_storage():
    Base.metadata.create_all(
        get_engine(),
        tables=[BotModuleSetting.__table__, BotModuleAudit.__table__],
    )
    with session_scope() as session:
        existing = set(session.scalars(select(BotModuleSetting.module_key)).all())
        for module_key in MODULES:
            if module_key not in existing:
                session.add(BotModuleSetting(module_key=module_key, enabled=True))
    invalidate_module_cache()
    return True


def invalidate_module_cache():
    global _cached_states, _cache_expires_at
    with _cache_lock:
        _cached_states = None
        _cache_expires_at = 0.0


def get_module_states(force=False):
    global _cached_states, _cache_expires_at
    now = time.monotonic()
    with _cache_lock:
        if not force and _cached_states is not None and now < _cache_expires_at:
            return dict(_cached_states)

    with session_scope() as session:
        stored = {
            row.module_key: bool(row.enabled)
            for row in session.scalars(select(BotModuleSetting)).all()
        }
    states = {module_key: stored.get(module_key, True) for module_key in MODULES}

    with _cache_lock:
        _cached_states = dict(states)
        _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
    return states


def enabled_module_keys():
    try:
        return {
            module_key
            for module_key, enabled in get_module_states().items()
            if enabled
        }
    except Exception:
        # Недоступность таблицы настроек не должна выключить весь рабочий бот.
        logger.exception("Не удалось прочитать настройки модулей; используем fail-open")
        fallback = {module_key: True for module_key in MODULES}
        global _cached_states, _cache_expires_at
        with _cache_lock:
            _cached_states = fallback
            _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
        return set(fallback)


def is_module_enabled(module_key):
    if module_key not in MODULES:
        return True
    return module_key in enabled_module_keys()


def set_module_enabled(module_key, enabled, user):
    if module_key not in MODULES:
        raise ValueError(f"Неизвестный модуль: {module_key}")

    user_id = str(getattr(user, "id", "") or "")
    user_name = str(getattr(user, "full_name", "") or "")
    with session_scope() as session:
        setting = session.get(BotModuleSetting, module_key)
        if setting is None:
            setting = BotModuleSetting(module_key=module_key)
            session.add(setting)
        setting.enabled = bool(enabled)
        setting.updated_by_user_id = user_id or None
        setting.updated_by_name = user_name or None
        setting.updated_at = datetime.now()
        session.add(
            BotModuleAudit(
                module_key=module_key,
                enabled=bool(enabled),
                changed_by_user_id=user_id or None,
                changed_by_name=user_name or None,
            )
        )

    invalidate_module_cache()
    # Заполняем кеш новым состоянием сразу, без пятисекундного окна.
    get_module_states(force=True)


def list_module_audit(limit=10):
    with session_scope() as session:
        rows = session.scalars(
            select(BotModuleAudit)
            .order_by(BotModuleAudit.changed_at.desc(), BotModuleAudit.id.desc())
            .limit(int(limit))
        ).all()
        return [
            {
                "module_key": row.module_key,
                "enabled": bool(row.enabled),
                "changed_by_user_id": row.changed_by_user_id or "",
                "changed_by_name": row.changed_by_name or "",
                "changed_at": row.changed_at,
            }
            for row in rows
        ]


# Префиксы callback_data всех модулей. Сначала идут более специфичные значения.
CALLBACK_PREFIXES = {
    "payroll": (
        "section:payroll", "pay:", "payback:", "payperiod:", "period:",
        "periodeditpay:", "periodfield:", "periodpay:", "bonus:", "bonusdel:",
        "bonusdelconfirm:", "vacation:", "vacationdel:", "vacationdelconfirm:",
        "vacationedit:", "vacemp:", "expense:", "expdel:", "expdelconfirm:",
        "addpay", "driver", "kpimgr", "bnemp:", "cleanup:", "crdate:",
        "cremp:", "crkpi:", "crlunch:", "crshift:", "eddate:", "edemp:",
        "editdelete:", "editfield:", "editkpi:", "editlunch:", "editnewdate:",
        "edittasks:", "edkpi:", "edshift:", "exemp:", "mgrdate:", "mgrwiz:",
        "penalty:", "penaltyphotos:", "pngrp:", "pntype:", "pnemp:", "salemp:",
    ),
    "schedule": ("section:schedule", "sch"),
    "consumables": ("section:consumables", "cons", "receipt:"),
    "marking": ("section:marking", "marking:"),
    "employees": ("section:employees", "emp"),
    "receiving": ("section:receiving", "menu:add", "menu:last", "incdate:", "report:", "recv", "lmrecv:", "specrep:", "recvtype:", "cat:", "model:", "prod:", "size:", "back:"),
    "returns": ("section:returns", "menu:return", "ret", "lmret:"),
}


def module_for_callback(callback_data):
    data = str(callback_data or "")
    if not data or data == "menu:start" or data == "section:admin" or data.startswith("admin:"):
        return None
    for module_key, prefixes in CALLBACK_PREFIXES.items():
        if data.startswith(prefixes):
            return module_key
    return None


def _clear_current_conversations(update, application):
    try:
        for handlers in application.handlers.values():
            for handler in handlers:
                if not isinstance(handler, ConversationHandler):
                    continue
                try:
                    key = handler._get_key(update)
                    handler._conversations.pop(key, None)
                except Exception:
                    logger.exception("Не удалось сбросить состояние отключенного модуля")
    except Exception:
        logger.exception("Не удалось сбросить диалоги отключенного модуля")


async def module_access_guard(update, context):
    callback_data = update.callback_query.data if update.callback_query else ""

    message_text = str(getattr(update.effective_message, "text", "") or "").strip()
    command = message_text.split(maxsplit=1)[0].split("@", 1)[0].lower() if message_text else ""
    if command in {"/start", "/whoami", "/whereami", "/db_status"}:
        if command == "/start":
            context.user_data.pop("_active_module", None)
        return

    if callback_data == "menu:start" or callback_data == "section:admin" or callback_data.startswith("admin:"):
        context.user_data.pop("_active_module", None)
        return

    module_key = module_for_callback(callback_data) if callback_data else None
    if not module_key and command == "/last":
        module_key = "receiving"
    if module_key:
        context.user_data["_active_module"] = module_key
    else:
        module_key = context.user_data.get("_active_module")

    if not module_key or is_module_enabled(module_key):
        return

    _clear_current_conversations(update, context.application)
    context.user_data.clear()
    text = f"⛔️ Модуль «{MODULES[module_key]}» временно отключён руководством."

    # Импорты внутри функции не создают циклическую зависимость с клавиатурами.
    from core.keyboards import build_main_menu_keyboard
    from modules.employees.roles import has_role
    from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager

    employee = find_employee_for_telegram_user(update.effective_user)
    keyboard = build_main_menu_keyboard(
        manager=is_manager(employee),
        admin=has_role(employee, "admin") or has_role(employee, "warehouse_manager"),
    )

    if update.callback_query:
        query = update.callback_query
        await query.answer(text, show_alert=True)
        if update.effective_chat and update.effective_chat.type == "private":
            try:
                await query.edit_message_text(text, reply_markup=keyboard)
            except Exception:
                await query.message.reply_text(text, reply_markup=keyboard)
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=keyboard)

    raise ApplicationHandlerStop
