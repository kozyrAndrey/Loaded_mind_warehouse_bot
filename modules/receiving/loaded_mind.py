import asyncio
import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from modules.moysklad.search import search_products
from modules.receiving.postgres_storage import save_moysklad_incoming_good


SEARCH, SELECT_PRODUCT, SELECT_DATE, PACKED, DEFECTIVE, REWORK, CONFIRM = range(1800, 1807)
TYPE_LABELS = {
    "new_supply": "Новая поставка",
    "illiquid": "Неликвид",
    "rejected": "Отбракованный товар",
}


def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="lmrecv:cancel")]])


def results_keyboard(products):
    rows = [
        [InlineKeyboardButton(product["display_name"][:60], callback_data=f"lmrecv:product:{index}")]
        for index, product in enumerate(products)
    ]
    rows.extend([
        [InlineKeyboardButton("🔎 Новый поиск", callback_data="lmrecv:new_search")],
        [InlineKeyboardButton("✅ Завершить приёмку", callback_data="lmrecv:finish")],
    ])
    return InlineKeyboardMarkup(rows)


async def start(update, context):
    query = update.callback_query
    await query.answer()
    report_type = query.data.split(":", 1)[1]
    if report_type not in TYPE_LABELS:
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["lm_receiving"] = {"report_type": report_type}
    await query.edit_message_text(
        f"📦 {TYPE_LABELS[report_type]}\n\nОтсканируйте штрихкод/код ЧЗ или введите часть названия товара.",
        reply_markup=cancel_keyboard(),
    )
    return SEARCH


async def search(update, context):
    query_text = (update.message.text or "").strip()
    previous_results = context.user_data["lm_receiving"].get("results")
    try:
        products = await asyncio.to_thread(search_products, query_text)
    except Exception:
        logging.exception("Ошибка поиска номенклатуры в МойСклад")
        await update.message.reply_text("Не удалось выполнить поиск в МойСклад. Проверьте запрос или повторите позже.")
        return SELECT_PRODUCT if previous_results else SEARCH
    if not products:
        await update.message.reply_text("Товар не найден. Введите другой штрихкод, код ЧЗ или название.")
        return SELECT_PRODUCT if previous_results else SEARCH
    context.user_data["lm_receiving"]["results"] = products
    await update.message.reply_text(
        "Выберите товар или введите новый запрос:", reply_markup=results_keyboard(products)
    )
    return SELECT_PRODUCT


async def select_product(update, context):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.rsplit(":", 1)[-1])
        product = context.user_data["lm_receiving"]["results"][index]
    except (KeyError, IndexError, ValueError):
        await query.edit_message_text("Поиск устарел. Начните приёмку заново.")
        return ConversationHandler.END
    context.user_data["lm_receiving"]["product"] = product
    today = datetime.now().date()
    rows = [
        [InlineKeyboardButton(day.strftime("%d.%m.%Y"), callback_data=f"lmrecv:date:{day:%d.%m.%Y}")]
        for day in (today, today - timedelta(days=1))
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="lmrecv:cancel")])
    await query.edit_message_text(
        f"{product['display_name']}\n\nВыберите дату:", reply_markup=InlineKeyboardMarkup(rows)
    )
    return SELECT_DATE


async def select_date(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["lm_receiving"]["record_date"] = query.data.rsplit(":", 1)[-1]
    await query.edit_message_text("Сколько единиц принято?", reply_markup=cancel_keyboard())
    return PACKED


async def receive_number(update, context, field, next_state, question):
    value = (update.message.text or "").strip()
    if not value.isdecimal():
        await update.message.reply_text("Введите целое неотрицательное число.")
        return {"packed": PACKED, "defective": DEFECTIVE, "rework": REWORK}[field]
    context.user_data["lm_receiving"][field] = int(value)
    await update.message.reply_text(question, reply_markup=cancel_keyboard())
    return next_state


async def receive_packed(update, context):
    return await receive_number(update, context, "packed", DEFECTIVE, "Сколько единиц брака?")


async def receive_defective(update, context):
    return await receive_number(update, context, "defective", REWORK, "Сколько единиц на доработку?")


async def receive_rework(update, context):
    data = context.user_data["lm_receiving"]
    value = (update.message.text or "").strip()
    if not value.isdecimal():
        await update.message.reply_text("Введите целое неотрицательное число.")
        return REWORK
    data["rework"] = int(value)
    product = data["product"]
    text = (f"Проверьте приёмку:\n{TYPE_LABELS[data['report_type']]} · {data['record_date']}\n"
            f"{product['display_name']}\nПринято: {data['packed']} · Брак: {data['defective']} · "
            f"Доработка: {data['rework']}")
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Сохранить", callback_data="lmrecv:save")],
        [InlineKeyboardButton("❌ Отмена", callback_data="lmrecv:cancel")],
    ]))
    return CONFIRM


async def save(update, context):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("lm_receiving") or {}
    product = data.get("product") or {}
    if not product:
        await query.edit_message_text("Данные приёмки устарели. Начните заново.")
        return ConversationHandler.END
    try:
        record_id = save_moysklad_incoming_good(
            user_id=update.effective_user.id,
            username=update.effective_user.username or update.effective_user.full_name,
            report_type=data["report_type"],
            product_id=product["id"], product_name=product["base_name"], size=product["size"],
            packed=data["packed"], defective=data["defective"], rework=data["rework"],
            record_date=data["record_date"],
        )
    except Exception:
        logging.exception("Не удалось сохранить приёмку")
        await query.edit_message_text("Не удалось сохранить приёмку. Попробуйте позже.")
        return ConversationHandler.END
    for key in ("product", "record_date", "packed", "defective", "rework"):
        data.pop(key, None)
    await query.edit_message_text(
        f"✅ Приёмка сохранена, запись №{record_id}.\n\n"
        "Выберите следующий размер или введите новый запрос:",
        reply_markup=results_keyboard(data["results"]),
    )
    return SELECT_PRODUCT


async def new_search(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["lm_receiving"].pop("results", None)
    await query.edit_message_text(
        "Отсканируйте штрихкод/код ЧЗ или введите название другого товара.",
        reply_markup=cancel_keyboard(),
    )
    return SEARCH


async def finish(update, context):
    await update.callback_query.answer()
    context.user_data.pop("lm_receiving", None)
    await update.callback_query.edit_message_text("✅ Приёмка завершена.")
    return ConversationHandler.END


async def cancel(update, context):
    await update.callback_query.answer()
    context.user_data.pop("lm_receiving", None)
    await update.callback_query.edit_message_text("Приёмка отменена.")
    return ConversationHandler.END


def get_loaded_mind_receiving_handler():
    cancel_handler = CallbackQueryHandler(cancel, pattern=r"^lmrecv:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start, pattern=r"^recvtype:(new_supply|illiquid|rejected)$")],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search), cancel_handler],
            SELECT_PRODUCT: [
                CallbackQueryHandler(select_product, pattern=r"^lmrecv:product:\d+$"),
                CallbackQueryHandler(new_search, pattern=r"^lmrecv:new_search$"),
                CallbackQueryHandler(finish, pattern=r"^lmrecv:finish$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, search),
                cancel_handler,
            ],
            SELECT_DATE: [CallbackQueryHandler(select_date, pattern=r"^lmrecv:date:"), cancel_handler],
            PACKED: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_packed), cancel_handler],
            DEFECTIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_defective), cancel_handler],
            REWORK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_rework), cancel_handler],
            CONFIRM: [CallbackQueryHandler(save, pattern=r"^lmrecv:save$"), cancel_handler],
        },
        fallbacks=[cancel_handler],
    )
