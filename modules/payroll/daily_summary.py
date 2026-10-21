"""Persistent delivery of daily warehouse summaries to brand managers."""

import asyncio
import json
import logging
from datetime import date

from sqlalchemy import Boolean, Date, Text
from sqlalchemy.orm import Mapped, mapped_column
from telegram.error import BadRequest

from modules.employees.roles import has_role
from modules.payroll.google_sheets import get_employees
from modules.payroll.report_automation import (
    format_daily_summary, submitted_reports_snapshot, summary_chunks,
)
from modules.schedule.config import parse_date
from modules.storage.postgres import Base, get_engine, session_scope


class DailySummary(Base):
    __tablename__ = "payroll_daily_summaries"

    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deliveries: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


_locks = {}


def init_daily_summary_storage():
    Base.metadata.create_all(get_engine(), tables=[DailySummary.__table__])


def summary_state(day):
    with session_scope() as session:
        row = session.get(DailySummary, day)
        return json.loads(row.deliveries) if row else {}


def save_summary_state(day, deliveries, pending=True):
    with session_scope() as session:
        row = session.get(DailySummary, day)
        if row is None:
            row = DailySummary(report_date=day)
            session.add(row)
        row.deliveries = json.dumps(deliveries, ensure_ascii=False)
        row.pending = pending


async def deliver_summary(context, day, chat_id, chunks, deliveries):
    messages = deliveries.setdefault(str(chat_id), [])
    for index, text in enumerate(chunks):
        if index < len(messages):
            previous = messages[index]
            if previous["text"] == text:
                continue
            try:
                await context.bot.edit_message_text(chat_id=int(chat_id), message_id=previous["id"], text=text)
            except BadRequest as error:
                reason = str(error).lower()
                if "message is not modified" in reason:
                    pass
                elif "message to edit not found" in reason:
                    message = await context.bot.send_message(chat_id=int(chat_id), text=text)
                    previous["id"] = message.message_id
                else:
                    raise
            previous["text"] = text
        else:
            message = await context.bot.send_message(chat_id=int(chat_id), text=text)
            messages.append({"id": message.message_id, "text": text})
        # Persist each fragment so retrying one recipient does not resend earlier parts.
        save_summary_state(day, deliveries)

    while len(messages) > len(chunks):
        previous = messages[-1]
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=previous["id"])
        except BadRequest as error:
            reason = str(error).lower()
            if "message to delete not found" not in reason:
                try:
                    await context.bot.edit_message_text(
                        chat_id=int(chat_id), message_id=previous["id"],
                        text="Сводка обновлена. Эта часть больше не используется.",
                    )
                except BadRequest as edit_error:
                    if "message is not modified" not in str(edit_error).lower():
                        raise
        messages.pop()
        save_summary_state(day, deliveries)


async def refresh_daily_summary(context, report_date):
    """Send submitted daily reports after the manager report is saved."""
    day = parse_date(report_date)
    lock = _locks.setdefault(day, asyncio.Lock())
    async with lock:
        try:
            deliveries = summary_state(day)
            save_summary_state(day, deliveries)
            snapshot = submitted_reports_snapshot(report_date)
            chunks = summary_chunks(format_daily_summary(report_date, snapshot))
            recipients = {
                str(employee.get("telegram_user_id", "")).strip()
                for employee in get_employees(include_inactive=False)
                if has_role(employee, "brand_manager")
                and str(employee.get("telegram_user_id", "")).strip()
            }
            failed = not recipients
            if failed:
                logging.warning("Нет получателей сводки склада за %s", report_date)
            for chat_id in sorted(recipients):
                try:
                    await deliver_summary(context, day, chat_id, chunks, deliveries)
                except Exception:
                    failed = True
                    logging.exception("Не удалось доставить сводку за %s получателю %s", report_date, chat_id)
            save_summary_state(day, deliveries, pending=failed)
        except Exception:
            logging.exception("Не удалось отправить сводку склада за %s", report_date)
