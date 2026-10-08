import asyncio
import logging
from datetime import datetime
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from core.keyboards import build_shipping_menu_keyboard
from modules.marking.export import build_moysklad_client
from modules.moysklad.client import MoySkladError
from modules.payroll.google_sheets import find_employee_for_telegram_user
from modules.shipping.excel import create_shipping_xlsx
from modules.shipping.service import (
    ShippingServiceError,
    ShippingValidationError,
    build_order_units,
    parse_shipping_marking_code,
    search_customer_orders,
    set_order_shipping_flag,
)
from modules.shipping.storage import (
    confirm_export_retired,
    create_export_snapshot,
    fail_export_snapshot,
    latest_pending_export,
    save_shipping,
)


logger = logging.getLogger(__name__)

SHIPPING_ORDER_NUMBER, SHIPPING_ORDER_CHOICE, SHIPPING_SCAN, SHIPPING_REVIEW = range(5100, 5104)


def _cancel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data="shipping:cancel")]]
    )


def _scan_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Кода нет", callback_data="shipping:no_code")],
            [InlineKeyboardButton("❌ Отмена", callback_data="shipping:cancel")],
        ]
    )


def _review_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Завершить отгрузку", callback_data="shipping:finish")],
            [InlineKeyboardButton("❌ Отмена", callback_data="shipping:cancel")],
        ]
    )


def _employee_name(user):
    try:
        employee = find_employee_for_telegram_user(user) or {}
    except Exception:
        logger.exception("Не удалось получить сотрудника для отгрузки")
        employee = {}
    return str(employee.get("full_name") or user.full_name or user.id)


def _draft(context):
    return context.user_data["shipping_draft"]


async def shipping_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("shipping_draft", None)
    context.user_data["_active_module"] = "shipping"
    await query.edit_message_text(
        "Отсканируйте накладную CDEK или введите цифры из номера заказа.\n\n"
        "Например, для заказа mind-5098 введите 5098.",
        reply_markup=_cancel_keyboard(),
    )
    return SHIPPING_ORDER_NUMBER


async def order_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = str(update.message.text or "").strip()
    status = await update.message.reply_text("🔎 Ищу заказ в «МойСклад»…")
    try:
        result = await asyncio.to_thread(
            search_customer_orders,
            build_moysklad_client(),
            query_text,
        )
    except (ShippingServiceError, ShippingValidationError, MoySkladError) as error:
        logger.warning("Не удалось найти заказ для отгрузки: %s", error)
        await status.edit_text(
            f"⚠️ Не удалось выполнить поиск: {error}\n\nПопробуйте ещё раз.",
            reply_markup=_cancel_keyboard(),
        )
        return SHIPPING_ORDER_NUMBER
    except Exception:
        logger.exception("Непредвиденная ошибка поиска заказа для отгрузки")
        await status.edit_text(
            "⚠️ Не удалось связаться с «МойСклад». Попробуйте ещё раз позже.",
            reply_markup=_cancel_keyboard(),
        )
        return SHIPPING_ORDER_NUMBER

    orders = result["orders"]
    if not orders:
        is_cdek_track = result["query_kind"] == "cdek_track"
        lookup_label = (
            f"трек-номеру CDEK {result['query']}"
            if is_cdek_track
            else f"номеру {result['query']}"
        )
        suffix = (
            " Найденные заказы имеют статус «Отменён»."
            if result["cancelled_count"] else ""
        )
        await status.edit_text(
            f"Заказ по {lookup_label} не найден.{suffix}\n\n"
            "Проверьте номер и повторите ввод или сканирование.",
            reply_markup=_cancel_keyboard(),
        )
        return SHIPPING_ORDER_NUMBER

    context.user_data["shipping_draft"] = {
        "query": result["query"],
        "query_kind": result["query_kind"],
        "orders": orders,
    }
    if len(orders) == 1:
        await status.edit_text(f"Найден заказ {orders[0].get('name')}. Загружаю товары…")
        return await _prepare_selected_order(status, context, orders[0])

    keyboard = [
        [InlineKeyboardButton(str(order.get("name") or order.get("id")), callback_data=f"shipping:order:{index}")]
        for index, order in enumerate(orders)
    ]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="shipping:cancel")])
    await status.edit_text(
        "Найдено несколько заказов. Выберите нужный:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SHIPPING_ORDER_CHOICE


async def order_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.rsplit(":", 1)[-1])
        order = _draft(context)["orders"][index]
    except (KeyError, IndexError, TypeError, ValueError):
        await query.edit_message_text(
            "Список заказов устарел. Начните поиск заново.",
            reply_markup=build_shipping_menu_keyboard(),
        )
        return ConversationHandler.END
    await query.edit_message_text(f"Загружаю товары заказа {order.get('name')}…")
    return await _prepare_selected_order(query.message, context, order)


async def _prepare_selected_order(message, context, order):
    try:
        prepared = await asyncio.to_thread(build_order_units, build_moysklad_client(), order)
    except (ShippingServiceError, MoySkladError) as error:
        logger.warning("Не удалось загрузить позиции заказа: %s", error)
        await message.edit_text(
            f"⚠️ Не удалось подготовить заказ: {error}",
            reply_markup=build_shipping_menu_keyboard(),
        )
        context.user_data.pop("shipping_draft", None)
        return ConversationHandler.END
    except Exception:
        logger.exception("Непредвиденная ошибка подготовки заказа к отгрузке")
        await message.edit_text(
            "⚠️ Не удалось загрузить товары заказа. Попробуйте ещё раз позже.",
            reply_markup=build_shipping_menu_keyboard(),
        )
        context.user_data.pop("shipping_draft", None)
        return ConversationHandler.END

    context.user_data["shipping_draft"] = {
        "order": {"id": str(order.get("id") or ""), "name": str(order.get("name") or "")},
        "units": prepared["units"],
        "unit_index": 0,
        "codes": [],
        "unmarked_count": prepared["unmarked_count"],
        "no_code_count": 0,
    }
    if not prepared["units"]:
        return await _show_review(message, context)
    return await _ask_for_current_code(message, context)


async def _ask_for_current_code(message, context):
    draft = _draft(context)
    unit = draft["units"][draft["unit_index"]]
    await message.edit_text(
        f"Заказ: {draft['order']['name']}\n"
        f"Маркируемый товар {draft['unit_index'] + 1} из {len(draft['units'])}\n\n"
        f"{unit['product_name']}\n"
        f"Единица {unit['unit_number']} из {unit['position_quantity']}\n"
        f"Цена: {unit['price']} ₽\n"
        f"GTIN: {unit['gtin']}\n\n"
        "Отсканируйте полный DataMatrix Честного ЗНАКа.",
        reply_markup=_scan_keyboard(),
    )
    return SHIPPING_SCAN


async def marking_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        draft = _draft(context)
        unit = draft["units"][draft["unit_index"]]
    except (KeyError, IndexError):
        await update.message.reply_text(
            "Состояние отгрузки потеряно. Начните заново.",
            reply_markup=build_shipping_menu_keyboard(),
        )
        return ConversationHandler.END

    try:
        parsed = parse_shipping_marking_code(update.message.text, expected_gtin=unit["gtin"])
    except ShippingValidationError as error:
        await update.message.reply_text(
            f"⚠️ Код не принят: {error}\n\n"
            "Повторно отсканируйте DataMatrix или нажмите «Кода нет».",
            reply_markup=_scan_keyboard(),
        )
        return SHIPPING_SCAN

    if any(row["short_code"] == parsed["short_code"] for row in draft["codes"]):
        await update.message.reply_text(
            "⚠️ Этот код уже использован в текущей отгрузке. Отсканируйте другой код.",
            reply_markup=_scan_keyboard(),
        )
        return SHIPPING_SCAN

    draft["codes"].append({**unit, **parsed})
    draft["unit_index"] += 1
    status = await update.message.reply_text(f"Код принят ✅\n{parsed['short_code']}")
    return await _advance_or_review(status, context)


async def no_code_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = _draft(context)
    draft["no_code_count"] += 1
    draft["unit_index"] += 1
    return await _advance_or_review(query.message, context)


async def _advance_or_review(message, context):
    draft = _draft(context)
    if draft["unit_index"] >= len(draft["units"]):
        return await _show_review(message, context)
    return await _ask_for_current_code(message, context)


async def _show_review(message, context):
    draft = _draft(context)
    skipped = draft["unmarked_count"] + draft["no_code_count"]
    await message.edit_text(
        f"Проверьте отгрузку заказа {draft['order']['name']}:\n\n"
        f"Кодов будет сохранено: {len(draft['codes'])}\n"
        f"Товаров без GTIN: {draft['unmarked_count']}\n"
        f"Нажатий «Кода нет»: {draft['no_code_count']}\n"
        f"Всего пропущено: {skipped}",
        reply_markup=_review_keyboard(),
    )
    return SHIPPING_REVIEW


async def shipping_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    draft = _draft(context)
    user = update.effective_user
    try:
        saved = await asyncio.to_thread(
            save_shipping,
            draft["order"],
            draft["codes"],
            user.id,
            _employee_name(user),
        )
    except Exception:
        logger.exception("Не удалось сохранить отгрузку")
        await query.edit_message_text(
            "⚠️ Не удалось сохранить отгрузку. Данные не записаны; попробуйте ещё раз.",
            reply_markup=_review_keyboard(),
        )
        return SHIPPING_REVIEW

    order_name = draft["order"]["name"]
    attribute_warning = ""
    try:
        await asyncio.to_thread(
            set_order_shipping_flag,
            build_moysklad_client(),
            draft["order"]["id"],
        )
    except Exception as error:
        logger.exception("Не удалось установить признак отгрузки в заказе МойСклад")
        attribute_warning = (
            "\n\n⚠️ Отгрузка сохранена, но не удалось записать «уедет» "
            f"в заказ «МойСклад»: {error}"
        )
    context.user_data.pop("shipping_draft", None)
    await query.edit_message_text(
        f"Отгрузка {order_name} завершена ✅\n"
        f"Сохранено кодов: {saved}."
        + (attribute_warning or "\nПоле заказа обновлено: уедет."),
        reply_markup=build_shipping_menu_keyboard(),
    )
    return ConversationHandler.END


async def shipping_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("shipping_draft", None)
    await query.edit_message_text(
        "Отгрузка отменена. Данные не сохранены.",
        reply_markup=build_shipping_menu_keyboard(),
    )
    return ConversationHandler.END


async def export_shipping_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Формирую Excel с активными кодами…")
    user = update.effective_user
    snapshot = None
    document_sent = False
    try:
        snapshot = await asyncio.to_thread(
            create_export_snapshot,
            user.id,
            _employee_name(user),
        )
        if not snapshot:
            await query.edit_message_text(
                "Активных кодов для выгрузки нет.",
                reply_markup=build_shipping_menu_keyboard(),
            )
            return
        content = await asyncio.to_thread(create_shipping_xlsx, snapshot["rows"])
        filename = f"shipping_codes_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        await query.message.reply_document(
            document=InputFile(BytesIO(content), filename=filename),
            caption=f"Коды отгруженных товаров: {len(snapshot['rows'])}.",
        )
        document_sent = True
        await query.message.reply_text(
            "После фактического вывода этих кодов из оборота подтвердите удаление из базы.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(
                        "✅ Коды выведены из оборота",
                        callback_data=f"shipping:retire:{snapshot['batch_id']}",
                    )],
                    [InlineKeyboardButton("⬅️ В меню отгрузки", callback_data="section:shipping")],
                ]
            ),
        )
        await query.edit_message_text("Excel сформирован ✅")
    except Exception:
        logger.exception("Не удалось сформировать выгрузку отгрузок")
        if snapshot and not document_sent:
            try:
                await asyncio.to_thread(fail_export_snapshot, snapshot["batch_id"])
            except Exception:
                logger.exception("Не удалось пометить неотправленную выгрузку ошибочной")
        error_text = (
            "Excel отправлен, но не удалось показать кнопку подтверждения. "
            "После вывода кодов из оборота используйте соответствующую кнопку в меню."
            if document_sent else
            "⚠️ Не удалось сформировать Excel. Попробуйте ещё раз позже."
        )
        await query.edit_message_text(error_text, reply_markup=build_shipping_menu_keyboard())


async def retire_export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        suffix = query.data.removeprefix("shipping:retire:")
        if suffix and suffix != query.data:
            batch = {"batch_id": int(suffix), "count": None}
        else:
            batch = await asyncio.to_thread(latest_pending_export)
    except (TypeError, ValueError):
        batch = None
    except Exception:
        logger.exception("Не удалось найти последнюю выгрузку отгрузок")
        batch = None
    if not batch:
        await query.edit_message_text(
            "Нет подготовленной выгрузки для подтверждения.",
            reply_markup=build_shipping_menu_keyboard(),
        )
        return

    count_text = f" ({batch['count']} кодов)" if batch.get("count") is not None else ""
    await query.edit_message_text(
        f"Удалить из базы коды, вошедшие в выгрузку №{batch['batch_id']}{count_text}?\n\n"
        "Коды, повторно отсканированные после этой выгрузки, будут сохранены.",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(
                    "✅ Да, удалить",
                    callback_data=f"shipping:retire:confirm:{batch['batch_id']}",
                )],
                [InlineKeyboardButton("❌ Отмена", callback_data="shipping:retire:cancel")],
            ]
        ),
    )


async def retire_export_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    batch_id = int(query.data.rsplit(":", 1)[-1])
    user = update.effective_user
    try:
        result = await asyncio.to_thread(
            confirm_export_retired,
            batch_id,
            user.id,
            _employee_name(user),
        )
    except (ValueError, TypeError) as error:
        await query.edit_message_text(
            f"⚠️ {error}",
            reply_markup=build_shipping_menu_keyboard(),
        )
        return
    except Exception:
        logger.exception("Не удалось подтвердить вывод кодов из оборота")
        await query.edit_message_text(
            "⚠️ Не удалось удалить коды. Попробуйте ещё раз позже.",
            reply_markup=build_shipping_menu_keyboard(),
        )
        return

    if result["already_confirmed"]:
        text = "Эта выгрузка уже была подтверждена ранее. Дополнительные записи не удалены."
    else:
        text = f"Готово ✅\nУдалено кодов: {result['deleted']}."
        if result["preserved"]:
            text += f"\nСохранено обновлённых после выгрузки кодов: {result['preserved']}."
    await query.edit_message_text(text, reply_markup=build_shipping_menu_keyboard())


async def retire_export_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Удаление отменено.",
        reply_markup=build_shipping_menu_keyboard(),
    )


def get_shipping_handlers():
    conversation = ConversationHandler(
        entry_points=[CallbackQueryHandler(shipping_start, pattern=r"^shipping:new$")],
        states={
            SHIPPING_ORDER_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, order_number_received),
                CallbackQueryHandler(shipping_cancel, pattern=r"^shipping:cancel$"),
            ],
            SHIPPING_ORDER_CHOICE: [
                CallbackQueryHandler(order_selected, pattern=r"^shipping:order:\d+$"),
                CallbackQueryHandler(shipping_cancel, pattern=r"^shipping:cancel$"),
            ],
            SHIPPING_SCAN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, marking_code_received),
                CallbackQueryHandler(no_code_selected, pattern=r"^shipping:no_code$"),
                CallbackQueryHandler(shipping_cancel, pattern=r"^shipping:cancel$"),
            ],
            SHIPPING_REVIEW: [
                CallbackQueryHandler(shipping_finish, pattern=r"^shipping:finish$"),
                CallbackQueryHandler(shipping_cancel, pattern=r"^shipping:cancel$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(shipping_cancel, pattern=r"^shipping:cancel$")],
        name="shipping_workflow",
        persistent=False,
    )
    return [
        conversation,
        CallbackQueryHandler(export_shipping_codes, pattern=r"^shipping:export$"),
        CallbackQueryHandler(retire_export_confirm, pattern=r"^shipping:retire:confirm:\d+$"),
        CallbackQueryHandler(retire_export_cancel, pattern=r"^shipping:retire:cancel$"),
        CallbackQueryHandler(retire_export_start, pattern=r"^shipping:retire(?::\d+)?$"),
    ]
