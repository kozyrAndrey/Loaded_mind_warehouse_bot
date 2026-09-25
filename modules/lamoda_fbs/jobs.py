import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    LAMODA_REMINDER_TIME,
    LAMODA_SYNC_INTERVAL_MINUTES,
)
from modules.lamoda_fbs.services import get_client, sync_lamoda_statuses
from modules.lamoda_fbs.storage import pending_counts
from modules.tasks.storage import get_warehouse_managers
from core.module_control import is_module_enabled


logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


async def lamoda_sync_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("lamoda"):
        return
    client = get_client()
    if not client.configured:
        return
    try:
        result = await sync_lamoda_statuses(client)
        logger.info("Lamoda sync complete: %s", result)
    except Exception:
        logger.exception("Lamoda periodic sync failed")


async def lamoda_marking_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    if not is_module_enabled("lamoda"):
        return
    try:
        counts = pending_counts()
        withdrawal = counts.get("WAITING_WITHDRAWAL", 0)
        reintro = counts.get("WAITING_REINTRODUCTION", 0)
        expected = counts.get("RETURN_EXPECTED", 0)
        problems = counts.get("NEEDS_RECONCILIATION", 0)
        if not any((withdrawal, reintro, expected, problems)):
            return
        text = (
            "⚠️ Операции ЧЗ Lamoda\n\n"
            f"На вывод из оборота: {withdrawal}\n"
            f"На возврат в оборот: {reintro}\n"
            f"Ожидаются физически: {expected}\n"
            f"Проблемные операции: {problems}"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏷 Открыть операции ЧЗ", callback_data="lamoda:marking")],
            [InlineKeyboardButton("🛒 Lamoda FBS", callback_data="section:lamoda")],
        ])
        for manager in get_warehouse_managers():
            user_id = str(manager.get("telegram_user_id") or "").strip()
            if not user_id:
                continue
            try:
                await context.bot.send_message(chat_id=int(user_id), text=text, reply_markup=keyboard)
            except Exception:
                logger.exception("Could not send Lamoda reminder to manager_id=%s", user_id)
    except Exception:
        logger.exception("Lamoda marking reminder failed")


def _reminder_time():
    try:
        hour_text, minute_text = str(LAMODA_REMINDER_TIME).split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text), tzinfo=MSK)
    except (TypeError, ValueError):
        logger.warning("Invalid LAMODA_REMINDER_TIME=%r; using 10:00", LAMODA_REMINDER_TIME)
        return time(hour=10, minute=0, tzinfo=MSK)


def setup_lamoda_jobs(app):
    if not app.job_queue:
        logger.warning("JobQueue is unavailable; Lamoda jobs are disabled")
        return
    interval = max(int(LAMODA_SYNC_INTERVAL_MINUTES or 10), 1) * 60
    if not app.job_queue.get_jobs_by_name("lamoda_status_sync"):
        app.job_queue.run_repeating(
            lamoda_sync_job, interval=interval, first=30,
            name="lamoda_status_sync",
        )
    if not app.job_queue.get_jobs_by_name("lamoda_marking_reminder"):
        app.job_queue.run_daily(
            lamoda_marking_reminder_job, time=_reminder_time(),
            name="lamoda_marking_reminder",
        )
