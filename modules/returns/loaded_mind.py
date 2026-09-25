"""Возвраты Loaded Mind с выбором товара из МойСклад."""

import asyncio
import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from config import GROUP_CHAT_ID, RETURNS_TOPIC_ID, SUPPORT_MANAGER_MENTION
from modules.marking.duplicate_chz import DuplicateChzError, extract_short_marking_code
from modules.moysklad.search import compact_product_name, search_products
from modules.payroll.google_sheets import find_employee_for_telegram_user, get_employees
from modules.employees.roles import has_role
from modules.returns.storage import create_return_record
from modules.returns.cdek_ocr import recognize_cdek_photo, recognize_showroom_photo


(PHOTO, COUNTERPARTY, TRACK, COUNT, SEARCH, PRODUCT, CONDITION,
 CHZ_PHOTO, CHZ_CODE, EXTRA_PHOTO, COMMENT, CONFIRM) = range(2000, 2012)
REVIEW = 2012
ORDER_NUMBER = 2013

CONDITIONS = {
    "normal": "норм",
    "invoice_defect": "брак по накладной",
    "invoice_rework": "доработка по накладной",
    "not_invoice_defect": "брак не по накладной",
}


def nav_keyboard(extra=None):
    rows = extra or []
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="lmret:cancel")])
    return InlineKeyboardMarkup(rows)


def data(context):
    return context.user_data["lm_return"]


def review_keyboard(value):
    rows = []
    is_cdek = value["return_type"] == "cdek"
    if value.get("counterparty") and (not is_cdek or value.get("track_number")):
        rows.append([InlineKeyboardButton("✅ Всё верно, продолжить", callback_data="lmret:review_confirm")])
    rows.append([InlineKeyboardButton("✏️ Исправить ФИО", callback_data="lmret:edit_counterparty")])
    if is_cdek:
        rows.append([InlineKeyboardButton("✏️ Исправить трек-номер", callback_data="lmret:edit_track")])
    else:
        rows.append([InlineKeyboardButton("✏️ Добавить/исправить номер заказа", callback_data="lmret:edit_order")])
    return nav_keyboard(rows)


def review_text(value):
    is_cdek = value["return_type"] == "cdek"
    title = "накладной СДЭК" if is_cdek else "этикетки шоурума"
    number = (f"Трек-номер: {value.get('track_number') or 'не распознан'}" if is_cdek
              else f"Номер заказа: {value.get('order_number') or 'не найден (необязательно)'}")
    return (f"Проверьте данные с {title}:\n\n"
            f"ФИО контрагента: {value.get('counterparty') or 'не распознано'}\n"
            f"{number}\n\nЕсли OCR ошибся, исправьте поле перед продолжением.")


async def start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    return_type = query.data.rsplit(":", 1)[-1]
    context.user_data["lm_return"] = {
        "return_type": return_type, "items": [], "photo_ids": [], "current": {},
    }
    prompt = "Пришлите фото накладной." if return_type == "cdek" else "Пришлите фото этикетки возврата."
    extra = [[InlineKeyboardButton("Этикетки нет", callback_data="lmret:label_missing")]] if return_type == "showroom" else []
    await query.edit_message_text(prompt, reply_markup=nav_keyboard(extra))
    return PHOTO


async def photo(update, context):
    if not update.message.photo:
        await update.message.reply_text("Нужно фото документа.")
        return PHOTO
    file_id = update.message.photo[-1].file_id
    data(context)["photo_ids"].append(file_id)
    is_cdek = data(context)["return_type"] == "cdek"
    status = await update.message.reply_text("🔎 Считываю данные с накладной…" if is_cdek else "🔎 Считываю данные с этикетки…")
    try:
        telegram_file = await context.bot.get_file(file_id)
        image_buffer = io.BytesIO()
        await telegram_file.download_to_memory(out=image_buffer)
        recognize = recognize_cdek_photo if is_cdek else recognize_showroom_photo
        recognized = await asyncio.to_thread(recognize, image_buffer.getvalue())
        data(context).update(recognized)
    except Exception:
        logging.exception("Не удалось распознать возвратную этикетку; доступен ручной ввод")
    await status.edit_text(review_text(data(context)), reply_markup=review_keyboard(data(context)))
    return REVIEW


async def edit_counterparty(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Введите правильное ФИО контрагента:", reply_markup=nav_keyboard()
    )
    return COUNTERPARTY


async def edit_track(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Введите правильный трек-номер СДЭК цифрами:", reply_markup=nav_keyboard()
    )
    return TRACK


async def edit_order(update, context):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Введите номер заказа с этикетки:", reply_markup=nav_keyboard()
    )
    return ORDER_NUMBER


async def review_confirm(update, context):
    query = update.callback_query
    await query.answer()
    value = data(context)
    if not value.get("counterparty") or (value["return_type"] == "cdek" and
                                         not str(value.get("track_number") or "").isdecimal()):
        await query.edit_message_text(review_text(value), reply_markup=review_keyboard(value))
        return REVIEW
    await query.edit_message_text("Сколько товаров в возврате?", reply_markup=nav_keyboard())
    return COUNT


async def label_missing(update, context):
    await update.callback_query.answer()
    data(context)["label_status"] = "Этикетки нет"
    await update.callback_query.edit_message_text("Введите ФИО контрагента:", reply_markup=nav_keyboard())
    return COUNTERPARTY


async def counterparty(update, context):
    value = (update.message.text or "").strip()
    if len(value) < 2:
        await update.message.reply_text("Введите ФИО контрагента.")
        return COUNTERPARTY
    data(context)["counterparty"] = value
    if data(context).get("photo_ids"):
        await update.message.reply_text(review_text(data(context)), reply_markup=review_keyboard(data(context)))
        return REVIEW
    await update.message.reply_text("Сколько товаров в возврате?", reply_markup=nav_keyboard())
    return COUNT


async def track(update, context):
    value = (update.message.text or "").strip()
    if not value.isdecimal():
        await update.message.reply_text("Трек-номер должен содержать только цифры.")
        return TRACK
    data(context)["track_number"] = value
    await update.message.reply_text(review_text(data(context)), reply_markup=review_keyboard(data(context)))
    return REVIEW


async def order_number(update, context):
    value = (update.message.text or "").strip()
    if not 3 <= len(value) <= 50:
        await update.message.reply_text("Введите номер заказа (3–50 символов).")
        return ORDER_NUMBER
    data(context)["order_number"] = value
    await update.message.reply_text(review_text(data(context)), reply_markup=review_keyboard(data(context)))
    return REVIEW


async def count(update, context):
    value = (update.message.text or "").strip()
    if not value.isdecimal() or not 1 <= int(value) <= 30:
        await update.message.reply_text("Введите количество от 1 до 30.")
        return COUNT
    data(context)["total"] = int(value)
    await update.message.reply_text("Товар 1: отсканируйте штрихкод/ЧЗ или введите название из МойСклад.", reply_markup=nav_keyboard())
    return SEARCH


async def search(update, context):
    try:
        products = await asyncio.to_thread(search_products, update.message.text)
    except Exception:
        logging.exception("Ошибка поиска товара возврата")
        await update.message.reply_text("Не удалось найти товар. Попробуйте ещё раз.")
        return SEARCH
    if not products:
        await update.message.reply_text("Товар не найден. Попробуйте другой запрос.")
        return SEARCH
    data(context)["results"] = products
    buttons = [[InlineKeyboardButton(p["display_name"][:60], callback_data=f"lmret:product:{i}")]
               for i, p in enumerate(products)]
    await update.message.reply_text("Выберите товар:", reply_markup=nav_keyboard(buttons))
    return PRODUCT


async def product(update, context):
    query = update.callback_query
    await query.answer()
    try:
        item = data(context)["results"][int(query.data.rsplit(":", 1)[-1])]
    except (KeyError, IndexError, ValueError):
        await query.edit_message_text("Поиск устарел. Начните возврат заново.")
        return ConversationHandler.END
    data(context)["current"] = {"product_id": item["id"], "product_name": item["base_name"],
                                 "size": item["size"] or "—"}
    buttons = [[InlineKeyboardButton(label.capitalize(), callback_data=f"lmret:condition:{key}")]
               for key, label in CONDITIONS.items()]
    await query.edit_message_text(f"{item['display_name']}\nВыберите состояние:", reply_markup=nav_keyboard(buttons))
    return CONDITION


async def condition(update, context):
    query = update.callback_query
    await query.answer()
    key = query.data.rsplit(":", 1)[-1]
    if key not in CONDITIONS:
        return CONDITION
    current = data(context)["current"]
    current["condition_key"] = key
    current["condition_label"] = CONDITIONS[key]
    if key == "normal":
        await query.edit_message_text(
            "Отправьте фото ЧЗ на товаре или нажмите «ЧЗ нет».",
            reply_markup=nav_keyboard([[InlineKeyboardButton("ЧЗ нет", callback_data="lmret:chz_missing")]]),
        )
        return CHZ_PHOTO
    await query.edit_message_text("Отправьте дополнительное фото проблемного товара.", reply_markup=nav_keyboard())
    return EXTRA_PHOTO


async def chz_photo(update, context):
    if not update.message.photo:
        await update.message.reply_text("Отправьте фото ЧЗ или нажмите «ЧЗ нет».")
        return CHZ_PHOTO
    current = data(context)["current"]
    current["chz_photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("Теперь отсканируйте DataMatrix ЧЗ.", reply_markup=nav_keyboard())
    return CHZ_CODE


async def chz_missing(update, context):
    await update.callback_query.answer()
    data(context)["current"]["chz_status"] = "ЧЗ нет"
    return await finish_item(update.callback_query.message, context)


async def chz_code(update, context):
    raw = (update.message.text or "").strip()
    try:
        short = extract_short_marking_code(raw)
    except DuplicateChzError as error:
        await update.message.reply_text(str(error))
        return CHZ_CODE
    current = data(context)["current"]
    current["chz_code_full"] = raw
    current["chz_code_short"] = short
    current["chz_status"] = short
    return await finish_item(update.message, context)


async def extra_photo(update, context):
    if not update.message.photo:
        await update.message.reply_text("Для этого состояния нужно фото товара.")
        return EXTRA_PHOTO
    data(context)["current"]["extra_photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("Добавьте комментарий к состоянию товара:", reply_markup=nav_keyboard())
    return COMMENT


async def comment(update, context):
    value = (update.message.text or "").strip()
    if not value:
        await update.message.reply_text("Комментарий обязателен.")
        return COMMENT
    data(context)["current"]["condition_comment"] = value
    return await finish_item(update.message, context)


def summary(value):
    lines = [
        f"Возврат: {'СДЭК' if value['return_type'] == 'cdek' else 'Шоу-рум'}",
        f"Контрагент: {value['counterparty']}",
    ]
    if value["return_type"] == "cdek":
        lines.append(f"Трек-номер: {value.get('track_number', '')}")
    else:
        lines.append(f"Этикетка: {value.get('label_status', 'фото приложено')}")
        if value.get("order_number"):
            lines.append(f"Номер заказа: {value['order_number']}")
    lines.extend([f"Товаров: {len(value['items'])}", ""])
    for index, item in enumerate(value["items"], start=1):
        name_with_size = compact_product_name(item["product_name"], item["size"])
        line = f"{index}. {name_with_size} · {item['condition_label']}"
        if item.get("chz_status"):
            line += f" · ЧЗ: {item['chz_status']}"
        if item.get("condition_comment"):
            line += f" · {item['condition_comment']}"
        lines.append(line)
    if any(item.get("condition_key") != "normal" for item in value["items"]):
        for employee in get_employees(include_inactive=False):
            username = str(employee.get("telegram_username") or "").lstrip("@")
            if username and has_role(employee, "warehouse_manager"):
                lines.append(f"@{username}")
    if SUPPORT_MANAGER_MENTION and any(item.get("condition_key") == "not_invoice_defect" for item in value["items"]):
        lines.append(SUPPORT_MANAGER_MENTION)
    return "\n".join(lines)


def split_text(text, limit=3900):
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


async def finish_item(message, context):
    value = data(context)
    value["items"].append(value.pop("current"))
    value["current"] = {}
    if len(value["items"]) < value["total"]:
        next_number = len(value["items"]) + 1
        await message.reply_text(
            f"Товар {next_number} из {value['total']}: отсканируйте штрихкод/ЧЗ или введите название.",
            reply_markup=nav_keyboard(),
        )
        return SEARCH
    preview = summary(value)
    if len(preview) > 3900:
        preview = preview[:3800] + "\n… Полный отчёт будет отправлен в тему."
    await message.reply_text(preview, reply_markup=nav_keyboard([
        [InlineKeyboardButton("✅ Сохранить и отправить", callback_data="lmret:save")]
    ]))
    return CONFIRM


async def save(update, context):
    query = update.callback_query
    await query.answer()
    value = data(context)
    employee = find_employee_for_telegram_user(update.effective_user)
    employee_name = employee["full_name"] if employee else update.effective_user.full_name
    text = f"Сотрудник: {employee_name}\n{summary(value)}"
    photo_ids = value["photo_ids"] + [
        item[key] for item in value["items"]
        for key in ("extra_photo_file_id", "chz_photo_file_id") if item.get(key)
    ]
    message_ids = []
    try:
        if GROUP_CHAT_ID and RETURNS_TOPIC_ID:
            kwargs = {"chat_id": int(GROUP_CHAT_ID), "message_thread_id": int(RETURNS_TOPIC_ID)}
            for chunk in split_text(text):
                sent = await context.bot.send_message(text=chunk, **kwargs)
                message_ids.append(sent.message_id)
            for photo_id in photo_ids:
                sent = await context.bot.send_photo(photo=photo_id, **kwargs)
                message_ids.append(sent.message_id)
        record_id = create_return_record({
            "return_type": value["return_type"], "employee_name": employee_name,
            "employee_user_id": update.effective_user.id,
            "counterparty": value["counterparty"], "track_number": value.get("track_number", ""),
            "order_number": value.get("order_number", ""),
            "label_status": value.get("label_status", ""), "items": value["items"],
            "photo_ids": photo_ids, "chat_id": GROUP_CHAT_ID,
            "thread_id": RETURNS_TOPIC_ID, "message_ids": message_ids,
        })
    except Exception:
        logging.exception("Не удалось сохранить или отправить возврат")
        for message_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=int(GROUP_CHAT_ID), message_id=message_id)
            except Exception:
                logging.exception("Не удалось убрать часть незавершённого возврата")
        await query.edit_message_text(
            "Не удалось сохранить возврат. Попробуйте ещё раз.",
            reply_markup=nav_keyboard([[InlineKeyboardButton("🔄 Повторить", callback_data="lmret:save")]]),
        )
        return CONFIRM
    context.user_data.pop("lm_return", None)
    suffix = " Отправлен в тему." if message_ids else " Тема не настроена; запись сохранена в базе."
    await query.edit_message_text(f"✅ Возврат №{record_id} сохранён.{suffix}")
    return ConversationHandler.END


async def cancel(update, context):
    await update.callback_query.answer()
    context.user_data.pop("lm_return", None)
    await update.callback_query.edit_message_text("Возврат отменён.")
    return ConversationHandler.END


def get_loaded_mind_returns_handler():
    stop = CallbackQueryHandler(cancel, pattern=r"^lmret:cancel$")
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start, pattern=r"^menu:return:(cdek|showroom)$")],
        states={
            PHOTO: [MessageHandler(filters.PHOTO, photo), CallbackQueryHandler(label_missing, pattern=r"^lmret:label_missing$"), stop],
            REVIEW: [
                CallbackQueryHandler(review_confirm, pattern=r"^lmret:review_confirm$"),
                CallbackQueryHandler(edit_counterparty, pattern=r"^lmret:edit_counterparty$"),
                CallbackQueryHandler(edit_track, pattern=r"^lmret:edit_track$"), stop,
                CallbackQueryHandler(edit_order, pattern=r"^lmret:edit_order$"),
            ],
            COUNTERPARTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, counterparty), stop],
            TRACK: [MessageHandler(filters.TEXT & ~filters.COMMAND, track), stop],
            ORDER_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_number), stop],
            COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, count), stop],
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search), stop],
            PRODUCT: [CallbackQueryHandler(product, pattern=r"^lmret:product:\d+$"), stop],
            CONDITION: [CallbackQueryHandler(condition, pattern=r"^lmret:condition:"), stop],
            CHZ_PHOTO: [MessageHandler(filters.PHOTO, chz_photo), CallbackQueryHandler(chz_missing, pattern=r"^lmret:chz_missing$"), stop],
            CHZ_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, chz_code), stop],
            EXTRA_PHOTO: [MessageHandler(filters.PHOTO, extra_photo), stop],
            COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, comment), stop],
            CONFIRM: [CallbackQueryHandler(save, pattern=r"^lmret:save$"), stop],
        },
        fallbacks=[stop],
    )
