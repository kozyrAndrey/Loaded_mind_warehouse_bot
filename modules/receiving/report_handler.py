import logging
import uuid
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from config import GROUP_CHAT_ID, RECEIVING_REPORT_TOPIC_ID
from core.keyboards import build_receiving_report_type_keyboard
from modules.receiving.postgres_storage import (
    build_receiving_report_text, get_last_records_text,
    mark_receiving_rows_exported, unexported_receiving_ids_for_date,
)


def split_report(text, limit=3900):
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current.rstrip())
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def last(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        get_last_records_text(limit=10), reply_markup=build_receiving_report_type_keyboard()
    )


async def choose_date(update, context):
    query = update.callback_query
    await query.answer()
    today = datetime.now().date()
    rows = [[InlineKeyboardButton(day.strftime("%d.%m.%Y"), callback_data=f"lmrecv:report:{day:%d.%m.%Y}")]
            for day in (today, today - timedelta(days=1))]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:receiving")])
    await query.edit_message_text("Выберите дату отчёта:", reply_markup=InlineKeyboardMarkup(rows))


async def export(update, context):
    query = update.callback_query
    await query.answer()
    report_date = query.data.rsplit(":", 1)[-1]
    record_ids = unexported_receiving_ids_for_date(report_date)
    if not record_ids:
        await query.edit_message_text("За эту дату нет новых записей для выгрузки.", reply_markup=build_receiving_report_type_keyboard())
        return
    report_text = build_receiving_report_text(
        report_date, exported_by=update.effective_user.full_name,
        only_unexported=True, group_by_type=True, record_ids=record_ids,
    )
    chunks = split_report(report_text)
    if not GROUP_CHAT_ID or not RECEIVING_REPORT_TOPIC_ID:
        for chunk in chunks:
            await query.message.reply_text(chunk)
        await query.edit_message_text(
            "Тема отчётов пока не настроена. Отчёт показан здесь; записи останутся невыгруженными.",
            reply_markup=build_receiving_report_type_keyboard(),
        )
        return
    message_ids = []
    try:
        for chunk in chunks:
            sent = await context.bot.send_message(
                chat_id=int(GROUP_CHAT_ID), message_thread_id=int(RECEIVING_REPORT_TOPIC_ID), text=chunk
            )
            message_ids.append(sent.message_id)
        mark_receiving_rows_exported(
            report_date, update.effective_user.full_name, uuid.uuid4().hex,
            GROUP_CHAT_ID, RECEIVING_REPORT_TOPIC_ID, message_ids, record_ids=record_ids,
        )
    except Exception:
        logging.exception("Не удалось выгрузить отчёт приёмки")
        for message_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=int(GROUP_CHAT_ID), message_id=message_id)
            except Exception:
                logging.exception("Не удалось удалить часть незавершённого отчёта")
        await query.edit_message_text("Не удалось выгрузить отчёт. Записи остались невыгруженными.")
        return
    await query.edit_message_text(
        f"✅ Отчёт за {report_date} отправлен в тему.", reply_markup=build_receiving_report_type_keyboard()
    )


def get_loaded_mind_report_handlers():
    return [
        CallbackQueryHandler(last, pattern=r"^lmrecv:last$"),
        CallbackQueryHandler(choose_date, pattern=r"^lmrecv:report$"),
        CallbackQueryHandler(export, pattern=r"^lmrecv:report:\d{2}\.\d{2}\.\d{4}$"),
    ]
