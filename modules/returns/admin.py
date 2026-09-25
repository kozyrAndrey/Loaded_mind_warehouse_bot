import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager
from modules.moysklad.search import compact_product_name, search_products
from modules.returns.loaded_mind import CONDITIONS, split_text, summary
from modules.returns.storage import (
    get_recent_return_records, get_return_record, mark_return_record_deleted,
    update_return_record,
)


SELECT, FIELD, VALUE, DELETE_CONFIRM = range(2100, 2104)
FIELDS = {"counterparty": "ФИО контрагента", "track_number": "Трек-номер", "label_status": "Статус этикетки"}


def keyboard(rows):
    return InlineKeyboardMarkup(rows + [[InlineKeyboardButton("❌ Отмена", callback_data="lmretadmin:cancel")]])


def allowed(update):
    return is_manager(find_employee_for_telegram_user(update.effective_user))


async def deny(update):
    await update.callback_query.answer("Доступно только руководителям.", show_alert=True)
    return ConversationHandler.END


async def start(update, context):
    query = update.callback_query
    if not allowed(update):
        return await deny(update)
    await query.answer()
    mode = query.data.rsplit(":", 1)[-1]
    context.user_data["lm_return_admin"] = {"mode": mode}
    rows = [
        [InlineKeyboardButton(f"#{record['id']} · {record['counterparty'][:30]}",
                              callback_data=f"lmretadmin:select:{record['id']}")]
        for record in get_recent_return_records(limit=20)
    ]
    if not rows:
        await query.edit_message_text("Возвратов пока нет.")
        return ConversationHandler.END
    await query.edit_message_text("Выберите запись возврата:", reply_markup=keyboard(rows))
    return SELECT


async def select(update, context):
    query = update.callback_query
    await query.answer()
    record_id = int(query.data.rsplit(":", 1)[-1])
    record = get_return_record(record_id)
    if not record or record["status"] == "deleted":
        await query.edit_message_text("Запись не найдена.")
        return ConversationHandler.END
    state = context.user_data["lm_return_admin"]
    state["record_id"] = record_id
    if state["mode"] == "delete":
        preview = summary(record)
        if len(preview) > 3700:
            preview = preview[:3650] + "\n…"
        await query.edit_message_text(
            f"Удалить возврат #{record_id}?\n\n{preview}",
            reply_markup=keyboard([[InlineKeyboardButton("🗑 Удалить", callback_data="lmretadmin:delete_confirm")]]),
        )
        return DELETE_CONFIRM
    rows = [[InlineKeyboardButton(label, callback_data=f"lmretadmin:field:{key}")]
            for key, label in FIELDS.items()
            if key != "track_number" or record["return_type"] == "cdek"]
    rows.extend([
        [InlineKeyboardButton("✏️ Изменить товар", callback_data="lmretadmin:items")],
        [InlineKeyboardButton("✏️ Тип возврата", callback_data="lmretadmin:field:return_type")],
    ])
    preview = summary(record)
    if len(preview) > 3700:
        preview = preview[:3650] + "\n…"
    await query.edit_message_text(f"Возврат #{record_id}\n\n{preview}\n\nЧто изменить?", reply_markup=keyboard(rows))
    return FIELD


async def items(update, context):
    query = update.callback_query
    await query.answer()
    record = get_return_record(context.user_data["lm_return_admin"]["record_id"])
    rows = [[InlineKeyboardButton(f"{i + 1}. {compact_product_name(item['product_name'], item['size'])[:45]}",
                                  callback_data=f"lmretadmin:item:{i}")]
            for i, item in enumerate(record["items"])]
    await query.edit_message_text("Выберите товар:", reply_markup=keyboard(rows))
    return FIELD


async def item(update, context):
    query = update.callback_query
    await query.answer()
    state = context.user_data["lm_return_admin"]
    state["item_index"] = int(query.data.rsplit(":", 1)[-1])
    rows = [
        [InlineKeyboardButton("Название товара", callback_data="lmretadmin:field:item:product_name")],
        [InlineKeyboardButton("Размер", callback_data="lmretadmin:field:item:size")],
        [InlineKeyboardButton("Состояние", callback_data="lmretadmin:field:item:condition_key")],
        [InlineKeyboardButton("Комментарий", callback_data="lmretadmin:field:item:condition_comment")],
        [InlineKeyboardButton("Статус ЧЗ", callback_data="lmretadmin:field:item:chz_status")],
    ]
    await query.edit_message_text("Что изменить в товаре?", reply_markup=keyboard(rows))
    return FIELD


async def field(update, context):
    query = update.callback_query
    await query.answer()
    field_name = query.data.split("lmretadmin:field:", 1)[-1]
    context.user_data["lm_return_admin"]["field"] = field_name
    if field_name == "return_type":
        rows = [
            [InlineKeyboardButton("СДЭК", callback_data="lmretadmin:type:cdek")],
            [InlineKeyboardButton("Шоу-рум", callback_data="lmretadmin:type:showroom")],
        ]
        await query.edit_message_text("Выберите тип возврата:", reply_markup=keyboard(rows))
        return FIELD
    if field_name == "item:condition_key":
        rows = [[InlineKeyboardButton(label.capitalize(), callback_data=f"lmretadmin:condition:{key}")]
                for key, label in CONDITIONS.items()]
        await query.edit_message_text("Выберите состояние:", reply_markup=keyboard(rows))
        return FIELD
    await query.edit_message_text("Введите новое значение текстом:", reply_markup=keyboard([]))
    return VALUE


async def selected_value(update, context):
    query = update.callback_query
    await query.answer()
    state = context.user_data["lm_return_admin"]
    value = query.data.rsplit(":", 1)[-1]
    if state["field"] == "return_type":
        update_return_record(state["record_id"], return_type=value)
    else:
        record = get_return_record(state["record_id"])
        items = record["items"]
        index = state["item_index"]
        items[index]["condition_key"] = value
        items[index]["condition_label"] = CONDITIONS[value]
        update_return_record(state["record_id"], items=items)
    await synchronize_topic(context, state["record_id"])
    await query.edit_message_text("✅ Возврат обновлён.")
    context.user_data.pop("lm_return_admin", None)
    return ConversationHandler.END


async def text_value(update, context):
    state = context.user_data["lm_return_admin"]
    value = (update.message.text or "").strip()
    if not value:
        await update.message.reply_text("Значение не может быть пустым.")
        return VALUE
    field_name = state["field"]
    if field_name == "item:product_name":
        try:
            products = await asyncio.to_thread(search_products, value)
        except Exception:
            logging.exception("Не удалось найти товар для изменения возврата")
            await update.message.reply_text("Поиск в МойСклад не удался. Попробуйте ещё раз.")
            return VALUE
        if not products:
            await update.message.reply_text("Товар не найден. Попробуйте другой запрос.")
            return VALUE
        state["products"] = products
        rows = [[InlineKeyboardButton(product["display_name"][:60], callback_data=f"lmretadmin:product:{index}")]
                for index, product in enumerate(products)]
        await update.message.reply_text("Выберите товар:", reply_markup=keyboard(rows))
        return FIELD
    if field_name.startswith("item:"):
        record = get_return_record(state["record_id"])
        items = record["items"]
        items[state["item_index"]][field_name.split(":", 1)[1]] = value
        update_return_record(state["record_id"], items=items)
    else:
        update_return_record(state["record_id"], **{field_name: value})
    await synchronize_topic(context, state["record_id"])
    await update.message.reply_text("✅ Возврат обновлён.")
    context.user_data.pop("lm_return_admin", None)
    return ConversationHandler.END


async def selected_product(update, context):
    query = update.callback_query
    await query.answer()
    state = context.user_data["lm_return_admin"]
    try:
        product = state["products"][int(query.data.rsplit(":", 1)[-1])]
    except (KeyError, IndexError, ValueError):
        await query.edit_message_text("Список товаров устарел. Начните изменение заново.")
        return ConversationHandler.END
    record = get_return_record(state["record_id"])
    items = record["items"]
    item = items[state["item_index"]]
    item.update(product_id=product["id"], product_name=product["base_name"], size=product["size"] or "—")
    update_return_record(state["record_id"], items=items)
    await synchronize_topic(context, state["record_id"])
    await query.edit_message_text("✅ Товар в возврате обновлён.")
    context.user_data.pop("lm_return_admin", None)
    return ConversationHandler.END


async def synchronize_topic(context, record_id):
    record = get_return_record(record_id)
    if record["chat_id"] and record["message_ids"]:
        try:
            chat_id = int(record["chat_id"])
            old_text_count = max(1, len(record["message_ids"]) - len(record["photo_ids"]))
            old_text_ids = record["message_ids"][:old_text_count]
            photo_message_ids = record["message_ids"][old_text_count:]
            new_text_ids = []
            chunks = split_text(f"Сотрудник: {record['employee_name']}\n{summary(record)}")
            for index, chunk in enumerate(chunks):
                if index < len(old_text_ids):
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id, message_id=old_text_ids[index], text=chunk,
                        )
                    except Exception as error:
                        if "message is not modified" not in str(error).lower():
                            raise
                    new_text_ids.append(old_text_ids[index])
                else:
                    sent = await context.bot.send_message(
                        chat_id=chat_id, message_thread_id=int(record["thread_id"]), text=chunk,
                    )
                    new_text_ids.append(sent.message_id)
            for obsolete_id in old_text_ids[len(chunks):]:
                await context.bot.delete_message(chat_id=chat_id, message_id=obsolete_id)
            update_return_record(record_id, message_ids=new_text_ids + photo_message_ids)
        except Exception:
            logging.exception("Возврат обновлён в БД, но не в теме Telegram")


async def delete(update, context):
    query = update.callback_query
    await query.answer()
    record_id = context.user_data["lm_return_admin"]["record_id"]
    record = mark_return_record_deleted(record_id)
    if record["chat_id"]:
        for message_id in record["message_ids"]:
            try:
                await context.bot.delete_message(chat_id=int(record["chat_id"]), message_id=message_id)
            except Exception:
                logging.exception("Не удалось удалить сообщение возврата из темы")
    await query.edit_message_text(f"🗑 Возврат #{record_id} удалён.")
    context.user_data.pop("lm_return_admin", None)
    return ConversationHandler.END


async def cancel(update, context):
    await update.callback_query.answer()
    context.user_data.pop("lm_return_admin", None)
    await update.callback_query.edit_message_text("Действие отменено.")
    return ConversationHandler.END


def get_loaded_mind_returns_admin_handler():
    stop = CallbackQueryHandler(cancel, pattern=r"^lmretadmin:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start, pattern=r"^retadmin:(edit|delete)$")],
        states={
            SELECT: [CallbackQueryHandler(select, pattern=r"^lmretadmin:select:\d+$"), stop],
            FIELD: [
                CallbackQueryHandler(items, pattern=r"^lmretadmin:items$"),
                CallbackQueryHandler(item, pattern=r"^lmretadmin:item:\d+$"),
                CallbackQueryHandler(field, pattern=r"^lmretadmin:field:"),
                CallbackQueryHandler(selected_value, pattern=r"^lmretadmin:(type|condition):"), stop,
                CallbackQueryHandler(selected_product, pattern=r"^lmretadmin:product:\d+$"),
            ],
            VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_value), stop],
            DELETE_CONFIRM: [CallbackQueryHandler(delete, pattern=r"^lmretadmin:delete_confirm$"), stop],
        },
        fallbacks=[stop],
    )
