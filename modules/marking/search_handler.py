import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from modules.moysklad.search import search_products


WAIT_QUERY = 1900


async def start(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Введите название, штрихкод или полный код ЧЗ для поиска в МойСклад.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="marking:search:cancel")]]),
    )
    return WAIT_QUERY


async def lookup(update, context):
    try:
        products = await asyncio.to_thread(search_products, update.message.text)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return WAIT_QUERY
    except Exception:
        logging.exception("Не удалось найти товар в МойСклад")
        await update.message.reply_text("МойСклад сейчас недоступен. Попробуйте позже.")
        return WAIT_QUERY
    if not products:
        await update.message.reply_text("Товар не найден. Попробуйте другой запрос.")
        return WAIT_QUERY
    lines = ["🔎 Найдено в МойСклад:"]
    for product in products:
        article = f" · арт. {product['article']}" if product["article"] else ""
        size = f" · размер {product['size']}" if product["size"] else ""
        lines.append(f"\n{product['name']}{size}{article}")
    await update.message.reply_text("\n".join(lines)[:4000])
    return ConversationHandler.END


async def cancel(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Поиск отменён.")
    return ConversationHandler.END


def get_marking_search_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start, pattern=r"^marking:search$")],
        states={WAIT_QUERY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, lookup),
            CallbackQueryHandler(cancel, pattern=r"^marking:search:cancel$"),
        ]},
        fallbacks=[CallbackQueryHandler(cancel, pattern=r"^marking:search:cancel$")],
    )
