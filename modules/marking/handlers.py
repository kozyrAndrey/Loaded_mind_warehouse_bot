import asyncio
import logging
import os
import tempfile
from datetime import datetime

from telegram import Update
from core.reply_callback import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from core.keyboards import build_marking_menu_keyboard
from modules.marking.catalog_import import CatalogImportError, parse_catalog_file
from modules.marking.duplicate_chz import (
    DuplicateChzError,
    create_duplicate_chz_75x120_pdf,
    create_duplicate_chz_pdf,
)
from modules.marking.export import (
    TrendExportValidationError,
    build_moysklad_client,
    create_honest_sign_catalog_csv,
    create_stock_marking_codes_xlsx,
    create_trend_island_upd_csv,
    get_retireorder_export_rows,
    get_unmarked_moysklad_rows,
)
from modules.marking.moysklad_lookup import find_marking_product_info
from modules.marking.one_c_export import OneCExportValidationError, create_one_c_xlsx
from modules.marking.storage import (
    get_honest_sign_names,
    get_unmarked_product,
    list_honest_sign_products,
    list_unmarked_products,
    replace_honest_sign_products,
)
from modules.moysklad.client import MoySkladError
from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager


(
    MARKING_DISCOUNTS,
    MARKING_DOCUMENT_NAME,
    MARKING_DUPLICATE_CHZ_CODE,
    MARKING_CATALOG_MENU,
    MARKING_UNMARKED_CONFIRM,
    MARKING_UNMARKED_PRODUCT,
    MARKING_UNMARKED_QUANTITY,
    MARKING_STOCK_CODES_DOCUMENT_NAME,
    MARKING_CATALOG_UPLOAD,
    MARKING_CATALOG_IMPORT_CONFIRM,
) = range(1400, 1410)


TREND_PRICE_TYPE_WITH_DISCOUNTS = "Старая цена"
TREND_PRICE_TYPE_WITHOUT_DISCOUNTS = "Цена продажи"


def marking_cancel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="marking:cancel")],
    ])


def trend_back_keyboard(target):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Назад", callback_data=f"marking:trend:back:{target}")]]
    )


def trend_discounts_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Да",
                    callback_data="marking:trend_export:discounts:yes",
                ),
                InlineKeyboardButton(
                    "Нет",
                    callback_data="marking:trend_export:discounts:no",
                ),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="marking:trend:back:menu")],
        ]
    )


def trend_unmarked_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Да",
                    callback_data="marking:trend:unmarked:yes",
                ),
                InlineKeyboardButton(
                    "Нет",
                    callback_data="marking:trend:unmarked:no",
                ),
            ],
            [InlineKeyboardButton("⬅️ Назад", callback_data="marking:trend:back:document")],
        ]
    )


def trend_unmarked_products_keyboard(products, selected_quantities):
    selected_quantities = selected_quantities or {}
    rows = []
    for product in products:
        product_id = str(product["id"])
        quantity = selected_quantities.get(product_id)
        mark = f"✅ {quantity} шт. — " if quantity else ""
        label = f"{mark}{product['honest_sign_name']} · {product['size']}"
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"marking:trend:unmarked:product:{product_id}",
                )
            ]
        )
    if selected_quantities:
        rows.append(
            [
                InlineKeyboardButton(
                    "✅ Завершить выбор",
                    callback_data="marking:trend:unmarked:done",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("⬅️ Назад", callback_data="marking:trend:back:unmarked")]
    )
    return InlineKeyboardMarkup(rows)


def catalog_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Загрузить таблицу", callback_data="marking:catalog:upload")],
            [InlineKeyboardButton("📤 Скачать справочник CSV", callback_data="marking:catalog:export")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="marking:catalog:back")],
        ]
    )


def catalog_import_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Заменить справочник",
                    callback_data="marking:catalog:import:confirm",
                )
            ],
            [InlineKeyboardButton("❌ Отмена", callback_data="marking:catalog:import:cancel")],
        ]
    )


def ensure_manager(update: Update):
    employee = find_employee_for_telegram_user(update.effective_user)
    return is_manager(employee)


def marking_menu_keyboard(update: Update):
    return build_marking_menu_keyboard(manager=ensure_manager(update))


async def trend_export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    clear_trend_export_context(context)
    await query.edit_message_text(
        "Есть ли сейчас скидки?",
        reply_markup=trend_discounts_keyboard(),
    )
    return MARKING_DISCOUNTS


async def stock_codes_export_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Введите точное название документа «Вывод из оборота» для выгрузки кодов маркировки:",
        reply_markup=marking_cancel_keyboard(),
    )
    return MARKING_STOCK_CODES_DOCUMENT_NAME


async def stock_codes_export_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document_name = (update.message.text or "").strip()
    if not document_name:
        await update.message.reply_text(
            "Введите непустое название документа.",
            reply_markup=marking_cancel_keyboard(),
        )
        return MARKING_STOCK_CODES_DOCUMENT_NAME

    status_message = await update.message.reply_text("Получаю коды маркировки из МойСклад...")
    output_path = None
    try:
        client = build_moysklad_client()
        document, rows = get_retireorder_export_rows(client, document_name)
        codes_count = sum(len(item.get("codes") or []) for item in rows)
        if not codes_count:
            await status_message.edit_text(
                "В документе не найдены коды маркировки.",
                reply_markup=marking_menu_keyboard(update),
            )
            return ConversationHandler.END

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            output_path = tmp.name
        create_stock_marking_codes_xlsx(rows, output_path)

        with open(output_path, "rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=f"stock_marking_codes_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                caption=(
                    "Коды маркировки для стока\n"
                    f"Документ: {document.get('name')}\n"
                    f"Кодов: {codes_count}"
                ),
            )
        await status_message.edit_text(
            "Excel с кодами маркировки сформирован ✅",
            reply_markup=marking_menu_keyboard(update),
        )
    except MoySkladError as error:
        await status_message.edit_text(
            f"Не удалось сформировать выгрузку:\n{error}",
            reply_markup=marking_menu_keyboard(update),
        )
    except Exception as error:
        logging.exception("Ошибка формирования выгрузки кодов для стока")
        await status_message.edit_text(
            f"Не удалось сформировать выгрузку:\n{error}",
            reply_markup=marking_menu_keyboard(update),
        )
    finally:
        remove_temporary_file(output_path, "Excel-выгрузки кодов для стока")
    return ConversationHandler.END


async def trend_export_discounts_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    has_discounts = query.data.endswith(":yes")
    price_type = (
        TREND_PRICE_TYPE_WITH_DISCOUNTS
        if has_discounts
        else TREND_PRICE_TYPE_WITHOUT_DISCOUNTS
    )
    context.user_data["marking_trend_price_type"] = price_type
    await query.edit_message_text(
        "Введите название документа «Вывод из оборота», откуда сформировать CSV для УПД и Excel для 1С:\n\n"
        f"Для выгрузки будет использована цена «{price_type}».",
        reply_markup=trend_back_keyboard("discounts"),
    )
    return MARKING_DOCUMENT_NAME


async def trend_export_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document_name = (update.message.text or "").strip()
    if not document_name:
        await update.message.reply_text(
            "Введите непустое название документа.",
            reply_markup=trend_back_keyboard("discounts"),
        )
        return MARKING_DOCUMENT_NAME

    context.user_data["marking_trend_document_name"] = document_name
    context.user_data["marking_trend_unmarked_quantities"] = {}
    await update.message.reply_text(
        "Есть ли немаркируемая продукция в этой поставке?",
        reply_markup=trend_unmarked_confirm_keyboard(),
    )
    return MARKING_UNMARKED_CONFIRM


async def trend_unmarked_confirm_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.endswith(":no"):
        context.user_data["marking_trend_unmarked_quantities"] = {}
        return await generate_trend_export(update, context)

    products = list_unmarked_products()
    if not products:
        await query.edit_message_text(
            "Справочник немаркируемой продукции пуст.",
            reply_markup=trend_unmarked_confirm_keyboard(),
        )
        return MARKING_UNMARKED_CONFIRM
    await query.edit_message_text(
        "Выберите немаркируемый товар:",
        reply_markup=trend_unmarked_products_keyboard(
            products,
            context.user_data.get("marking_trend_unmarked_quantities"),
        ),
    )
    return MARKING_UNMARKED_PRODUCT


async def trend_unmarked_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.rsplit(":", 1)[-1]
    product = get_unmarked_product(product_id)
    if not product:
        await query.edit_message_text(
            "Товар не найден в справочнике. Выберите другой товар.",
            reply_markup=trend_unmarked_products_keyboard(
                list_unmarked_products(),
                context.user_data.get("marking_trend_unmarked_quantities"),
            ),
        )
        return MARKING_UNMARKED_PRODUCT

    context.user_data["marking_trend_pending_unmarked_id"] = product_id
    await query.edit_message_text(
        f"{product['honest_sign_name']}\n"
        f"Размер: {product['size']}\n\n"
        "Введите количество целым числом:",
        reply_markup=trend_back_keyboard("products"),
    )
    return MARKING_UNMARKED_QUANTITY


async def trend_unmarked_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = int((update.message.text or "").strip())
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Количество должно быть положительным целым числом.",
            reply_markup=trend_back_keyboard("products"),
        )
        return MARKING_UNMARKED_QUANTITY

    product_id = context.user_data.pop("marking_trend_pending_unmarked_id", None)
    product = get_unmarked_product(product_id) if product_id else None
    if not product:
        await update.message.reply_text(
            "Товар больше не найден в справочнике.",
            reply_markup=trend_unmarked_products_keyboard(
                list_unmarked_products(),
                context.user_data.get("marking_trend_unmarked_quantities"),
            ),
        )
        return MARKING_UNMARKED_PRODUCT

    selected = context.user_data.setdefault("marking_trend_unmarked_quantities", {})
    selected[str(product["id"])] = quantity
    await update.message.reply_text(
        f"Добавлено: {product['honest_sign_name']} — {quantity} шт.\n\n"
        "Можно выбрать ещё один товар или завершить выбор.",
        reply_markup=trend_unmarked_products_keyboard(
            list_unmarked_products(),
            selected,
        ),
    )
    return MARKING_UNMARKED_PRODUCT


async def trend_unmarked_selection_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("marking_trend_unmarked_quantities"):
        await query.answer("Сначала выберите хотя бы один товар.", show_alert=True)
        return MARKING_UNMARKED_PRODUCT
    return await generate_trend_export(update, context)


async def trend_export_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.rsplit(":", 1)[-1]

    if target == "menu":
        clear_trend_export_context(context)
        await query.edit_message_text(
            "🏷 Маркировка:",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END
    if target == "discounts":
        await query.edit_message_text(
            "Есть ли сейчас скидки?",
            reply_markup=trend_discounts_keyboard(),
        )
        return MARKING_DISCOUNTS
    if target == "document":
        price_type = context.user_data.get(
            "marking_trend_price_type",
            TREND_PRICE_TYPE_WITHOUT_DISCOUNTS,
        )
        await query.edit_message_text(
            "Введите название документа «Вывод из оборота», откуда сформировать "
            "CSV для УПД и Excel для 1С:\n\n"
            f"Для выгрузки будет использована цена «{price_type}».",
            reply_markup=trend_back_keyboard("discounts"),
        )
        return MARKING_DOCUMENT_NAME
    if target == "unmarked":
        await query.edit_message_text(
            "Есть ли немаркируемая продукция в этой поставке?",
            reply_markup=trend_unmarked_confirm_keyboard(),
        )
        return MARKING_UNMARKED_CONFIRM

    context.user_data.pop("marking_trend_pending_unmarked_id", None)
    await query.edit_message_text(
        "Выберите немаркируемый товар:",
        reply_markup=trend_unmarked_products_keyboard(
            list_unmarked_products(),
            context.user_data.get("marking_trend_unmarked_quantities"),
        ),
    )
    return MARKING_UNMARKED_PRODUCT


def selected_unmarked_products(context):
    products = []
    for product_id, quantity in context.user_data.get(
        "marking_trend_unmarked_quantities",
        {},
    ).items():
        product = get_unmarked_product(product_id)
        if not product:
            continue
        product["quantity"] = quantity
        products.append(product)
    return products


async def generate_trend_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document_name = context.user_data.get("marking_trend_document_name", "")
    price_type = context.user_data.get(
        "marking_trend_price_type",
        TREND_PRICE_TYPE_WITHOUT_DISCOUNTS,
    )
    unmarked_products = selected_unmarked_products(context)

    status_text = (
        f"Проверяю данные и готовлю CSV для УПД и Excel для 1С "
        f"по цене «{price_type}»..."
    )
    if update.callback_query:
        status_message = await update.callback_query.edit_message_text(status_text)
    else:
        status_message = await update.message.reply_text(status_text)

    try:
        client = build_moysklad_client()
        document, rows = get_retireorder_export_rows(
            client,
            document_name,
            sale_price_type=price_type,
        )
        if not rows:
            await status_message.edit_text(
                "В документе не найдено позиций для выгрузки.",
                reply_markup=marking_menu_keyboard(update),
            )
            return ConversationHandler.END
        catalog_names = get_honest_sign_names(item.get("gtin") for item in rows)
        excel_rows = rows + get_unmarked_moysklad_rows(
            client,
            unmarked_products,
            sale_price_type=price_type,
            existing_rows=rows,
        )
    except MoySkladError as error:
        await status_message.edit_text(
            f"Не удалось сформировать выгрузку:\n{error}",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END
    except Exception as error:
        logging.exception("Ошибка получения данных маркировки из МойСклад")
        await status_message.edit_text(
            f"Не удалось сформировать выгрузку:\n{error}",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END

    csv_path = None
    one_c_path = None
    one_c_items = []
    validation_errors = []

    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            csv_path = tmp.name
        create_trend_island_upd_csv(rows, catalog_names, csv_path)
    except TrendExportValidationError as error:
        remove_temporary_file(csv_path, "некорректной CSV-выгрузки маркировки")
        csv_path = None
        validation_errors.extend(f"CSV для УПД: {message}" for message in error.errors)
    except Exception as error:
        remove_temporary_file(csv_path, "неуспешной CSV-выгрузки маркировки")
        csv_path = None
        logging.exception("Ошибка формирования CSV для УПД")
        validation_errors.append(f"CSV для УПД: техническая ошибка: {error}")

    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            one_c_path = tmp.name
        _, one_c_items = create_one_c_xlsx(
            excel_rows,
            catalog_names,
            one_c_path,
            price_type=price_type,
            unmarked_products=unmarked_products,
        )
    except OneCExportValidationError as error:
        remove_temporary_file(one_c_path, "некорректной Excel-выгрузки 1С")
        one_c_path = None
        validation_errors.extend(f"Excel для 1С: {message}" for message in error.errors)
    except Exception as error:
        remove_temporary_file(one_c_path, "неуспешной Excel-выгрузки 1С")
        one_c_path = None
        logging.exception("Ошибка формирования Excel для 1С")
        validation_errors.append(f"Excel для 1С: техническая ошибка: {error}")

    total_codes = sum(len(item["codes"]) for item in rows)
    date_suffix = datetime.now().strftime("%Y-%m-%d")

    try:
        if csv_path:
            with open(csv_path, "rb") as file:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=file,
                    filename=f"trend_island_upd_{date_suffix}.csv",
                    caption=(
                        "CSV для загрузки в УПД\n"
                        f"Документ: {document.get('name')}\n"
                        f"Позиций: {len(rows)}\n"
                        f"Кодов: {total_codes}"
                    ),
                )
        if one_c_path:
            with open(one_c_path, "rb") as file:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=file,
                    filename=f"trend_island_1c_{date_suffix}.xlsx",
                    caption=(
                        "Excel для импорта номенклатуры в 1С\n"
                        f"Документ: {document.get('name')}\n"
                        f"Модификаций: {len(one_c_items)}"
                    ),
                )
    finally:
        remove_temporary_file(csv_path, "CSV-выгрузки маркировки")
        remove_temporary_file(one_c_path, "Excel-выгрузки 1С")

    if validation_errors:
        generated_files = []
        if csv_path:
            generated_files.append("CSV для УПД")
        if one_c_path:
            generated_files.append("Excel для 1С")
        await send_validation_errors(
            update,
            context,
            status_message,
            validation_errors,
            generated_files=generated_files,
        )
        return ConversationHandler.END

    await status_message.edit_text(
        "CSV для УПД и Excel для 1С сформированы ✅",
        reply_markup=marking_menu_keyboard(update),
    )
    return ConversationHandler.END


async def send_validation_errors(update, context, status_message, errors, generated_files=None):
    generated_files = generated_files or []
    if generated_files:
        heading = f"Сформировано: {', '.join(generated_files)}. Другая выгрузка не создана:"
        short_status = f"Часть файлов сформирована ({', '.join(generated_files)})"
    else:
        heading = "Файлы не сформированы: исправьте ошибки в исходных данных:"
        short_status = "Файлы не сформированы"

    lines = [heading]
    lines.extend(f"{index}. {error}" for index, error in enumerate(errors, start=1))
    text = "\n".join(lines)

    if len(text) <= 3900:
        await status_message.edit_text(text, reply_markup=marking_menu_keyboard(update))
        return

    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(text)
            path = tmp.name
        with open(path, "rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=f"trend_island_errors_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.txt",
                caption=f"{short_status}. Найдено ошибок: {len(errors)}.",
            )
    finally:
        remove_temporary_file(path, "отчета об ошибках маркировки")

    await status_message.edit_text(
        f"{short_status}. Найдено ошибок: {len(errors)}. Список отправлен отдельным файлом.",
        reply_markup=marking_menu_keyboard(update),
    )


async def catalog_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await query.edit_message_text(
            "⛔️ Управление справочником доступно только руководителям.",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END

    products = list_honest_sign_products()
    marked_count = sum(1 for product in products if product.get("gtin"))
    unmarked_count = len(products) - marked_count
    await query.edit_message_text(
        "Справочник Честного ЗНАКа\n\n"
        f"Маркируемых товаров: {marked_count}\n"
        f"Немаркируемых товаров: {unmarked_count}\n"
        "Загрузите Excel или CSV с колонками GTIN и названием. "
        "После проверки и подтверждения файл полностью заменит текущий справочник.",
        reply_markup=catalog_menu_keyboard(),
    )
    return MARKING_CATALOG_MENU


async def catalog_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_catalog_import_context(context)
    await query.edit_message_text(
        "Отправьте таблицу .xlsx или .csv размером до 10 МБ.\n\n"
        "Бот сам найдёт строку заголовков и колонки GTIN/штрихкода и названия товара, "
        "приведёт GTIN к 14 цифрам и проверит дубли. До подтверждения база не изменится.",
        reply_markup=marking_cancel_keyboard(),
    )
    return MARKING_CATALOG_UPLOAD


async def catalog_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_manager(update):
        await update.message.reply_text("⛔️ Управление справочником доступно только руководителям.")
        return ConversationHandler.END

    document = update.message.document
    filename = str(document.file_name or "catalog.xlsx")
    if not filename.lower().endswith((".xlsx", ".csv")):
        await update.message.reply_text(
            "Поддерживаются только файлы .xlsx и .csv.",
            reply_markup=marking_cancel_keyboard(),
        )
        return MARKING_CATALOG_UPLOAD
    if int(document.file_size or 0) > 10 * 1024 * 1024:
        await update.message.reply_text(
            "Файл больше 10 МБ. Уменьшите таблицу и отправьте снова.",
            reply_markup=marking_cancel_keyboard(),
        )
        return MARKING_CATALOG_UPLOAD

    status_message = await update.message.reply_text("Проверяю структуру и данные таблицы...")
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        content = bytes(await telegram_file.download_as_bytearray())
        result = await asyncio.to_thread(parse_catalog_file, content, filename)
    except CatalogImportError as error:
        await status_message.edit_text("Проверка завершена с ошибкой.")
        await update.message.reply_text(
            f"Таблица не принята:\n{error}",
            reply_markup=marking_cancel_keyboard(),
        )
        return MARKING_CATALOG_UPLOAD
    except Exception as error:
        logging.exception("Ошибка загрузки справочника Честного ЗНАКа")
        await status_message.edit_text("Проверка завершена с ошибкой.")
        await update.message.reply_text(
            f"Не удалось обработать таблицу: {error}",
            reply_markup=marking_cancel_keyboard(),
        )
        return MARKING_CATALOG_UPLOAD

    context.user_data["marking_catalog_import_rows"] = result.rows
    context.user_data["marking_catalog_import_filename"] = filename
    preview = "\n".join(
        f"{index}. {row['gtin']} — {row['honest_sign_name']}"
        for index, row in enumerate(result.rows[:10], start=1)
    )
    more = "" if len(result.rows) <= 10 else f"\n…и ещё {len(result.rows) - 10} позиций"
    await status_message.edit_text("Проверка завершена ✅")
    await update.message.reply_text(
        "Таблица распознана ✅\n\n"
        f"Файл: {filename}\n"
        f"Лист: {result.source_name}\n"
        f"Строка заголовков: {result.header_row}\n"
        f"GTIN: колонка {result.gtin_column}\n"
        f"Название: колонка {result.name_column}\n"
        f"Товаров: {len(result.rows)}\n\n"
        f"Предпросмотр:\n{preview}{more}\n\n"
        "После подтверждения текущий справочник будет полностью заменён.",
        reply_markup=catalog_import_confirm_keyboard(),
    )
    return MARKING_CATALOG_IMPORT_CONFIRM


async def catalog_import_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not ensure_manager(update):
        await query.edit_message_text("⛔️ Управление справочником доступно только руководителям.")
        return ConversationHandler.END
    rows = context.user_data.get("marking_catalog_import_rows")
    if not rows:
        await query.edit_message_text(
            "Черновик загрузки не найден. Отправьте таблицу ещё раз.",
            reply_markup=catalog_menu_keyboard(),
        )
        return MARKING_CATALOG_MENU
    try:
        imported = replace_honest_sign_products(rows)
    except (RuntimeError, ValueError) as error:
        await query.edit_message_text(
            f"Не удалось заменить справочник: {error}",
            reply_markup=catalog_menu_keyboard(),
        )
        return MARKING_CATALOG_MENU
    filename = context.user_data.get("marking_catalog_import_filename", "таблица")
    clear_catalog_import_context(context)
    await query.edit_message_text(
        f"Справочник заменён ✅\nФайл: {filename}\nЗаписей в БД: {imported}.",
        reply_markup=catalog_menu_keyboard(),
    )
    return MARKING_CATALOG_MENU


async def catalog_import_cancelled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_catalog_import_context(context)
    await query.edit_message_text(
        "Загрузка отменена. Справочник не изменён.",
        reply_markup=catalog_menu_keyboard(),
    )
    return MARKING_CATALOG_MENU


async def catalog_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    products = list_honest_sign_products()
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            path = tmp.name
        create_honest_sign_catalog_csv(products, path)
        with open(path, "rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=f"honest_sign_products_{datetime.now().strftime('%Y-%m-%d')}.csv",
                caption=f"Справочник Честного ЗНАКа. Записей: {len(products)}.",
            )
    finally:
        remove_temporary_file(path, "CSV-справочника маркировки")
    await query.edit_message_text(
        f"Справочник отправлен ✅\nЗаписей: {len(products)}.",
        reply_markup=catalog_menu_keyboard(),
    )
    return MARKING_CATALOG_MENU


async def catalog_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_catalog_context(context)
    await query.edit_message_text("🏷 Маркировка:", reply_markup=marking_menu_keyboard(update))
    return ConversationHandler.END


async def duplicate_chz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    is_large_label = query.data == "marking:duplicate_chz_75x120"
    context.user_data["marking_duplicate_chz_size"] = "75x120" if is_large_label else "58x40"

    await query.edit_message_text(
        f"Дубликат ЧЗ {'75×120' if is_large_label else '58×40'} мм.\n\n"
        "Пришлите полный код ЧЗ текстом.\n\n"
        "Если в коде есть разделитель GS, можно вставить его как <GS> или \\x1d.",
        reply_markup=marking_cancel_keyboard(),
    )
    return MARKING_DUPLICATE_CHZ_CODE


async def duplicate_chz_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_code = (update.message.text or "").strip()
    if not raw_code:
        await update.message.reply_text("Пришлите непустой полный код ЧЗ.", reply_markup=marking_cancel_keyboard())
        return MARKING_DUPLICATE_CHZ_CODE

    status_message = await update.message.reply_text("Готовлю дубликат ЧЗ...")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        path = tmp.name

    try:
        product_info = find_marking_product_info(raw_code)
        label_size = context.user_data.get("marking_duplicate_chz_size", "58x40")
        if label_size == "75x120" and not product_info:
            raise DuplicateChzError(
                "Не нашёл товар по GTIN кода ЧЗ в МойСклад. "
                "Проверьте штрихкод товара и повторите попытку."
            )
        create_pdf = create_duplicate_chz_75x120_pdf if label_size == "75x120" else create_duplicate_chz_pdf
        create_pdf(raw_code, path, product_info=product_info)
        with open(path, "rb") as file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=file,
                filename=f"duplicate_chz_{label_size}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                caption=f"Дубликат ЧЗ {label_size.replace('x', '×')} мм ✅",
            )
    except DuplicateChzError as error:
        await status_message.edit_text(str(error), reply_markup=marking_menu_keyboard(update))
        return ConversationHandler.END
    except MoySkladError as error:
        logging.exception("Ошибка поиска товара для этикетки ЧЗ в МойСклад")
        await status_message.edit_text(
            f"Не удалось получить товар из МойСклад: {error}",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END
    except Exception as error:
        logging.exception("Ошибка генерации дубликата ЧЗ")
        await status_message.edit_text(
            f"Не удалось сформировать дубликат ЧЗ:\n{error}",
            reply_markup=marking_menu_keyboard(update),
        )
        return ConversationHandler.END
    finally:
        remove_temporary_file(path, "PDF дубликата ЧЗ")

    await status_message.edit_text("Дубликат ЧЗ сформирован ✅", reply_markup=marking_menu_keyboard(update))
    return ConversationHandler.END


async def marking_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_catalog_context(context)
    clear_trend_export_context(context)
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Действие отменено.", reply_markup=marking_menu_keyboard(update))
    elif update.message:
        await update.message.reply_text("Действие отменено.", reply_markup=marking_menu_keyboard(update))

    return ConversationHandler.END


def clear_catalog_context(context):
    clear_catalog_import_context(context)


def clear_catalog_import_context(context):
    context.user_data.pop("marking_catalog_import_rows", None)
    context.user_data.pop("marking_catalog_import_filename", None)


def clear_trend_export_context(context):
    for key in (
        "marking_trend_price_type",
        "marking_trend_document_name",
        "marking_trend_unmarked_quantities",
        "marking_trend_pending_unmarked_id",
    ):
        context.user_data.pop(key, None)


def remove_temporary_file(path, description):
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logging.exception("Не удалось удалить временный файл %s", description)


def get_marking_handlers():
    conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(trend_export_start, pattern=r"^marking:trend_export$"),
            CallbackQueryHandler(stock_codes_export_start, pattern=r"^marking:stock_codes_export$"),
            CallbackQueryHandler(duplicate_chz_start, pattern=r"^marking:duplicate_chz(?:_75x120)?$"),
            CallbackQueryHandler(catalog_start, pattern=r"^marking:catalog$"),
        ],
        states={
            MARKING_DISCOUNTS: [
                CallbackQueryHandler(
                    trend_export_discounts_received,
                    pattern=r"^marking:trend_export:discounts:(yes|no)$",
                ),
                CallbackQueryHandler(
                    trend_export_back,
                    pattern=r"^marking:trend:back:",
                ),
                CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
            ],
            MARKING_DOCUMENT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, trend_export_document_received),
                CallbackQueryHandler(
                    trend_export_back,
                    pattern=r"^marking:trend:back:",
                ),
                CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
            ],
            MARKING_STOCK_CODES_DOCUMENT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    stock_codes_export_document_received,
                ),
                CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
            ],
            MARKING_UNMARKED_CONFIRM: [
                CallbackQueryHandler(
                    trend_unmarked_confirm_received,
                    pattern=r"^marking:trend:unmarked:(yes|no)$",
                ),
                CallbackQueryHandler(
                    trend_export_back,
                    pattern=r"^marking:trend:back:",
                ),
            ],
            MARKING_UNMARKED_PRODUCT: [
                CallbackQueryHandler(
                    trend_unmarked_product_selected,
                    pattern=r"^marking:trend:unmarked:product:\d+$",
                ),
                CallbackQueryHandler(
                    trend_unmarked_selection_done,
                    pattern=r"^marking:trend:unmarked:done$",
                ),
                CallbackQueryHandler(
                    trend_export_back,
                    pattern=r"^marking:trend:back:",
                ),
            ],
            MARKING_UNMARKED_QUANTITY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    trend_unmarked_quantity_received,
                ),
                CallbackQueryHandler(
                    trend_export_back,
                    pattern=r"^marking:trend:back:",
                ),
            ],
            MARKING_DUPLICATE_CHZ_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, duplicate_chz_code_received),
                CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
            ],
            MARKING_CATALOG_MENU: [
                CallbackQueryHandler(catalog_import_start, pattern=r"^marking:catalog:upload$"),
                CallbackQueryHandler(catalog_export, pattern=r"^marking:catalog:export$"),
                CallbackQueryHandler(catalog_back, pattern=r"^marking:catalog:back$"),
            ],
            MARKING_CATALOG_UPLOAD: [
                MessageHandler(filters.Document.ALL, catalog_file_received),
                CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
            ],
            MARKING_CATALOG_IMPORT_CONFIRM: [
                CallbackQueryHandler(
                    catalog_import_confirmed,
                    pattern=r"^marking:catalog:import:confirm$",
                ),
                CallbackQueryHandler(
                    catalog_import_cancelled,
                    pattern=r"^marking:catalog:import:cancel$",
                ),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(marking_cancel, pattern=r"^marking:cancel$"),
        ],
        name="marking_trend_export",
        persistent=False,
    )

    return [conversation]
