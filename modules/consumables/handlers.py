import logging
from datetime import datetime
from html import escape
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import (
    ACTS_CLOSING_DOCUMENTS_TOPIC_ID,
    CONSUMABLES_TOPIC_ID,
    DOCUMENT_WORKFLOW_CHAT_ID,
    GROUP_CHAT_ID,
    WAREHOUSE_INVOICES_TOPIC_ID,
)
from core.keyboards import (
    build_consumables_counting_menu_keyboard,
    build_consumables_menu_keyboard,
    build_consumables_receipt_menu_keyboard,
    build_consumables_supplies_menu_keyboard,
    build_consumables_suppliers_menu_keyboard,
)
from modules.consumables.storage import (
    apply_inventory_session,
    apply_inventory_batch_counts,
    complete_inventory_session,
    create_consumable_receipt,
    create_supply,
    clear_acceptance,
    add_consumable_movement,
    create_inventory_count,
    create_inventory_count_batch,
    discard_inventory_session,
    delete_consumable_receipt,
    create_supplier,
    deactivate_supplier,
    deactivate_consumable_item,
    delete_supply,
    get_accepted_supplies,
    get_active_suppliers,
    get_consumable_item,
    get_consumable_items,
    get_consumable_receipt,
    get_inventory_batch_comparison,
    get_completed_inventory_session_records,
    get_or_create_active_inventory_session,
    get_inventory_session,
    get_inventory_session_items,
    get_pending_inventory_session,
    get_recent_consumable_movements,
    get_recent_inventory_batches,
    list_completed_inventory_sessions,
    list_consumable_receipts,
    get_pending_supplies,
    get_product_consumable_rules,
    get_recent_organizations,
    get_supply,
    get_supplies,
    get_supplier,
    get_supplier_records,
    format_quantity,
    mark_supply_accepted,
    return_inventory_session_to_draft,
    save_inventory_session_item,
    set_product_consumable_rule,
    split_message_ids,
    update_acceptance,
    update_consumable_receipt_quantity,
    update_inventory_review_item,
    update_supply,
    update_supplier,
    upsert_consumable_item,
    SUPPLIER_DOCUMENTS_EDO,
    SUPPLIER_DOCUMENTS_PAPER,
)
from modules.consumables.pdf_reports import create_consumables_stock_pdf, create_inventory_count_pdf
from modules.payroll.google_sheets import find_employee_for_telegram_user, is_manager, money, safe_float
from modules.receiving.products import CATEGORIES, SIZES


INVENTORY_REVIEW_PAGE_SIZE = 15
INVENTORY_CATEGORIES = (
    ("boxes", "📦 Коробки и упаковка", ("короб", "скотч")),
    ("bags", "🛍 Пакеты и мешки", ("пыльник", "курьерский пакет", "дой-пак", "мешк", "zip lock", "пакет")),
    ("labels", "🏷 Бирки, пломбы и наклейки", ("бирк", "пломб", "наклейк")),
    ("number_signs", "🔢 Номерные знаки", ("номерной знак",)),
    ("inserts", "💌 Открытки и вложения", ("открытк", "сертификат")),
    ("printing", "🖨 Печать и этикетки", ("картридж", "бумаг", "этикетк")),
    ("household", "🍴 Хозяйственные товары", ("стакан", "вилк", "ложк")),
    ("other", "📁 Прочее", ()),
)


(
    SUPPLY_NAME,
    SUPPLY_ORGANIZATION,
    SUPPLY_ORGANIZATION_NEW,
    SUPPLY_AMOUNT,
    ACCEPT_SUPPLY,
    ACCEPT_LAYOUT_PHOTO,
    ACCEPT_DOCUMENT,
    SUPPLY_MANAGE_SELECT,
    SUPPLY_EDIT_FIELD,
    SUPPLY_EDIT_VALUE,
    SUPPLY_DELETE_CONFIRM,
    ACCEPTANCE_MANAGE_SELECT,
    ACCEPTANCE_DELETE_CONFIRM,
    SUPPLIER_DELETE_SELECT,
    SUPPLIER_DELETE_CONFIRM,
    ITEM_NAME,
    ITEM_UNIT,
    STOCK_ITEM_SELECT,
    STOCK_QUANTITY,
    RULE_CATEGORY,
    RULE_MODEL,
    RULE_PRODUCT,
    RULE_ITEM_SELECT,
    RULE_QUANTITY,
    SUPPLY_ITEM_SELECT,
    SUPPLY_ITEM_QUANTITY,
    SUPPLY_INVOICE_DOCUMENT,
    INVENTORY_ITEM_SELECT,
    INVENTORY_QUANTITY,
    INVENTORY_COMPARE_SELECT,
    SUPPLY_ORGANIZATION_NEW_DELIVERY,
    SUPPLIER_ADD_NAME,
    SUPPLIER_ADD_DELIVERY,
    SUPPLIER_EDIT_SELECT,
    SUPPLIER_EDIT_NAME,
    SUPPLIER_EDIT_DELIVERY,
    RECEIPT_ITEM_SELECT,
    RECEIPT_QUANTITY,
    RECEIPT_LAYOUT_PHOTOS,
    RECEIPT_DOCUMENT_KIND,
    RECEIPT_DOCUMENT_FILES,
    RECEIPT_CONFIRM,
    INVENTORY_PDF_SELECT,
    INVENTORY_EXIT_CONFIRM,
    RECEIPT_MANAGE_SELECT,
    RECEIPT_EDIT_QUANTITY,
    RECEIPT_DELETE_CONFIRM,
    INVENTORY_REVIEW_SELECT,
    INVENTORY_REVIEW_QUANTITY,
    ITEM_DELETE_SELECT,
    ITEM_DELETE_CONFIRM,
) = range(500, 551)


def current_employee_or_none(update):
    return find_employee_for_telegram_user(update.effective_user)


def current_employee_name(update):
    employee = current_employee_or_none(update)
    if employee:
        return employee["full_name"]
    user = update.effective_user
    return user.full_name or user.username or str(user.id)


def consumables_main_keyboard(update):
    return build_consumables_menu_keyboard(manager=is_manager(current_employee_or_none(update)))


def consumables_receipt_keyboard(update):
    return build_consumables_receipt_menu_keyboard(manager=is_manager(current_employee_or_none(update)))


def consumables_supplies_keyboard(update):
    return build_consumables_supplies_menu_keyboard(manager=is_manager(current_employee_or_none(update)))


def consumables_counting_keyboard(update):
    return build_consumables_counting_menu_keyboard(manager=is_manager(current_employee_or_none(update)))


def consumables_suppliers_keyboard():
    return build_consumables_suppliers_menu_keyboard()


def set_consumables_module(context, module):
    context.user_data.clear()
    context.user_data["consumables_module"] = module


def consumables_back_keyboard(back_target=None):
    rows = []
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"consback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def organization_keyboard(organizations=None, allow_new=True, back_target="supply_name"):
    organizations = organizations if organizations is not None else get_recent_organizations()
    rows = []
    for index, organization in enumerate(organizations):
        rows.append([InlineKeyboardButton(organization, callback_data=f"consorg:{index}")])

    if allow_new:
        rows.append([InlineKeyboardButton("➕ Новый поставщик", callback_data="consorg:new")])
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"consback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def supplies_keyboard(supplies, prefix):
    rows = []
    for supply in supplies:
        rows.append([InlineKeyboardButton(f"#{supply['id']}", callback_data=f"{prefix}:{supply['id']}")])

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def pending_supplies_keyboard():
    return supplies_keyboard(get_pending_supplies(), "conssup")


def supplies_list_text(title, supplies):
    lines = [title]
    for supply in supplies:
        lines.extend(["", format_supply_line(supply)])
        if supply.get("status") == "accepted":
            accepted_at = supply.get("accepted_at")
            accepted_at_text = accepted_at.strftime("%d.%m.%Y %H:%M") if accepted_at else "-"
            lines.append(f"Принял: {supply.get('accepted_by_name') or '-'}")
            lines.append(f"Дата приемки: {accepted_at_text}")
    return "\n".join(lines)


def document_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Документа нет", callback_data="consdoc:none")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")],
        ]
    )


def supply_edit_field_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Название счета", callback_data="consfield:name")],
            [InlineKeyboardButton("Контрагент", callback_data="consfield:organization")],
            [InlineKeyboardButton("Сумма к оплате", callback_data="consfield:amount")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")],
        ]
    )


def confirm_keyboard(confirm_callback, back_target=None):
    rows = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data=confirm_callback)],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"consback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def suppliers_keyboard(suppliers):
    rows = []
    for index, supplier in enumerate(suppliers):
        rows.append([InlineKeyboardButton(supplier, callback_data=f"conssupplier:{index}")])

    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def supplier_records_keyboard(suppliers, prefix):
    rows = [
        [InlineKeyboardButton(button_text(supplier["name"]), callback_data=f"{prefix}:{supplier['id']}")]
        for supplier in suppliers
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def supplier_documents_delivery_keyboard(prefix, back_target=None):
    rows = [
            [InlineKeyboardButton("📡 По ЭДО", callback_data=f"{prefix}:edo")],
            [InlineKeyboardButton("📄 В бумажном виде", callback_data=f"{prefix}:paper")],
    ]
    if back_target:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"consback:{back_target}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def supplier_documents_delivery_text(value):
    return "по ЭДО" if value == SUPPLIER_DOCUMENTS_EDO else "в бумажном виде"


def suppliers_list_text(title, suppliers):
    lines = [title]
    for supplier in suppliers:
        lines.extend(
            [
                "",
                supplier["name"],
                "Закрывающие документы: "
                + supplier_documents_delivery_text(supplier["closing_documents_delivery"]),
            ]
        )
    return "\n".join(lines)


def consumable_items_keyboard(items, prefix):
    rows = []
    for item in items:
        rows.append([InlineKeyboardButton(item["name"], callback_data=f"{prefix}:{item['item_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def consumable_item_delete_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Да, удалить из учета", callback_data="consdeleteitem:yes")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="consdeleteitem:back")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")],
        ]
    )


def button_text(value, limit=58):
    value = str(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def supply_items_keyboard(context):
    selected = context.user_data.get("supply_items", {})
    rows = []
    for item in get_consumable_items(active_only=True):
        selected_item = selected.get(str(item["item_id"]))
        suffix = ""
        if selected_item:
            suffix = f" - {format_quantity(selected_item['quantity'])} {item['unit']} ✅"
        rows.append([InlineKeyboardButton(button_text(item["name"] + suffix), callback_data=f"conssupplyitem:{item['item_id']}")])
    if selected:
        rows.append([InlineKeyboardButton("✅ Дальше", callback_data="conssupplyitems:done")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def supply_items_text(context):
    selected = context.user_data.get("supply_items", {})
    lines = ["Выберите расходник из списка."]
    if selected:
        lines.extend(["", "В поставке:"])
        for item in selected.values():
            lines.append(f"{item['item_name']}: {format_quantity(item['quantity'])} {item['unit']}")
    return "\n".join(lines)


def inventory_count_keyboard(context):
    counts = context.user_data.get("inventory_counts", {})
    rows = []
    for item in context.user_data.get("inventory_items", get_consumable_items(active_only=True)):
        value = counts.get(str(item["item_id"]))
        suffix = f" - {format_quantity(value)} {item['unit']} ✅" if value is not None else ""
        rows.append([InlineKeyboardButton(button_text(item["name"] + suffix), callback_data=f"consinventoryitem:{item['item_id']}")])
    if counts:
        rows.append([InlineKeyboardButton("✅ Пересчет окончен", callback_data="consinventory:finish")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def inventory_quantity_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К пересчету", callback_data="consinventory:back")]])


def inventory_count_text(context):
    counts = context.user_data.get("inventory_counts", {})
    return f"🔢 Пересчет расходников\n\nЗаполнено: {len(counts)}"


def receipt_layout_photos_keyboard(has_photos):
    rows = []
    if has_photos:
        rows.append([InlineKeyboardButton("✅ Фото загружены", callback_data="receiptphotos:done")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="consback:receipt_quantity")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def receipt_document_kind_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Нет бумажного документа", callback_data="receiptdoc:no_paper")],
            [InlineKeyboardButton("Скан-копия", callback_data="receiptdoc:scan")],
            [InlineKeyboardButton("Фото", callback_data="receiptdoc:photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="consback:receipt_layout")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")],
        ]
    )


def receipt_document_files_keyboard(has_files):
    rows = []
    if has_files:
        rows.append([InlineKeyboardButton("✅ Документы загружены", callback_data="receiptdocs:done")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="consback:receipt_document_kind")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def receipt_preview_text(context):
    item = get_consumable_item(context.user_data.get("receipt_item_id"))
    item_name = item["name"] if item else "Расходник"
    document_labels = {
        "no_paper": "Нет бумажного документа",
        "scan": "Скан-копия",
        "photo": "Фото",
    }
    return "\n".join(
        [
            "📥 Приемка расходника",
            "",
            f"Расходник: {item_name}",
            f"Количество: {format_quantity(context.user_data.get('receipt_quantity'))} {item.get('unit', 'шт') if item else 'шт'}",
            f"Фото приемки: {len(context.user_data.get('receipt_layout_photo_file_ids', []))}",
            "Закрывающий документ: " + document_labels[context.user_data["receipt_document_kind"]],
            f"Вложений документа: {len(context.user_data.get('receipt_document_file_ids', []))}",
        ]
    )


def receipt_manage_keyboard(receipts):
    rows = []
    for receipt in receipts:
        created_at = receipt.get("created_at")
        created_text = created_at.strftime("%d.%m %H:%M") if created_at else ""
        label = (
            f"{created_text} · {button_text(receipt['item_name'], limit=30)} · "
            f"{format_quantity(receipt['quantity'])} {receipt['unit']}"
        )
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"consreceiptmanage:{receipt['receipt_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def receipt_delete_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Да, удалить приемку", callback_data="consreceiptdelete:yes")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")],
        ]
    )


def inventory_category_definition(category_key):
    return next(
        (category for category in INVENTORY_CATEGORIES if category[0] == category_key),
        None,
    )


def inventory_record_category(record):
    item_name = str(record.get("item_name") or "").strip().casefold().replace("ё", "е")
    for category_key, _, patterns in INVENTORY_CATEGORIES:
        if category_key == "other":
            continue
        if any(pattern in item_name for pattern in patterns):
            return category_key
    return "other"


def inventory_category_records(records, category_key):
    return [record for record in records if inventory_record_category(record) == category_key]


def inventory_session_text(session, records, category_key=None):
    counted = sum(1 for record in records if record["counted"])
    lines = [
        "🔢 Параллельный пересчет расходников",
        "",
        f"Участников: {session.get('participant_count', 0)}",
        f"Посчитано: {counted} из {len(records)}",
    ]
    category = inventory_category_definition(category_key)
    if category:
        category_records = inventory_category_records(records, category_key)
        category_counted = sum(1 for record in category_records if record["counted"])
        lines.extend(
            [
                "",
                category[1],
                f"В папке посчитано: {category_counted} из {len(category_records)}",
            ]
        )
    else:
        lines.extend(["", "Выберите папку расходников:"])
    return "\n".join(lines)


def inventory_session_keyboard(records, category_key=None):
    rows = []
    category = inventory_category_definition(category_key)
    if category:
        for record in inventory_category_records(records, category_key):
            status = "✅" if record["counted"] else "⬜"
            label = f"{status} {button_text(record['item_name'], limit=46)}"
            rows.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"consinventoryitem:{record['item_id']}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton("⬅️ К папкам", callback_data="consinventory:categories")])
    else:
        for folder_key, folder_name, _ in INVENTORY_CATEGORIES:
            folder_records = inventory_category_records(records, folder_key)
            if not folder_records:
                continue
            folder_counted = sum(1 for record in folder_records if record["counted"])
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{folder_name} · {folder_counted}/{len(folder_records)}",
                        callback_data=f"consinventory:category:{folder_key}",
                    )
                ]
            )
    if any(record["counted"] for record in records):
        rows.append([InlineKeyboardButton("✅ Завершить пересчет", callback_data="consinventory:finish")])
    rows.append([InlineKeyboardButton("❌ Выйти", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def inventory_exit_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💾 Сохранить и выйти", callback_data="consinventory:exit:save")],
            [InlineKeyboardButton("↩️ Продолжить пересчет", callback_data="consinventory:exit:resume")],
            [InlineKeyboardButton("🗑 Удалить черновик", callback_data="consinventory:exit:discard")],
        ]
    )


def inventory_discard_confirm_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Да, удалить черновик", callback_data="consinventory:discard:yes")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="consinventory:exit:resume")],
        ]
    )


def inventory_complete_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Отправить руководителю", callback_data="consinventory:complete:yes")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="consinventory:complete:no")],
        ]
    )


def inventory_pdf_keyboard(sessions):
    rows = []
    for inventory_session in sessions:
        completed_at = inventory_session.get("completed_at") or inventory_session.get("created_at")
        completed_text = completed_at.strftime("%d.%m.%Y %H:%M") if completed_at else ""
        label = f"{completed_text} · {inventory_session.get('completed_by_name') or inventory_session.get('created_by_name') or '-'}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"consinventorypdf:{inventory_session['session_id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def comparison_is_large(diff, current_quantity):
    diff = abs(float(diff or 0))
    current_quantity = abs(float(current_quantity or 0))
    if diff >= 10:
        return True
    if current_quantity > 0 and diff / current_quantity >= 0.2:
        return True
    return False


def comparison_text(records, selected_ids):
    if not records:
        return "⚖️ Сравнение\n\nНет данных для сравнения."
    lines = ["⚖️ Сравнение", ""]
    for record in records:
        diff = float(record["current_difference"] or 0)
        sign = "+" if diff > 0 else ""
        marker = " 🟡" if comparison_is_large(diff, record["current_quantity"]) else ""
        chosen = "✓ " if int(record["item_id"]) in selected_ids else ""
        item_name = button_text(record["item_name"], limit=32)
        lines.append(
            f"{chosen}{item_name}: бот {format_quantity(record['current_quantity'])} {record['unit']}, "
            f"факт {format_quantity(record['counted_quantity'])} {record['unit']}, "
            f"разница {sign}{format_quantity(diff)}{marker}"
        )
    lines.append("")
    lines.append("Выберите строки, которые нужно заменить фактическим значением сотрудника.")
    return "\n".join(lines)


def comparison_keyboard(records, selected_ids):
    rows = []
    for record in records:
        diff = float(record["current_difference"] or 0)
        if diff == 0:
            continue
        item_id = int(record["item_id"])
        prefix = "✅ " if item_id in selected_ids else ""
        rows.append([InlineKeyboardButton(button_text(prefix + record["item_name"]), callback_data=f"conscompare:toggle:{item_id}")])
    if selected_ids:
        rows.append([InlineKeyboardButton("✅ Применить выбранное", callback_data="conscompare:apply")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def inventory_review_page(records, page=0):
    page_count = max(1, (len(records) + INVENTORY_REVIEW_PAGE_SIZE - 1) // INVENTORY_REVIEW_PAGE_SIZE)
    page = min(max(int(page or 0), 0), page_count - 1)
    start = page * INVENTORY_REVIEW_PAGE_SIZE
    end = min(start + INVENTORY_REVIEW_PAGE_SIZE, len(records))
    return page, page_count, start, end, records[start:end]


def inventory_review_text(inventory_session, records, page=0):
    page, page_count, start, end, page_records = inventory_review_page(records, page)
    lines = [
        "🔎 Проверка пересчета расходников",
        "",
        f"Отправил: {inventory_session.get('completed_by_name') or '—'}",
        f"Заполнено: {sum(1 for record in records if record['counted'])} из {len(records)}",
        f"Позиции: {start + 1 if records else 0}–{end} из {len(records)} · страница {page + 1}/{page_count}",
        "",
    ]
    for record in page_records:
        system_quantity = format_quantity(record["system_quantity"])
        unit = button_text(record["unit"], 16)
        if not record["counted"]:
            lines.append(
                f"• {button_text(record['item_name'], 34)}: "
                f"система {system_quantity} {unit}, факт не указан"
            )
            continue
        counted_quantity = float(record["counted_quantity"] or 0)
        difference = counted_quantity - float(record["system_quantity"] or 0)
        sign = "+" if difference > 0 else ""
        lines.append(
            f"• {button_text(record['item_name'], 34)}: "
            f"система {system_quantity}, факт {format_quantity(counted_quantity)}, "
            f"разница {sign}{format_quantity(difference)} {unit} — "
            f"{button_text(record.get('counted_by_name') or '—', 32)}"
        )
    return "\n".join(lines)


def inventory_review_keyboard(records, page=0):
    page, page_count, _, _, page_records = inventory_review_page(records, page)
    rows = [
        [
            InlineKeyboardButton(
                button_text(f"✏️ {record['item_name']}"),
                callback_data=f"consreview:item:{record['item_id']}",
            )
        ]
        for record in page_records
    ]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("⬅️", callback_data=f"consreview:page:{page - 1}"))
    if page + 1 < page_count:
        navigation.append(InlineKeyboardButton("➡️", callback_data=f"consreview:page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.extend(
        [
            [InlineKeyboardButton("✅ Применить пересчет", callback_data="consreview:apply")],
            [InlineKeyboardButton("↩️ Вернуть на исправление", callback_data="consreview:reject")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="cons:cancel")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def inventory_review_quantity_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ К проверке", callback_data="consreview:back")]]
    )


def consumable_category_keyboard():
    rows = [
        [InlineKeyboardButton(category_data["name"], callback_data=f"conscat:{category_id}")]
        for category_id, category_data in CATEGORIES.items()
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def consumable_models_keyboard(category_id):
    rows = [
        [InlineKeyboardButton(model_data["name"], callback_data=f"consmodel:{model_id}")]
        for model_id, model_data in CATEGORIES[category_id]["models"].items()
    ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def consumable_products_keyboard(category_id, model_id):
    rows = []
    for variant_data in CATEGORIES[category_id]["models"][model_id]["variants"].values():
        text = "Выбрать" if variant_data["color"] == "ONE COLOR" else variant_data["color"]
        rows.append([InlineKeyboardButton(text, callback_data=f"consprod:{variant_data['id']}")])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cons:cancel")])
    return InlineKeyboardMarkup(rows)


def parse_positive_amount(text):
    value = safe_float(text)
    if value <= 0:
        return None
    return value


def format_supply_line(supply):
    lines = [
        f"#{supply['id']} {supply['consumable_name']}",
        f"Контрагент: {supply['organization']}",
        f"Сумма к оплате: {money(supply['amount'])}",
    ]
    if supply.get("supply_items"):
        lines.append("Состав:")
        for item in supply["supply_items"]:
            lines.append(f"- {item['item_name']}: {format_quantity(item['quantity'])} {item.get('unit') or 'шт'}")
    return "\n".join(lines)


def format_acceptance_text(supply, accepted_by, document_status):
    return "\n".join(
        [
            "📥 Приемка расходника",
            "",
            format_supply_line(supply),
            "",
            f"Принял: {accepted_by}",
            f"Закрывающий документ: {document_status}",
        ]
    )


def format_stock_text(items):
    if not items:
        return "📊 Остатки расходников\n\nРасходники пока не заведены."

    lines = ["📊 Остатки расходников", ""]
    for item in items:
        lines.append(f"{item['name']}: {format_quantity(item['current_quantity'])} {item['unit']}")
    return "\n".join(lines)


def format_movements_text(movements):
    if not movements:
        return "🧾 Движения расходников\n\nДвижений пока нет."

    lines = ["🧾 Последние движения расходников", ""]
    for movement in movements:
        created = movement["created_at"].strftime("%d.%m.%Y %H:%M") if movement.get("created_at") else ""
        delta = format_quantity(movement["quantity_delta"])
        sign = "+" if float(movement["quantity_delta"] or 0) > 0 else ""
        lines.append(f"{created} · {movement['item_name']}: {sign}{delta}")
        if movement.get("comment"):
            lines.append(movement["comment"])
    return "\n".join(lines)


def format_rules_text(product_name, rules):
    lines = [f"⚙️ Нормы расходников\n\nТовар: {product_name}", ""]
    if not rules:
        lines.append("Для этого товара нормы пока не настроены.")
    else:
        for rule in rules:
            lines.append(f"{rule['item_name']}: {format_quantity(rule['quantity_per_unit'])} {rule['unit']} на 1 шт.")
    return "\n".join(lines)


async def consumables_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    employee = current_employee_or_none(update)
    await query.edit_message_text(
        "🧾 Расходники",
        reply_markup=build_consumables_menu_keyboard(manager=is_manager(employee)),
    )
    return ConversationHandler.END


async def consumables_supplies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")
    await query.edit_message_text("📦 Поставки расходников", reply_markup=consumables_supplies_keyboard(update))
    return ConversationHandler.END


async def consumables_suppliers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    set_consumables_module(context, "suppliers")
    await query.edit_message_text("🤝 Поставщики", reply_markup=consumables_suppliers_keyboard())
    return ConversationHandler.END


async def consumables_counting_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    await query.edit_message_text("🔢 Пересчет расходников", reply_markup=consumables_counting_keyboard(update))
    return ConversationHandler.END


async def consumables_receipt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "receipt")
    await query.edit_message_text("📥 Приемка расходника", reply_markup=consumables_receipt_keyboard(update))
    return ConversationHandler.END


async def consumables_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    module = context.user_data.get("consumables_module")
    context.user_data.clear()
    if module == "supplies":
        text = "📦 Поставки расходников"
        keyboard = consumables_supplies_keyboard(update)
    elif module == "suppliers":
        text = "🤝 Поставщики"
        keyboard = consumables_suppliers_keyboard()
    elif module == "counting":
        text = "🔢 Пересчет расходников"
        keyboard = consumables_counting_keyboard(update)
    else:
        text = "🧾 Расходники"
        keyboard = consumables_main_keyboard(update)

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text("Действие отменено.", reply_markup=keyboard)

    return ConversationHandler.END


async def receipt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "receipt")
    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text("Расходники пока не заведены.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите принятый расходник:",
        reply_markup=consumable_items_keyboard(items, "consreceiptitem"),
    )
    return RECEIPT_ITEM_SELECT


async def receipt_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["receipt_item_id"] = int(query.data.replace("consreceiptitem:", ""))
    item = get_consumable_item(context.user_data["receipt_item_id"])
    if not item:
        await query.edit_message_text("Расходник не найден.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    await query.edit_message_text(
        f"Введите принятое количество ({item['unit']}):",
        reply_markup=consumables_back_keyboard("receipt_items"),
    )
    return RECEIPT_QUANTITY


async def receipt_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity <= 0:
        await update.message.reply_text("Введите число больше нуля:")
        return RECEIPT_QUANTITY
    context.user_data["receipt_quantity"] = quantity
    context.user_data["receipt_layout_photo_file_ids"] = []
    await update.message.reply_text(
        "Отправьте фото принятого расходника, разложенного на складе. Можно добавить несколько фото.",
        reply_markup=receipt_layout_photos_keyboard(False),
    )
    return RECEIPT_LAYOUT_PHOTOS


async def receipt_layout_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("receipt_layout_photo_file_ids", [])
    photos.append({"file_id": update.message.photo[-1].file_id, "kind": "photo"})
    await update.message.reply_text(
        f"Фото добавлено ✅ Всего: {len(photos)}. Отправьте еще фото или нажмите «Фото загружены».",
        reply_markup=receipt_layout_photos_keyboard(True),
    )
    return RECEIPT_LAYOUT_PHOTOS


async def receipt_layout_photos_finished(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("receipt_layout_photo_file_ids"):
        await query.edit_message_text(
            "Нужно добавить хотя бы одно фото приемки.",
            reply_markup=receipt_layout_photos_keyboard(False),
        )
        return RECEIPT_LAYOUT_PHOTOS
    await query.edit_message_text(
        "Выберите тип закрывающего документа:",
        reply_markup=receipt_document_kind_keyboard(),
    )
    return RECEIPT_DOCUMENT_KIND


async def receipt_document_kind_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind = query.data.replace("receiptdoc:", "")
    context.user_data["receipt_document_kind"] = kind
    context.user_data["receipt_document_file_ids"] = []
    if kind == "no_paper":
        await query.edit_message_text(
            receipt_preview_text(context),
            reply_markup=confirm_keyboard("receipt:confirm", "receipt_document_kind"),
        )
        return RECEIPT_CONFIRM
    await query.edit_message_text(
        "Отправьте закрывающий документ. Можно добавить несколько файлов или фото.",
        reply_markup=receipt_document_files_keyboard(False),
    )
    return RECEIPT_DOCUMENT_FILES


async def receipt_document_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_kind = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_kind = "document"
    else:
        await update.message.reply_text(
            "Отправьте документ или фото закрывающего документа.",
            reply_markup=receipt_document_files_keyboard(
                bool(context.user_data.get("receipt_document_file_ids"))
            ),
        )
        return RECEIPT_DOCUMENT_FILES
    files = context.user_data.setdefault("receipt_document_file_ids", [])
    files.append({"file_id": file_id, "kind": file_kind})
    await update.message.reply_text(
        f"Документ добавлен ✅ Всего: {len(files)}. Отправьте еще файл или нажмите «Документы загружены».",
        reply_markup=receipt_document_files_keyboard(True),
    )
    return RECEIPT_DOCUMENT_FILES


async def receipt_document_files_finished(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("receipt_document_file_ids"):
        await query.edit_message_text(
            "Нужно добавить хотя бы один закрывающий документ.",
            reply_markup=receipt_document_files_keyboard(False),
        )
        return RECEIPT_DOCUMENT_FILES
    await query.edit_message_text(
        receipt_preview_text(context),
        reply_markup=confirm_keyboard("receipt:confirm", "receipt_document_files"),
    )
    return RECEIPT_CONFIRM


async def receipt_confirm_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        receipt = create_consumable_receipt(
            item_id=context.user_data["receipt_item_id"],
            quantity=context.user_data["receipt_quantity"],
            accepted_by_user_id=update.effective_user.id,
            accepted_by_name=current_employee_name(update),
            layout_photo_file_ids=context.user_data.get("receipt_layout_photo_file_ids"),
            closing_document_kind=context.user_data.get("receipt_document_kind", "no_paper"),
            closing_document_file_ids=context.user_data.get("receipt_document_file_ids"),
        )
    except RuntimeError as error:
        await query.edit_message_text(f"Не удалось сохранить приемку: {error}", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    try:
        _, topic_status = await send_receipt_to_consumables_topic(context, receipt)
    except Exception as error:
        logging.exception("Не удалось отправить отчет о приемке расходников в тему")
        topic_status = f"Приемка сохранена, но отчет в тему не отправлен ⚠️\nОшибка: {error}"
    context.user_data.clear()
    await query.edit_message_text(
        "Приемка расходника сохранена ✅\n\n"
        f"{receipt['item_name']}: +{format_quantity(receipt['quantity'])} {receipt['unit']}\n\n"
        f"{topic_status}",
        reply_markup=consumables_main_keyboard(update),
    )
    return ConversationHandler.END


def receipt_document_kind_text(kind):
    return {
        "no_paper": "Нет бумажного документа",
        "scan": "Скан-копия",
        "photo": "Фото",
    }.get(kind, "—")


def receipt_topic_caption(receipt):
    return "\n".join(
        [
            "📥 Приемка расходников",
            "",
            f"Расходник: {receipt['item_name']}",
            f"Количество: +{format_quantity(receipt['quantity'])} {receipt['unit']}",
            f"Принял: {receipt['accepted_by_name'] or '—'}",
            f"Закрывающий документ: {receipt_document_kind_text(receipt['closing_document_kind'])}",
        ]
    )


async def send_receipt_to_consumables_topic(context: ContextTypes.DEFAULT_TYPE, receipt):
    if not GROUP_CHAT_ID:
        return [], "GROUP_CHAT_ID не настроен, отчет в тему не отправлен."

    kwargs = {"chat_id": int(GROUP_CHAT_ID)}
    if CONSUMABLES_TOPIC_ID:
        kwargs["message_thread_id"] = int(CONSUMABLES_TOPIC_ID)

    message_ids = []
    for index, photo in enumerate(receipt["layout_photo_file_ids"]):
        message = await context.bot.send_photo(
            **kwargs,
            photo=photo["file_id"],
            caption=receipt_topic_caption(receipt) if index == 0 else "Фото приемки расходников",
        )
        message_ids.append(message.message_id)

    closing_documents = receipt["closing_document_file_ids"]
    for index, document in enumerate(closing_documents):
        caption = "Закрывающий документ" if index == 0 else "Закрывающий документ (продолжение)"
        if document.get("kind") == "photo":
            message = await context.bot.send_photo(
                **kwargs,
                photo=document["file_id"],
                caption=caption,
            )
        else:
            message = await context.bot.send_document(
                **kwargs,
                document=document["file_id"],
                caption=caption,
            )
        message_ids.append(message.message_id)
    return message_ids, "Отчет о приемке отправлен в тему расходников ✅"


async def receipt_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    action = "edit" if query.data == "cons:receipt_edit" else "delete"
    receipts = list_consumable_receipts(limit=30)
    if not receipts:
        await query.edit_message_text("Приемок пока нет.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    set_consumables_module(context, "receipt")
    context.user_data["receipt_manage_action"] = action
    title = "Выберите приемку для изменения количества:" if action == "edit" else "Выберите приемку для удаления:"
    await query.edit_message_text(title, reply_markup=receipt_manage_keyboard(receipts))
    return RECEIPT_MANAGE_SELECT


async def receipt_manage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    receipt = get_consumable_receipt(query.data.replace("consreceiptmanage:", ""))
    if not receipt:
        await query.edit_message_text("Приемка не найдена.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    context.user_data["receipt_manage_id"] = receipt["receipt_id"]
    action = context.user_data.get("receipt_manage_action")
    if action == "edit":
        await query.edit_message_text(
            "Изменение приемки\n\n"
            f"{receipt['item_name']}\n"
            f"Текущее количество: {format_quantity(receipt['quantity'])} {receipt['unit']}\n\n"
            "Введите новое количество:",
            reply_markup=consumables_back_keyboard(),
        )
        return RECEIPT_EDIT_QUANTITY
    await query.edit_message_text(
        "Удалить приемку и вычесть ее количество из остатка?\n\n"
        f"{receipt['item_name']}: {format_quantity(receipt['quantity'])} {receipt['unit']}",
        reply_markup=receipt_delete_confirm_keyboard(),
    )
    return RECEIPT_DELETE_CONFIRM


async def receipt_edit_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity <= 0:
        await update.message.reply_text("Введите количество больше нуля:")
        return RECEIPT_EDIT_QUANTITY
    try:
        receipt = update_consumable_receipt_quantity(
            context.user_data.get("receipt_manage_id"),
            quantity,
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await update.message.reply_text(str(error), reply_markup=consumables_main_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.clear()
    if not receipt:
        await update.message.reply_text("Приемка не найдена.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    await update.message.reply_text(
        "Приемка изменена ✅\n\n"
        f"{receipt['item_name']}: {format_quantity(receipt['quantity'])} {receipt['unit']}",
        reply_markup=consumables_main_keyboard(update),
    )
    return ConversationHandler.END


async def receipt_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    receipt = delete_consumable_receipt(
        context.user_data.get("receipt_manage_id"),
        update.effective_user.id,
        current_employee_name(update),
    )
    context.user_data.clear()
    if not receipt:
        await query.edit_message_text("Приемка не найдена.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    await query.edit_message_text(
        "Приемка удалена, остаток скорректирован ✅\n\n"
        f"{receipt['item_name']}: -{format_quantity(receipt['quantity'])} {receipt['unit']}",
        reply_markup=consumables_main_keyboard(update),
    )
    return ConversationHandler.END


async def add_supply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    employee = current_employee_or_none(update)
    if not is_manager(employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text(
            "Список расходников пуст. Сначала добавьте расходники в учет.",
            reply_markup=consumables_supplies_keyboard(update),
        )
        return ConversationHandler.END

    context.user_data["supply_items"] = {}
    await query.edit_message_text(supply_items_text(context), reply_markup=supply_items_keyboard(context))
    return SUPPLY_ITEM_SELECT


async def supply_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Введите наименование счета:")
        return SUPPLY_NAME

    context.user_data["supply_name"] = name
    context.user_data["organizations"] = get_active_suppliers()
    await update.message.reply_text(
        "Выберите поставщика:",
        reply_markup=organization_keyboard(context.user_data["organizations"], allow_new=True),
    )
    return SUPPLY_ORGANIZATION


async def supply_organization_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "consorg:new":
        await query.edit_message_text("Введите наименование нового поставщика:", reply_markup=consumables_back_keyboard("supply_organization"))
        return SUPPLY_ORGANIZATION_NEW

    try:
        index = int(data.replace("consorg:", ""))
        organization = context.user_data.get("organizations", [])[index]
    except (ValueError, IndexError):
        await query.edit_message_text(
            "Поставщик не найден. Выберите заново:",
            reply_markup=organization_keyboard(context.user_data.get("organizations", []), allow_new=True),
        )
        return SUPPLY_ORGANIZATION

    context.user_data["organization"] = organization
    await query.edit_message_text("Введите сумму к оплате:", reply_markup=consumables_back_keyboard("supply_organization"))
    return SUPPLY_AMOUNT


async def supply_organization_new_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    organization = update.message.text.strip()
    if not organization:
        await update.message.reply_text("Название поставщика не должно быть пустым. Введите наименование:")
        return SUPPLY_ORGANIZATION_NEW

    context.user_data["organization"] = organization
    await update.message.reply_text(
        "Как поставщик доставляет закрывающие документы?",
        reply_markup=supplier_documents_delivery_keyboard("consorgdelivery", "supply_organization_new"),
    )
    return SUPPLY_ORGANIZATION_NEW_DELIVERY


async def supply_organization_new_delivery_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delivery = query.data.replace("consorgdelivery:", "")
    supplier = create_supplier(context.user_data.get("organization"), delivery)
    context.user_data["organization"] = supplier["name"]
    await query.edit_message_text("Введите сумму к оплате:", reply_markup=consumables_back_keyboard("supply_organization"))
    return SUPPLY_AMOUNT


async def supply_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = parse_positive_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("Введите сумму числом больше 0:", reply_markup=consumables_back_keyboard("supply_organization"))
        return SUPPLY_AMOUNT

    context.user_data["supply_amount"] = amount
    await update.message.reply_text(
        "Отправьте файл счета PDF, документом или фото.",
        reply_markup=consumables_back_keyboard("supply_amount"),
    )
    return SUPPLY_INVOICE_DOCUMENT


async def supply_invoice_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        invoice_file_id = update.message.photo[-1].file_id
        invoice_kind = "photo"
    elif update.message.document:
        invoice_file_id = update.message.document.file_id
        invoice_kind = "document"
    else:
        await update.message.reply_text(
            "Отправьте счет PDF, документом или фото.",
            reply_markup=consumables_back_keyboard("supply_amount"),
        )
        return SUPPLY_INVOICE_DOCUMENT

    created_by = current_employee_name(update)
    supply = create_supply(
        consumable_name=context.user_data["supply_name"],
        organization=context.user_data["organization"],
        amount=context.user_data["supply_amount"],
        supply_items=list(context.user_data.get("supply_items", {}).values()),
        created_by_user_id=update.effective_user.id,
        created_by_name=created_by,
        invoice_document_file_id=invoice_file_id,
        invoice_document_kind=invoice_kind,
    )

    try:
        invoice_status = await send_invoice_to_document_workflow_topic(
            context,
            supply=supply,
            invoice_file_id=invoice_file_id,
            invoice_kind=invoice_kind,
        )
    except Exception as error:
        logging.exception("Не удалось отправить счет расходников в документооборот")
        invoice_status = f"Счет не отправлен в документооборот ⚠️\nОшибка: {error}"

    text = "Поставка создана ✅\n\n" + format_supply_line(supply)
    text += "\n\nОстатки будут пополнены после приемки поставки."
    text += f"\n\n{invoice_status}"

    await update.message.reply_text(text, reply_markup=consumables_supplies_keyboard(update))
    context.user_data.clear()
    return ConversationHandler.END


async def supply_invoice_wrong_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте счет PDF, документом или фото.",
        reply_markup=consumables_back_keyboard("supply_amount"),
    )
    return SUPPLY_INVOICE_DOCUMENT


async def stock_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text(
            "Расходники пока не заведены.",
            reply_markup=consumables_main_keyboard(update),
        )
        return ConversationHandler.END
    filename = f"consumables_stock_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    report_path = create_consumables_stock_pdf(items, filename=filename)
    try:
        with report_path.open("rb") as document:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=document,
                filename=filename,
                caption="Остатки расходников",
            )
    finally:
        Path(report_path).unlink(missing_ok=True)
    await query.edit_message_text(
        "PDF с остатками отправлен ✅",
        reply_markup=consumables_main_keyboard(update),
    )
    return ConversationHandler.END


async def movements_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["consumables_module"] = "counting"
    await query.edit_message_text(
        format_movements_text(get_recent_consumable_movements(limit=20)),
        reply_markup=consumables_counting_keyboard(update),
    )
    return ConversationHandler.END


async def inventory_count_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    if not get_consumable_items(active_only=True):
        await query.edit_message_text("Расходники пока не заведены.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    try:
        inventory_session = get_or_create_active_inventory_session(
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await query.edit_message_text(str(error), reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    context.user_data["inventory_session_id"] = inventory_session["session_id"]
    records = get_inventory_session_items(inventory_session["session_id"])
    await query.edit_message_text(
        inventory_session_text(inventory_session, records),
        reply_markup=inventory_session_keyboard(records),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    if not session_id:
        await query.edit_message_text("Откройте пересчет заново.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    category_key = query.data.rsplit(":", 1)[-1]
    if not inventory_category_definition(category_key):
        await query.edit_message_text("Папка не найдена.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    inventory_session = get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
    if not inventory_session or inventory_session["status"] != "draft":
        await query.edit_message_text("Пересчет уже завершен.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    records = get_inventory_session_items(session_id)
    context.user_data["inventory_category"] = category_key
    await query.edit_message_text(
        inventory_session_text(inventory_session, records, category_key),
        reply_markup=inventory_session_keyboard(records, category_key),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_categories_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    inventory_session = (
        get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
        if session_id
        else None
    )
    if not inventory_session or inventory_session["status"] != "draft":
        await query.edit_message_text("Пересчет уже завершен.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    context.user_data.pop("inventory_category", None)
    records = get_inventory_session_items(session_id)
    await query.edit_message_text(
        inventory_session_text(inventory_session, records),
        reply_markup=inventory_session_keyboard(records),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("inventory_session_id"):
        await query.edit_message_text("Откройте пересчет заново.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    context.user_data["inventory_item_id"] = int(query.data.replace("consinventoryitem:", ""))
    item = get_consumable_item(context.user_data["inventory_item_id"])
    item_name = item["name"] if item else "Расходник"
    current_record = next(
        (
            record
            for record in get_inventory_session_items(context.user_data["inventory_session_id"])
            if int(record["item_id"]) == context.user_data["inventory_item_id"]
        ),
        None,
    )
    unit = current_record["unit"] if current_record else (item.get("unit", "шт") if item else "шт")
    system_quantity = current_record["system_quantity"] if current_record else 0
    counted_quantity = (
        current_record["counted_quantity"]
        if current_record and current_record["counted"]
        else 0
    )
    await query.edit_message_text(
        "Введите фактическое количество:\n\n"
        f"Расходник: {item_name}\n"
        f"Значение в системе: {format_quantity(system_quantity)} {unit}\n"
        f"Фактическое значение: {format_quantity(counted_quantity)} {unit}",
        reply_markup=inventory_quantity_keyboard(),
    )
    return INVENTORY_QUANTITY


async def inventory_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    inventory_session = get_or_create_active_inventory_session(
        update.effective_user.id,
        current_employee_name(update),
    ) if not session_id else None
    if inventory_session:
        session_id = inventory_session["session_id"]
        context.user_data["inventory_session_id"] = session_id
    records = get_inventory_session_items(session_id)
    session_info = inventory_session or {
        "participant_count": 0,
    }
    if not inventory_session:
        session_info = get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
    if not session_info or session_info["status"] != "draft":
        await query.edit_message_text("Пересчет уже завершен.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    category_key = context.user_data.get("inventory_category")
    await query.edit_message_text(
        inventory_session_text(session_info, records, category_key),
        reply_markup=inventory_session_keyboard(records, category_key),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity < 0:
        await update.message.reply_text("Введите число от 0 и выше:")
        return INVENTORY_QUANTITY
    try:
        save_inventory_session_item(
            context.user_data["inventory_session_id"],
            context.user_data["inventory_item_id"],
            quantity,
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await update.message.reply_text(str(error), reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    context.user_data.pop("inventory_item_id", None)
    session_id = context.user_data["inventory_session_id"]
    inventory_session = get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
    records = get_inventory_session_items(session_id)
    category_key = context.user_data.get("inventory_category")
    await update.message.reply_text(
        inventory_session_text(inventory_session, records, category_key),
        reply_markup=inventory_session_keyboard(records, category_key),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_count_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    if not session_id:
        await query.edit_message_text("Откройте пересчет заново.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    records = get_inventory_session_items(session_id)
    counted = sum(1 for record in records if record["counted"])
    if not counted:
        category_key = context.user_data.get("inventory_category")
        await query.edit_message_text(
            "Сначала укажите количество хотя бы одного расходника.",
            reply_markup=inventory_session_keyboard(records, category_key),
        )
        return INVENTORY_ITEM_SELECT
    uncounted = len(records) - counted
    await query.edit_message_text(
        f"Завершить пересчет?\n\nПосчитано: {counted} из {len(records)}.\n"
        f"Непосчитано: {uncounted}.",
        reply_markup=inventory_complete_keyboard(),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_complete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    if not session_id:
        await query.edit_message_text("Откройте пересчет заново.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END

    if query.data == "consinventory:complete:no":
        inventory_session = get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
        records = get_inventory_session_items(session_id)
        category_key = context.user_data.get("inventory_category")
        await query.edit_message_text(
            inventory_session_text(inventory_session, records, category_key),
            reply_markup=inventory_session_keyboard(records, category_key),
        )
        return INVENTORY_ITEM_SELECT

    try:
        result = complete_inventory_session(
            session_id,
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await query.edit_message_text(str(error), reply_markup=consumables_counting_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END

    try:
        topic_status = await send_completed_inventory_pdf_to_topic(context, result)
    except Exception as error:
        logging.exception("Не удалось отправить PDF пересчета расходников в тему")
        topic_status = f"PDF пересчета не отправлен в тему ⚠️\nОшибка: {error}"
    context.user_data.clear()
    await query.edit_message_text(
        "Пересчет отправлен руководителю на проверку ✅\n\n"
        f"Зафиксировано позиций: {len(result['records'])}.\n"
        "Системные остатки пока не изменены.\n"
        f"{topic_status}",
        reply_markup=consumables_counting_keyboard(update),
    )
    return ConversationHandler.END


async def inventory_review_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    inventory_session = get_pending_inventory_session()
    if not inventory_session:
        await query.edit_message_text(
            "Пересчетов, ожидающих проверки, нет.",
            reply_markup=consumables_counting_keyboard(update),
        )
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["inventory_review_session_id"] = inventory_session["session_id"]
    context.user_data["inventory_review_page"] = 0
    records = get_inventory_session_items(inventory_session["session_id"])
    await query.edit_message_text(
        inventory_review_text(inventory_session, records),
        reply_markup=inventory_review_keyboard(records),
    )
    return INVENTORY_REVIEW_SELECT


async def inventory_review_page_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    inventory_session = get_pending_inventory_session()
    if not inventory_session:
        await query.edit_message_text("Пересчет уже обработан.", reply_markup=consumables_counting_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    try:
        requested_page = int(query.data.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        requested_page = 0
    records = get_inventory_session_items(inventory_session["session_id"])
    page, _, _, _, _ = inventory_review_page(records, requested_page)
    context.user_data["inventory_review_session_id"] = inventory_session["session_id"]
    context.user_data["inventory_review_page"] = page
    await query.edit_message_text(
        inventory_review_text(inventory_session, records, page),
        reply_markup=inventory_review_keyboard(records, page),
    )
    return INVENTORY_REVIEW_SELECT


async def inventory_review_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    item_id = int(query.data.replace("consreview:item:", ""))
    context.user_data["inventory_review_item_id"] = item_id
    item = get_consumable_item(item_id)
    await query.edit_message_text(
        f"Введите подтвержденное фактическое количество:\n\n"
        f"{item['name'] if item else 'Расходник'}",
        reply_markup=inventory_review_quantity_keyboard(),
    )
    return INVENTORY_REVIEW_QUANTITY


async def inventory_review_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_manager(current_employee_or_none(update)):
        await update.message.reply_text("Недостаточно прав.")
        return ConversationHandler.END
    quantity = safe_float(update.message.text)
    if quantity < 0:
        await update.message.reply_text("Введите число от 0 и выше:")
        return INVENTORY_REVIEW_QUANTITY
    try:
        update_inventory_review_item(
            context.user_data["inventory_review_session_id"],
            context.user_data["inventory_review_item_id"],
            quantity,
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await update.message.reply_text(str(error), reply_markup=consumables_counting_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.pop("inventory_review_item_id", None)
    inventory_session = get_pending_inventory_session()
    if not inventory_session:
        await update.message.reply_text(
            "Пересчет уже обработан.",
            reply_markup=consumables_counting_keyboard(update),
        )
        context.user_data.clear()
        return ConversationHandler.END
    records = get_inventory_session_items(inventory_session["session_id"])
    page = context.user_data.get("inventory_review_page", 0)
    await update.message.reply_text(
        inventory_review_text(inventory_session, records, page),
        reply_markup=inventory_review_keyboard(records, page),
    )
    return INVENTORY_REVIEW_SELECT


async def inventory_review_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    inventory_session = get_pending_inventory_session()
    if not inventory_session:
        await query.edit_message_text("Пересчет уже обработан.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    records = get_inventory_session_items(inventory_session["session_id"])
    page = context.user_data.get("inventory_review_page", 0)
    await query.edit_message_text(
        inventory_review_text(inventory_session, records, page),
        reply_markup=inventory_review_keyboard(records, page),
    )
    return INVENTORY_REVIEW_SELECT


async def inventory_review_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    try:
        result = apply_inventory_session(
            context.user_data.get("inventory_review_session_id"),
            update.effective_user.id,
            current_employee_name(update),
        )
    except RuntimeError as error:
        await query.edit_message_text(str(error), reply_markup=consumables_counting_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text(
        "Пересчет применен ✅\n\n"
        f"Зафиксировано позиций: {len(result['records'])}.\n"
        f"Создано движений: {len(result['movements'])}.",
        reply_markup=consumables_counting_keyboard(update),
    )
    return ConversationHandler.END


async def inventory_review_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    returned = return_inventory_session_to_draft(
        context.user_data.get("inventory_review_session_id")
    )
    context.user_data.clear()
    await query.edit_message_text(
        "Пересчет возвращен сотрудникам на исправление."
        if returned
        else "Пересчет уже обработан.",
        reply_markup=consumables_counting_keyboard(update),
    )
    return ConversationHandler.END


async def send_completed_inventory_pdf_to_topic(context: ContextTypes.DEFAULT_TYPE, result):
    if not GROUP_CHAT_ID:
        return "GROUP_CHAT_ID не настроен, PDF пересчета в тему не отправлен."

    inventory_session = result["session"]
    session_id = inventory_session["session_id"]
    filename = f"consumables_inventory_{session_id}.pdf"
    report_path = create_inventory_count_pdf(
        result["records"],
        counted_by_name=inventory_session.get("completed_by_name", ""),
        filename=filename,
    )
    kwargs = {"chat_id": int(GROUP_CHAT_ID)}
    if CONSUMABLES_TOPIC_ID:
        kwargs["message_thread_id"] = int(CONSUMABLES_TOPIC_ID)
    try:
        with report_path.open("rb") as document:
            await context.bot.send_document(
                **kwargs,
                document=document,
                filename=filename,
                caption="Пересчет расходников — ожидает проверки руководителем",
            )
    finally:
        Path(report_path).unlink(missing_ok=True)
    return "PDF пересчета отправлен в тему расходников ✅"


async def inventory_exit_requested(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("inventory_session_id"):
        return await consumables_cancel(update, context)
    await query.edit_message_text(
        "Пересчет сохранен как черновик. Что сделать дальше?",
        reply_markup=inventory_exit_keyboard(),
    )
    return INVENTORY_EXIT_CONFIRM


async def inventory_exit_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = context.user_data.get("inventory_session_id")
    action = query.data.rsplit(":", 1)[-1]

    if action == "save":
        context.user_data.clear()
        await query.edit_message_text(
            "Черновик пересчета сохранен ✅",
            reply_markup=consumables_counting_keyboard(update),
        )
        return ConversationHandler.END
    if action == "discard":
        await query.edit_message_text(
            "Удалить черновик пересчета без возможности восстановления?",
            reply_markup=inventory_discard_confirm_keyboard(),
        )
        return INVENTORY_EXIT_CONFIRM

    inventory_session = get_inventory_session(session_id, update.effective_user.id, current_employee_name(update))
    if not inventory_session or inventory_session["status"] != "draft":
        await query.edit_message_text("Черновик не найден.", reply_markup=consumables_counting_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    records = get_inventory_session_items(session_id)
    category_key = context.user_data.get("inventory_category")
    await query.edit_message_text(
        inventory_session_text(inventory_session, records, category_key),
        reply_markup=inventory_session_keyboard(records, category_key),
    )
    return INVENTORY_ITEM_SELECT


async def inventory_discard_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    discarded = discard_inventory_session(context.user_data.get("inventory_session_id"))
    context.user_data.clear()
    text = "Черновик пересчета удален." if discarded else "Черновик уже завершен или не найден."
    await query.edit_message_text(text, reply_markup=consumables_counting_keyboard(update))
    return ConversationHandler.END


async def inventory_pdf_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    sessions = list_completed_inventory_sessions(limit=20)
    if not sessions:
        await query.edit_message_text(
            "Завершенных пересчетов пока нет.",
            reply_markup=consumables_counting_keyboard(update),
        )
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите завершенный пересчет для выгрузки PDF:",
        reply_markup=inventory_pdf_keyboard(sessions),
    )
    return INVENTORY_PDF_SELECT


async def inventory_pdf_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session_id = query.data.replace("consinventorypdf:", "")
    records = get_completed_inventory_session_records(session_id)
    if not records:
        await query.edit_message_text(
            "В выбранном пересчете нет позиций.",
            reply_markup=consumables_counting_keyboard(update),
        )
        return ConversationHandler.END
    session_info = get_inventory_session(session_id)
    filename = f"consumables_inventory_{session_id}.pdf"
    report_path = create_inventory_count_pdf(
        records,
        counted_by_name=(session_info or {}).get("completed_by_name", ""),
        filename=filename,
    )
    try:
        with report_path.open("rb") as document:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=document,
                filename=filename,
                caption="Пересчет расходников",
            )
    finally:
        Path(report_path).unlink(missing_ok=True)
    await query.edit_message_text(
        "PDF пересчета отправлен ✅",
        reply_markup=consumables_counting_keyboard(update),
    )
    return ConversationHandler.END


async def inventory_compare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    batches = get_recent_inventory_batches(limit=1)
    if not batches:
        await query.edit_message_text("Пересчетов для сравнения пока нет.", reply_markup=consumables_counting_keyboard(update))
        return ConversationHandler.END
    batch_id = batches[0]["batch_id"]
    records = get_inventory_batch_comparison(batch_id)
    context.user_data["comparison_batch_id"] = batch_id
    context.user_data["comparison_records"] = records
    context.user_data["comparison_selected_ids"] = set()
    await query.edit_message_text(comparison_text(records, set()), reply_markup=comparison_keyboard(records, set()))
    return INVENTORY_COMPARE_SELECT


async def inventory_compare_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.replace("conscompare:toggle:", ""))
    selected_ids = context.user_data.setdefault("comparison_selected_ids", set())
    if item_id in selected_ids:
        selected_ids.remove(item_id)
    else:
        selected_ids.add(item_id)
    records = context.user_data.get("comparison_records", [])
    await query.edit_message_text(comparison_text(records, selected_ids), reply_markup=comparison_keyboard(records, selected_ids))
    return INVENTORY_COMPARE_SELECT


async def inventory_compare_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_ids = context.user_data.get("comparison_selected_ids", set())
    if not selected_ids:
        records = context.user_data.get("comparison_records", [])
        await query.edit_message_text(comparison_text(records, selected_ids), reply_markup=comparison_keyboard(records, selected_ids))
        return INVENTORY_COMPARE_SELECT
    movements = apply_inventory_batch_counts(
        context.user_data["comparison_batch_id"],
        selected_ids,
        created_by_user_id=update.effective_user.id,
        created_by_name=current_employee_name(update),
    )
    context.user_data.clear()
    lines = ["Сравнение применено ✅", "", f"Обновлено позиций: {len(movements)}"]
    for movement in movements:
        sign = "+" if float(movement["quantity_delta"] or 0) > 0 else ""
        lines.append(f"{movement['item_name']}: {sign}{format_quantity(movement['quantity_delta'])}")
    await query.edit_message_text("\n".join(lines), reply_markup=consumables_counting_keyboard(update))
    return ConversationHandler.END


async def add_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    await query.edit_message_text("Введите название расходника для учета:", reply_markup=consumables_back_keyboard())
    return ITEM_NAME


async def item_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Введите название расходника:")
        return ITEM_NAME
    context.user_data["item_name"] = name
    await update.message.reply_text("Введите единицу измерения. Например: шт", reply_markup=consumables_back_keyboard())
    return ITEM_UNIT


async def item_unit_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    unit = update.message.text.strip() or "шт"
    item = upsert_consumable_item(context.user_data["item_name"], unit)
    context.user_data.clear()
    await update.message.reply_text(
        f"Расходник добавлен в учет ✅\n\n{item['name']}, единица: {item['unit']}",
        reply_markup=build_consumables_counting_menu_keyboard(manager=True),
    )
    return ConversationHandler.END


async def delete_item_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text("Активных расходников нет.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    await query.edit_message_text(
        "Выберите неактуальный расходник, который нужно убрать из учета:",
        reply_markup=consumable_items_keyboard(items, "consdeleteitem"),
    )
    return ITEM_DELETE_SELECT


async def delete_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    try:
        item_id = int(query.data.replace("consdeleteitem:", ""))
    except (TypeError, ValueError):
        await query.edit_message_text("Расходник не найден.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    item = get_consumable_item(item_id)
    if not item or not item["is_active"]:
        await query.edit_message_text("Расходник не найден или уже удален.", reply_markup=consumables_main_keyboard(update))
        return ConversationHandler.END
    context.user_data["delete_consumable_item_id"] = item_id
    await query.edit_message_text(
        "Удалить расходник из активного учета?\n\n"
        f"{item['name']}\n"
        f"Текущий остаток: {format_quantity(item['current_quantity'])} {item['unit']}\n\n"
        "Он исчезнет из приемки, пересчетов и норм на товары. История движений сохранится.",
        reply_markup=consumable_item_delete_confirm_keyboard(),
    )
    return ITEM_DELETE_CONFIRM


async def delete_item_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    try:
        item = deactivate_consumable_item(context.user_data.get("delete_consumable_item_id"))
    except (RuntimeError, TypeError, ValueError) as error:
        await query.edit_message_text(str(error), reply_markup=consumables_main_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text(
        "Расходник удален из активного учета ✅\n\n"
        f"{item['name']}\n"
        "История остатков и движений сохранена.",
        reply_markup=consumables_main_keyboard(update),
    )
    return ConversationHandler.END


async def add_stock_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text("Сначала добавьте расходник в учет.", reply_markup=build_consumables_counting_menu_keyboard(manager=True))
        return ConversationHandler.END
    await query.edit_message_text("Выберите расходник для пополнения:", reply_markup=consumable_items_keyboard(items, "consstockitem"))
    return STOCK_ITEM_SELECT


async def stock_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["stock_item_id"] = int(query.data.replace("consstockitem:", ""))
    await query.edit_message_text("Введите количество, которое нужно добавить к остатку:", reply_markup=consumables_back_keyboard())
    return STOCK_QUANTITY


async def stock_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity <= 0:
        await update.message.reply_text("Введите число больше 0:")
        return STOCK_QUANTITY
    employee_name = current_employee_name(update)
    movement = add_consumable_movement(
        item_id=context.user_data["stock_item_id"],
        quantity_delta=quantity,
        source="manual_stock",
        comment="Ручное пополнение остатка",
        created_by_user_id=update.effective_user.id,
        created_by_name=employee_name,
    )
    context.user_data.clear()
    await update.message.reply_text(
        f"Остаток пополнен ✅\n\n{movement['item_name']}: +{format_quantity(quantity)}",
        reply_markup=build_consumables_counting_menu_keyboard(manager=True),
    )
    return ConversationHandler.END


async def set_rule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "counting")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    await query.edit_message_text("Выберите группу товара:", reply_markup=consumable_category_keyboard())
    return RULE_CATEGORY


async def rule_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = query.data.replace("conscat:", "")
    if category_id not in CATEGORIES:
        await query.edit_message_text("Группа не найдена.", reply_markup=build_consumables_counting_menu_keyboard(manager=True))
        return ConversationHandler.END
    context.user_data["rule_category_id"] = category_id
    await query.edit_message_text("Выберите модель:", reply_markup=consumable_models_keyboard(category_id))
    return RULE_MODEL


async def rule_model_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    model_id = query.data.replace("consmodel:", "")
    category_id = context.user_data.get("rule_category_id")
    if not category_id or model_id not in CATEGORIES[category_id]["models"]:
        await query.edit_message_text("Модель не найдена.", reply_markup=build_consumables_counting_menu_keyboard(manager=True))
        return ConversationHandler.END
    context.user_data["rule_model_id"] = model_id
    variants = CATEGORIES[category_id]["models"][model_id]["variants"]
    if len(variants) == 1:
        product_id = next(iter(variants.values()))["id"]
        return await finish_rule_product_selection(query, context, product_id)
    await query.edit_message_text("Выберите цвет / вариант:", reply_markup=consumable_products_keyboard(category_id, model_id))
    return RULE_PRODUCT


async def rule_product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = query.data.replace("consprod:", "")
    return await finish_rule_product_selection(query, context, product_id)


async def finish_rule_product_selection(query, context, product_id):
    category_id = context.user_data.get("rule_category_id")
    product_name = CATEGORIES[category_id]["products"].get(product_id, "")
    if not product_name:
        await query.edit_message_text("Товар не найден.", reply_markup=build_consumables_counting_menu_keyboard(manager=True))
        return ConversationHandler.END
    context.user_data["rule_product_id"] = product_id
    context.user_data["rule_product_name"] = product_name
    items = get_consumable_items(active_only=True)
    if not items:
        await query.edit_message_text("Сначала добавьте расходник в учет.", reply_markup=build_consumables_counting_menu_keyboard(manager=True))
        return ConversationHandler.END
    await query.edit_message_text(
        format_rules_text(product_name, get_product_consumable_rules(product_id))
        + "\n\nВыберите расходник для настройки нормы:",
        reply_markup=consumable_items_keyboard(items, "consruleitem"),
    )
    return RULE_ITEM_SELECT


async def rule_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["rule_item_id"] = int(query.data.replace("consruleitem:", ""))
    await query.edit_message_text("Введите норму расходника на 1 упакованную единицу товара:", reply_markup=consumables_back_keyboard())
    return RULE_QUANTITY


async def rule_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity <= 0:
        await update.message.reply_text("Введите число больше 0:")
        return RULE_QUANTITY
    rule = set_product_consumable_rule(
        product_id=context.user_data["rule_product_id"],
        product_name=context.user_data["rule_product_name"],
        item_id=context.user_data["rule_item_id"],
        quantity_per_unit=quantity,
    )
    await update.message.reply_text(
        "Норма сохранена ✅\n\n"
        f"{rule['product_name']}\n"
        f"{rule['item_name']}: {format_quantity(rule['quantity_per_unit'])} {rule['unit']} на 1 шт.",
        reply_markup=build_consumables_counting_menu_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def accept_supply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    supplies = get_pending_supplies()
    if not supplies:
        await query.edit_message_text(
            "Нет поставок, ожидающих приемки.",
            reply_markup=consumables_supplies_keyboard(update),
        )
        return ConversationHandler.END

    await query.edit_message_text(
        supplies_list_text("Выберите поставку:", supplies),
        reply_markup=supplies_keyboard(supplies, "conssup"),
    )
    return ACCEPT_SUPPLY


async def accept_supply_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supply_id = query.data.replace("conssup:", "")
    supply = get_supply(supply_id)
    if not supply or supply["status"] != "pending":
        supplies = get_pending_supplies()
        await query.edit_message_text(
            supplies_list_text("Поставка не найдена или уже принята. Выберите заново:", supplies),
            reply_markup=supplies_keyboard(supplies, "conssup"),
        )
        return ACCEPT_SUPPLY

    context.user_data["supply_id"] = supply["id"]
    await query.edit_message_text(
        "Отправьте фото разложенных расходников:",
        reply_markup=consumables_back_keyboard("accept_supply"),
    )
    return ACCEPT_LAYOUT_PHOTO


async def supply_item_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = int(query.data.replace("conssupplyitem:", ""))
    item = get_consumable_item(item_id)
    if not item or not item.get("is_active"):
        await query.edit_message_text("Расходник не найден.", reply_markup=supply_items_keyboard(context))
        return SUPPLY_ITEM_SELECT
    context.user_data["supply_item_id"] = item["item_id"]
    await query.edit_message_text(
        f"Введите количество:\n\n{item['name']}\nЕдиница: {item['unit']}",
        reply_markup=consumables_back_keyboard("supply_items"),
    )
    return SUPPLY_ITEM_QUANTITY


async def supply_item_quantity_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quantity = safe_float(update.message.text)
    if quantity <= 0:
        await update.message.reply_text("Введите число больше 0:")
        return SUPPLY_ITEM_QUANTITY
    item = get_consumable_item(context.user_data.get("supply_item_id"))
    if not item:
        await update.message.reply_text("Расходник не найден.", reply_markup=supply_items_keyboard(context))
        return SUPPLY_ITEM_SELECT
    context.user_data.setdefault("supply_items", {})[str(item["item_id"])] = {
        "item_id": item["item_id"],
        "item_name": item["name"],
        "unit": item["unit"],
        "quantity": quantity,
    }
    context.user_data.pop("supply_item_id", None)
    await update.message.reply_text(supply_items_text(context), reply_markup=supply_items_keyboard(context))
    return SUPPLY_ITEM_SELECT


async def supply_items_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("supply_items"):
        await query.edit_message_text(supply_items_text(context), reply_markup=supply_items_keyboard(context))
        return SUPPLY_ITEM_SELECT
    await query.edit_message_text("Введите наименование счета:", reply_markup=consumables_back_keyboard("supply_items"))
    return SUPPLY_NAME


async def layout_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Нужно отправить фото разложенных расходников:")
        return ACCEPT_LAYOUT_PHOTO

    context.user_data["layout_photo_file_id"] = update.message.photo[-1].file_id
    supply = get_supply(context.user_data.get("supply_id"))
    supplier = get_supplier(supply.get("organization")) if supply else None
    delivery = (supplier or {}).get("closing_documents_delivery", SUPPLIER_DOCUMENTS_PAPER)
    context.user_data["supplier_documents_delivery"] = delivery
    if delivery == SUPPLIER_DOCUMENTS_EDO:
        context.user_data["closing_document_file_id"] = ""
        context.user_data["closing_document_kind"] = "none"
        return await finish_acceptance(update, context)

    await update.message.reply_text(
        "Отправьте скан-копию закрывающего документа или нажмите «Документа нет».",
        reply_markup=document_keyboard(),
    )
    return ACCEPT_DOCUMENT


async def layout_photo_wrong_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Нужно отправить именно фото разложенных расходников:",
        reply_markup=consumables_back_keyboard(),
    )
    return ACCEPT_LAYOUT_PHOTO


async def acceptance_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data["closing_document_file_id"] = update.message.photo[-1].file_id
        context.user_data["closing_document_kind"] = "photo"
    elif update.message.document:
        context.user_data["closing_document_file_id"] = update.message.document.file_id
        context.user_data["closing_document_kind"] = "document"
    else:
        await update.message.reply_text(
            "Отправьте фото/файл закрывающего документа или нажмите «Документа нет».",
            reply_markup=document_keyboard(),
        )
        return ACCEPT_DOCUMENT

    return await finish_acceptance(update, context)


async def acceptance_document_wrong_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправьте фото/файл закрывающего документа или нажмите «Документа нет».",
        reply_markup=document_keyboard(),
    )
    return ACCEPT_DOCUMENT


async def acceptance_document_missing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["closing_document_file_id"] = ""
    context.user_data["closing_document_kind"] = "none"
    return await finish_acceptance(update, context)


async def send_acceptance_to_topic(context: ContextTypes.DEFAULT_TYPE, supply, accepted_by):
    if not GROUP_CHAT_ID:
        return [], "GROUP_CHAT_ID не настроен, сообщение в тему не отправлено."

    message_thread_id = int(CONSUMABLES_TOPIC_ID) if CONSUMABLES_TOPIC_ID else None
    common_kwargs = {"chat_id": int(GROUP_CHAT_ID)}
    if message_thread_id is not None:
        common_kwargs["message_thread_id"] = message_thread_id

    closing_file_id = context.user_data.get("closing_document_file_id", "")
    delivery = context.user_data.get("supplier_documents_delivery", SUPPLIER_DOCUMENTS_PAPER)
    if delivery == SUPPLIER_DOCUMENTS_EDO:
        document_status = "по ЭДО"
    else:
        document_status = "есть" if closing_file_id else "документа нет"

    first_message = await context.bot.send_photo(
        **common_kwargs,
        photo=context.user_data["layout_photo_file_id"],
        caption=format_acceptance_text(supply, accepted_by, document_status),
    )
    return [first_message.message_id], "Отчет о приемке отправлен в тему расходников ✅"


def format_document_workflow_amount(value):
    formatted = f"{float(value or 0):,.2f}"
    return formatted.replace(",", " ").replace(".", ",")


def closing_document_caption_text(supply, edo=False):
    lines = []
    if edo:
        lines.append("Закрывающий документ отправлен по ЭДО")
    lines.extend(
        [
            f"2 - {supply['consumable_name']}",
            f"3 - {supply['organization']}",
            f"4 - {format_document_workflow_amount(supply['amount'])}",
        ]
    )
    return "\n".join(lines)


async def send_closing_document_to_workflow(context: ContextTypes.DEFAULT_TYPE, supply):
    if not DOCUMENT_WORKFLOW_CHAT_ID:
        return [], "Закрывающий документ не отправлен: DOCUMENT_WORKFLOW_CHAT_ID не настроен."
    if not ACTS_CLOSING_DOCUMENTS_TOPIC_ID:
        return [], "Закрывающий документ не отправлен: ACTS_CLOSING_DOCUMENTS_TOPIC_ID не настроен."

    kwargs = {
        "chat_id": int(DOCUMENT_WORKFLOW_CHAT_ID),
        "message_thread_id": int(ACTS_CLOSING_DOCUMENTS_TOPIC_ID),
    }
    delivery = context.user_data.get("supplier_documents_delivery", SUPPLIER_DOCUMENTS_PAPER)
    if delivery == SUPPLIER_DOCUMENTS_EDO:
        message = await context.bot.send_message(
            **kwargs,
            text=closing_document_caption_text(supply, edo=True),
        )
        return [message.message_id], "Сообщение об ЭДО отправлено в документооборот ✅"

    file_id = context.user_data.get("closing_document_file_id", "")
    kind = context.user_data.get("closing_document_kind", "none")
    if not file_id:
        return [], "Закрывающий документ не прикреплен."

    caption = closing_document_caption_text(supply)
    if kind == "photo":
        message = await context.bot.send_photo(**kwargs, photo=file_id, caption=caption)
    else:
        message = await context.bot.send_document(**kwargs, document=file_id, caption=caption)
    return [message.message_id], "Закрывающий документ отправлен в документооборот ✅"


def invoice_caption_text(supply):
    items_text = "; ".join(
        f"{escape(str(item['item_name']))} {format_quantity(item['quantity'])} {escape(str(item.get('unit') or 'шт'))}"
        for item in supply.get("supply_items", [])
    )
    return "\n".join(
        [
            f"1 - {items_text}.",
            f"2 - {escape(str(supply['consumable_name']))}",
            f"<b>3 - {escape(str(supply['organization']))}</b>",
            f"<b>4 - {format_quantity(supply['amount'])}</b>",
        ]
    )


async def send_invoice_to_document_workflow_topic(context: ContextTypes.DEFAULT_TYPE, supply, invoice_file_id, invoice_kind):
    if not DOCUMENT_WORKFLOW_CHAT_ID:
        return "Счет не отправлен в документооборот: DOCUMENT_WORKFLOW_CHAT_ID не настроен."

    kwargs = {"chat_id": int(DOCUMENT_WORKFLOW_CHAT_ID)}
    if WAREHOUSE_INVOICES_TOPIC_ID:
        kwargs["message_thread_id"] = int(WAREHOUSE_INVOICES_TOPIC_ID)

    caption = invoice_caption_text(supply)
    if invoice_kind == "photo":
        await context.bot.send_photo(
            **kwargs,
            photo=invoice_file_id,
            caption=caption,
            parse_mode="HTML",
        )
    else:
        await context.bot.send_document(
            **kwargs,
            document=invoice_file_id,
            caption=caption,
            parse_mode="HTML",
        )

    return "Счет отправлен в документооборот ✅"


async def delete_topic_messages(context: ContextTypes.DEFAULT_TYPE, supply):
    if GROUP_CHAT_ID:
        for message_id in split_message_ids(supply.get("topic_message_ids")):
            try:
                await context.bot.delete_message(chat_id=int(GROUP_CHAT_ID), message_id=message_id)
            except Exception:
                logging.exception("Не удалось удалить сообщение приемки расходника из темы")

    if DOCUMENT_WORKFLOW_CHAT_ID:
        for message_id in split_message_ids(supply.get("document_workflow_message_ids")):
            try:
                await context.bot.delete_message(
                    chat_id=int(DOCUMENT_WORKFLOW_CHAT_ID),
                    message_id=message_id,
                )
            except Exception:
                logging.exception("Не удалось удалить закрывающий документ из документооборота")


async def finish_acceptance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supply = get_supply(context.user_data.get("supply_id"))
    mode = context.user_data.get("acceptance_mode", "create")
    expected_status = "accepted" if mode == "edit" else "pending"
    if not supply or supply["status"] != expected_status:
        text = "Поставка не найдена или уже принята."
        if mode == "edit":
            text = "Приемка не найдена или уже удалена."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        context.user_data.clear()
        return ConversationHandler.END

    accepted_by = current_employee_name(update)
    if "supplier_documents_delivery" not in context.user_data:
        supplier = get_supplier(supply.get("organization"))
        context.user_data["supplier_documents_delivery"] = (supplier or {}).get(
            "closing_documents_delivery",
            SUPPLIER_DOCUMENTS_PAPER,
        )

    if mode == "edit":
        await delete_topic_messages(context, supply)

    try:
        message_ids, topic_status = await send_acceptance_to_topic(context, supply, accepted_by)
    except Exception as error:
        logging.exception("Не удалось отправить приемку расходника в тему")
        message_ids = []
        topic_status = f"Приемка сохранена, но не отправлена в тему ⚠️\nОшибка: {error}"

    try:
        workflow_message_ids, document_status = await send_closing_document_to_workflow(context, supply)
    except Exception as error:
        logging.exception("Не удалось отправить закрывающий документ в документооборот")
        workflow_message_ids = []
        document_status = f"Закрывающий документ не отправлен ⚠️\nОшибка: {error}"

    if mode == "edit":
        update_acceptance(
            supply_id=supply["id"],
            accepted_by_user_id=update.effective_user.id,
            accepted_by_name=accepted_by,
            layout_photo_file_id=context.user_data["layout_photo_file_id"],
            closing_document_file_id=context.user_data.get("closing_document_file_id", ""),
            closing_document_kind=context.user_data.get("closing_document_kind", "none"),
            topic_message_ids=message_ids,
            document_workflow_message_ids=workflow_message_ids,
        )
    else:
        supply = mark_supply_accepted(
            supply_id=supply["id"],
            accepted_by_user_id=update.effective_user.id,
            accepted_by_name=accepted_by,
            layout_photo_file_id=context.user_data["layout_photo_file_id"],
            closing_document_file_id=context.user_data.get("closing_document_file_id", ""),
            closing_document_kind=context.user_data.get("closing_document_kind", "none"),
            topic_message_ids=message_ids,
            document_workflow_message_ids=workflow_message_ids,
        )

    result_title = "Приемка расходника изменена ✅" if mode == "edit" else "Приемка расходника завершена ✅"
    text = result_title + "\n\n" + format_supply_line(supply) + f"\n\n{topic_status}\n{document_status}"
    if mode != "edit" and supply.get("supply_items"):
        stock_lines = [
            f"{item['item_name']}: +{format_quantity(item['quantity'])} {item.get('unit') or 'шт'}"
            for item in supply["supply_items"]
        ]
        text += "\n\nОстатки пополнены:\n" + "\n".join(stock_lines)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=consumables_supplies_keyboard(update),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=consumables_supplies_keyboard(update),
        )

    context.user_data.clear()
    return ConversationHandler.END


async def edit_supply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    employee = current_employee_or_none(update)
    if not is_manager(employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    supplies = get_supplies()
    if not supplies:
        await query.edit_message_text("Поставок пока нет.", reply_markup=build_consumables_supplies_menu_keyboard(manager=True))
        return ConversationHandler.END

    context.user_data["supply_manage_action"] = "edit"
    await query.edit_message_text(
        supplies_list_text("Выберите поставку для изменения:", supplies),
        reply_markup=supplies_keyboard(supplies, "consmanage"),
    )
    return SUPPLY_MANAGE_SELECT


async def delete_supply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    employee = current_employee_or_none(update)
    if not is_manager(employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    supplies = get_supplies()
    if not supplies:
        await query.edit_message_text("Поставок пока нет.", reply_markup=build_consumables_supplies_menu_keyboard(manager=True))
        return ConversationHandler.END

    context.user_data["supply_manage_action"] = "delete"
    await query.edit_message_text(
        supplies_list_text("Выберите поставку для удаления:", supplies),
        reply_markup=supplies_keyboard(supplies, "consmanage"),
    )
    return SUPPLY_MANAGE_SELECT


async def supply_manage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supply_id = query.data.replace("consmanage:", "")
    supply = get_supply(supply_id)
    if not supply:
        await query.edit_message_text("Поставка не найдена.", reply_markup=build_consumables_supplies_menu_keyboard(manager=True))
        return ConversationHandler.END

    context.user_data["supply_id"] = supply["id"]

    if context.user_data.get("supply_manage_action") == "delete":
        await query.edit_message_text(
            "Удалить поставку?\n\n" + format_supply_line(supply),
            reply_markup=confirm_keyboard("conssupplydelete:yes"),
        )
        return SUPPLY_DELETE_CONFIRM

    await query.edit_message_text(
        "Что изменить?\n\n" + format_supply_line(supply),
        reply_markup=supply_edit_field_keyboard(),
    )
    return SUPPLY_EDIT_FIELD


async def supply_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    field = query.data.replace("consfield:", "")
    context.user_data["supply_edit_field"] = field

    prompts = {
        "name": "Введите новое название счета:",
        "organization": "Введите новое название контрагента:",
        "amount": "Введите новую сумму к оплате:",
    }
    await query.edit_message_text(prompts.get(field, "Введите новое значение:"), reply_markup=consumables_back_keyboard())
    return SUPPLY_EDIT_VALUE


async def supply_edit_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    field = context.user_data.get("supply_edit_field")
    if not value:
        await update.message.reply_text("Значение не должно быть пустым. Введите еще раз:")
        return SUPPLY_EDIT_VALUE

    kwargs = {}
    if field == "name":
        kwargs["consumable_name"] = value
    elif field == "organization":
        kwargs["organization"] = value
    elif field == "amount":
        amount = parse_positive_amount(value)
        if amount is None:
            await update.message.reply_text("Введите сумму числом больше 0:")
            return SUPPLY_EDIT_VALUE
        kwargs["amount"] = amount
    else:
        await update.message.reply_text("Поле не найдено.", reply_markup=build_consumables_supplies_menu_keyboard(manager=True))
        context.user_data.clear()
        return ConversationHandler.END

    supply = update_supply(context.user_data["supply_id"], **kwargs)

    topic_status = ""
    if supply["status"] == "accepted" and supply.get("layout_photo_file_id"):
        await delete_topic_messages(context, supply)
        context.user_data["layout_photo_file_id"] = supply["layout_photo_file_id"]
        context.user_data["closing_document_file_id"] = supply.get("closing_document_file_id", "")
        context.user_data["closing_document_kind"] = supply.get("closing_document_kind", "none")
        supplier = get_supplier(supply.get("organization"))
        context.user_data["supplier_documents_delivery"] = (supplier or {}).get(
            "closing_documents_delivery",
            SUPPLIER_DOCUMENTS_PAPER,
        )
        try:
            message_ids, topic_status = await send_acceptance_to_topic(context, supply, supply.get("accepted_by_name") or "-")
        except Exception as error:
            logging.exception("Не удалось обновить сообщение приемки после изменения поставки")
            message_ids = []
            topic_status = f"Отчет о приемке не обновлен ⚠️\nОшибка: {error}"
        try:
            workflow_message_ids, document_status = await send_closing_document_to_workflow(context, supply)
        except Exception as error:
            logging.exception("Не удалось обновить закрывающий документ после изменения поставки")
            workflow_message_ids = []
            document_status = f"Закрывающий документ не обновлен ⚠️\nОшибка: {error}"
        update_acceptance(
            supply_id=supply["id"],
            accepted_by_user_id=supply.get("accepted_by_user_id", ""),
            accepted_by_name=supply.get("accepted_by_name", ""),
            layout_photo_file_id=supply["layout_photo_file_id"],
            closing_document_file_id=supply.get("closing_document_file_id", ""),
            closing_document_kind=supply.get("closing_document_kind", "none"),
            topic_message_ids=message_ids,
            document_workflow_message_ids=workflow_message_ids,
        )
        topic_status = f"\n\n{topic_status}\n{document_status}"

    await update.message.reply_text(
        "Поставка изменена ✅\n\n" + format_supply_line(supply) + topic_status,
        reply_markup=build_consumables_supplies_menu_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def supply_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supply = get_supply(context.user_data.get("supply_id"))
    if not supply:
        await query.edit_message_text("Поставка не найдена.", reply_markup=build_consumables_supplies_menu_keyboard(manager=True))
        context.user_data.clear()
        return ConversationHandler.END

    await delete_topic_messages(context, supply)
    deleted = delete_supply(supply["id"])
    await query.edit_message_text(
        "Поставка удалена ✅\n\n" + format_supply_line(deleted),
        reply_markup=build_consumables_supplies_menu_keyboard(manager=True),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def edit_acceptance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    supplies = get_accepted_supplies()
    if not supplies:
        await query.edit_message_text(
            "Принятых поставок пока нет.",
            reply_markup=consumables_supplies_keyboard(update),
        )
        return ConversationHandler.END

    context.user_data["acceptance_manage_action"] = "edit"
    await query.edit_message_text(
        supplies_list_text("Выберите приемку для изменения:", supplies),
        reply_markup=supplies_keyboard(supplies, "consacc"),
    )
    return ACCEPTANCE_MANAGE_SELECT


async def delete_acceptance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "supplies")

    supplies = get_accepted_supplies()
    if not supplies:
        await query.edit_message_text(
            "Принятых поставок пока нет.",
            reply_markup=consumables_supplies_keyboard(update),
        )
        return ConversationHandler.END

    context.user_data["acceptance_manage_action"] = "delete"
    await query.edit_message_text(
        supplies_list_text("Выберите приемку для удаления:", supplies),
        reply_markup=supplies_keyboard(supplies, "consacc"),
    )
    return ACCEPTANCE_MANAGE_SELECT


async def acceptance_manage_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supply = get_supply(query.data.replace("consacc:", ""))
    if not supply or supply["status"] != "accepted":
        await query.edit_message_text("Приемка не найдена.", reply_markup=consumables_supplies_keyboard(update))
        return ConversationHandler.END

    context.user_data["supply_id"] = supply["id"]

    if context.user_data.get("acceptance_manage_action") == "delete":
        await query.edit_message_text(
            "Удалить приемку и вернуть поставку в ожидание приемки?\n\n" + format_supply_line(supply),
            reply_markup=confirm_keyboard("consaccdelete:yes"),
        )
        return ACCEPTANCE_DELETE_CONFIRM

    context.user_data["acceptance_mode"] = "edit"
    await query.edit_message_text(
        "Отправьте новое фото разложенных расходников:",
        reply_markup=consumables_back_keyboard(),
    )
    return ACCEPT_LAYOUT_PHOTO


async def acceptance_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supply = get_supply(context.user_data.get("supply_id"))
    if not supply or supply["status"] != "accepted":
        await query.edit_message_text("Приемка не найдена.", reply_markup=consumables_supplies_keyboard(update))
        context.user_data.clear()
        return ConversationHandler.END

    await delete_topic_messages(context, supply)
    cleared = clear_acceptance(supply["id"])
    await query.edit_message_text(
        "Приемка удалена ✅\nПоставка снова доступна для приемки.\n\n" + format_supply_line(cleared),
        reply_markup=consumables_supplies_keyboard(update),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def add_supplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "suppliers")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    await query.edit_message_text("Введите название поставщика:", reply_markup=consumables_back_keyboard())
    return SUPPLIER_ADD_NAME


async def supplier_add_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Введите название:")
        return SUPPLIER_ADD_NAME
    context.user_data["supplier_name"] = name
    await update.message.reply_text(
        "Как поставщик доставляет закрывающие документы?",
        reply_markup=supplier_documents_delivery_keyboard("conssupplieradddelivery"),
    )
    return SUPPLIER_ADD_DELIVERY


async def supplier_add_delivery_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delivery = query.data.replace("conssupplieradddelivery:", "")
    supplier = create_supplier(context.user_data.get("supplier_name"), delivery)
    context.user_data.clear()
    await query.edit_message_text(
        "Поставщик добавлен ✅\n\n"
        f"{supplier['name']}\n"
        "Закрывающие документы: "
        f"{supplier_documents_delivery_text(supplier['closing_documents_delivery'])}",
        reply_markup=consumables_suppliers_keyboard(),
    )
    return ConversationHandler.END


async def edit_supplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "suppliers")
    if not is_manager(current_employee_or_none(update)):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END
    suppliers = get_supplier_records(active_only=True)
    if not suppliers:
        await query.edit_message_text("Активных поставщиков пока нет.", reply_markup=consumables_suppliers_keyboard())
        return ConversationHandler.END
    context.user_data["supplier_records"] = suppliers
    await query.edit_message_text(
        suppliers_list_text("Выберите поставщика для изменения:", suppliers),
        reply_markup=supplier_records_keyboard(suppliers, "conssupplieredit"),
    )
    return SUPPLIER_EDIT_SELECT


async def supplier_edit_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    supplier_id = int(query.data.replace("conssupplieredit:", ""))
    supplier = next(
        (item for item in context.user_data.get("supplier_records", []) if int(item["id"]) == supplier_id),
        None,
    )
    if not supplier:
        await query.edit_message_text("Поставщик не найден.", reply_markup=consumables_suppliers_keyboard())
        return ConversationHandler.END
    context.user_data["supplier_id"] = supplier_id
    await query.edit_message_text(
        f"Текущее название: {supplier['name']}\n\nВведите новое название:",
        reply_markup=consumables_back_keyboard(),
    )
    return SUPPLIER_EDIT_NAME


async def supplier_edit_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не должно быть пустым. Введите новое название:")
        return SUPPLIER_EDIT_NAME
    context.user_data["supplier_new_name"] = name
    await update.message.reply_text(
        "Как поставщик доставляет закрывающие документы?",
        reply_markup=supplier_documents_delivery_keyboard("conssuppliereditdelivery"),
    )
    return SUPPLIER_EDIT_DELIVERY


async def supplier_edit_delivery_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delivery = query.data.replace("conssuppliereditdelivery:", "")
    try:
        supplier = update_supplier(
            context.user_data.get("supplier_id"),
            name=context.user_data.get("supplier_new_name"),
            closing_documents_delivery=delivery,
        )
    except RuntimeError as error:
        await query.edit_message_text(str(error), reply_markup=consumables_suppliers_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    context.user_data.clear()
    await query.edit_message_text(
        "Поставщик изменен ✅\n\n"
        f"{supplier['name']}\n"
        "Закрывающие документы: "
        f"{supplier_documents_delivery_text(supplier['closing_documents_delivery'])}",
        reply_markup=consumables_suppliers_keyboard(),
    )
    return ConversationHandler.END


async def delete_supplier_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    set_consumables_module(context, "suppliers")

    employee = current_employee_or_none(update)
    if not is_manager(employee):
        await query.edit_message_text("Недостаточно прав.")
        return ConversationHandler.END

    suppliers = get_active_suppliers()
    if not suppliers:
        await query.edit_message_text("Активных поставщиков пока нет.", reply_markup=consumables_suppliers_keyboard())
        return ConversationHandler.END

    context.user_data["suppliers"] = suppliers
    await query.edit_message_text(
        "Выберите поставщика, которого нужно убрать из выбора:",
        reply_markup=suppliers_keyboard(suppliers),
    )
    return SUPPLIER_DELETE_SELECT


async def supplier_delete_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.replace("conssupplier:", ""))
        supplier = context.user_data.get("suppliers", [])[index]
    except (ValueError, IndexError):
        await query.edit_message_text("Поставщик не найден.", reply_markup=consumables_suppliers_keyboard())
        return ConversationHandler.END

    context.user_data["supplier_name"] = supplier
    await query.edit_message_text(
        f"Удалить поставщика из выбора?\n\n{supplier}\n\nИстория поставок сохранится.",
        reply_markup=confirm_keyboard("conssupplierdelete:yes"),
    )
    return SUPPLIER_DELETE_CONFIRM


async def supplier_delete_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    supplier = deactivate_supplier(context.user_data.get("supplier_name"))
    await query.edit_message_text(
        f"Поставщик удален из выбора ✅\n\n{supplier}",
        reply_markup=consumables_suppliers_keyboard(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def consumables_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target = query.data.replace("consback:", "", 1)

    if target == "receipt_items":
        await query.edit_message_text("Выберите принятый расходник:", reply_markup=consumable_items_keyboard(get_consumable_items(active_only=True), "consreceiptitem"))
        return RECEIPT_ITEM_SELECT
    if target == "receipt_quantity":
        item = get_consumable_item(context.user_data.get("receipt_item_id"))
        await query.edit_message_text(f"Введите принятое количество ({item['unit'] if item else 'шт'}):", reply_markup=consumables_back_keyboard("receipt_items"))
        return RECEIPT_QUANTITY
    if target == "receipt_layout":
        await query.edit_message_text("Отправьте фото принятого расходника, разложенного на складе.", reply_markup=receipt_layout_photos_keyboard(bool(context.user_data.get("receipt_layout_photo_file_ids"))))
        return RECEIPT_LAYOUT_PHOTOS
    if target == "receipt_document_kind":
        await query.edit_message_text("Выберите тип закрывающего документа:", reply_markup=receipt_document_kind_keyboard())
        return RECEIPT_DOCUMENT_KIND
    if target == "receipt_document_files":
        await query.edit_message_text("Отправьте закрывающий документ. Можно добавить несколько файлов или фото.", reply_markup=receipt_document_files_keyboard(bool(context.user_data.get("receipt_document_file_ids"))))
        return RECEIPT_DOCUMENT_FILES

    if target == "supply_items":
        context.user_data.pop("supply_item_id", None)
        await query.edit_message_text(supply_items_text(context), reply_markup=supply_items_keyboard(context))
        return SUPPLY_ITEM_SELECT
    if target == "supply_name":
        await query.edit_message_text("Введите наименование счета:", reply_markup=consumables_back_keyboard("supply_items"))
        return SUPPLY_NAME
    if target == "supply_organization":
        organizations = context.user_data.get("organizations") or get_active_suppliers()
        context.user_data["organizations"] = organizations
        await query.edit_message_text("Выберите поставщика:", reply_markup=organization_keyboard(organizations, allow_new=True))
        return SUPPLY_ORGANIZATION
    if target == "supply_organization_new":
        await query.edit_message_text("Введите наименование нового поставщика:", reply_markup=consumables_back_keyboard("supply_organization"))
        return SUPPLY_ORGANIZATION_NEW
    if target == "supply_amount":
        await query.edit_message_text("Введите сумму к оплате:", reply_markup=consumables_back_keyboard("supply_organization"))
        return SUPPLY_AMOUNT
    if target == "accept_supply":
        supplies = get_pending_supplies()
        await query.edit_message_text(supplies_list_text("Выберите поставку для приемки:", supplies), reply_markup=supplies_keyboard(supplies, "conssup"))
        return ACCEPT_SUPPLY

    await query.edit_message_text("🧾 Расходники", reply_markup=consumables_main_keyboard(update))
    return ConversationHandler.END


def get_consumables_conversation_handler():
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(receipt_start, pattern=r"^cons:receipt$"),
            CallbackQueryHandler(receipt_manage_start, pattern=r"^cons:receipt_(edit|delete)$"),
            CallbackQueryHandler(stock_view, pattern=r"^cons:stock$"),
            CallbackQueryHandler(movements_view, pattern=r"^cons:movements$"),
            CallbackQueryHandler(inventory_count_start, pattern=r"^cons:inventory_count$"),
            CallbackQueryHandler(inventory_review_start, pattern=r"^cons:inventory_review$"),
            CallbackQueryHandler(inventory_pdf_start, pattern=r"^cons:inventory_pdf$"),
            CallbackQueryHandler(add_supply_start, pattern=r"^cons:add_supply$"),
            CallbackQueryHandler(add_item_start, pattern=r"^cons:add_item$"),
            CallbackQueryHandler(delete_item_start, pattern=r"^cons:delete_item$"),
            CallbackQueryHandler(add_stock_start, pattern=r"^cons:add_stock$"),
            CallbackQueryHandler(set_rule_start, pattern=r"^cons:set_rule$"),
            CallbackQueryHandler(accept_supply_start, pattern=r"^cons:accept_supply$"),
            CallbackQueryHandler(edit_supply_start, pattern=r"^cons:edit_supply$"),
            CallbackQueryHandler(delete_supply_start, pattern=r"^cons:delete_supply$"),
            CallbackQueryHandler(edit_acceptance_start, pattern=r"^cons:edit_acceptance$"),
            CallbackQueryHandler(delete_acceptance_start, pattern=r"^cons:delete_acceptance$"),
            CallbackQueryHandler(add_supplier_start, pattern=r"^cons:add_supplier$"),
            CallbackQueryHandler(edit_supplier_start, pattern=r"^cons:edit_supplier$"),
            CallbackQueryHandler(delete_supplier_start, pattern=r"^cons:delete_supplier$"),
        ],
        states={
            SUPPLY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supply_name_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_ORGANIZATION: [
                CallbackQueryHandler(supply_organization_selected, pattern=r"^consorg:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_ORGANIZATION_NEW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supply_organization_new_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_ORGANIZATION_NEW_DELIVERY: [
                CallbackQueryHandler(supply_organization_new_delivery_selected, pattern=r"^consorgdelivery:(edo|paper)$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supply_amount_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_INVOICE_DOCUMENT: [
                MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, supply_invoice_document_received),
                MessageHandler(filters.ALL & ~filters.COMMAND, supply_invoice_wrong_message),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_ITEM_SELECT: [
                CallbackQueryHandler(supply_item_selected, pattern=r"^conssupplyitem:"),
                CallbackQueryHandler(supply_items_done, pattern=r"^conssupplyitems:done$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_ITEM_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supply_item_quantity_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ACCEPT_SUPPLY: [
                CallbackQueryHandler(accept_supply_selected, pattern=r"^conssup:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ACCEPT_LAYOUT_PHOTO: [
                MessageHandler(filters.PHOTO, layout_photo_received),
                MessageHandler(filters.ALL & ~filters.COMMAND, layout_photo_wrong_message),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ACCEPT_DOCUMENT: [
                MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, acceptance_document_received),
                MessageHandler(filters.ALL & ~filters.COMMAND, acceptance_document_wrong_message),
                CallbackQueryHandler(acceptance_document_missing, pattern=r"^consdoc:none$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_MANAGE_SELECT: [
                CallbackQueryHandler(supply_manage_selected, pattern=r"^consmanage:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_EDIT_FIELD: [
                CallbackQueryHandler(supply_edit_field_selected, pattern=r"^consfield:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supply_edit_value_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLY_DELETE_CONFIRM: [
                CallbackQueryHandler(supply_delete_confirmed, pattern=r"^conssupplydelete:yes$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ACCEPTANCE_MANAGE_SELECT: [
                CallbackQueryHandler(acceptance_manage_selected, pattern=r"^consacc:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ACCEPTANCE_DELETE_CONFIRM: [
                CallbackQueryHandler(acceptance_delete_confirmed, pattern=r"^consaccdelete:yes$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_DELETE_SELECT: [
                CallbackQueryHandler(supplier_delete_selected, pattern=r"^conssupplier:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_DELETE_CONFIRM: [
                CallbackQueryHandler(supplier_delete_confirmed, pattern=r"^conssupplierdelete:yes$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supplier_add_name_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_ADD_DELIVERY: [
                CallbackQueryHandler(supplier_add_delivery_selected, pattern=r"^conssupplieradddelivery:(edo|paper)$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_EDIT_SELECT: [
                CallbackQueryHandler(supplier_edit_selected, pattern=r"^conssupplieredit:\d+$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_EDIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, supplier_edit_name_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            SUPPLIER_EDIT_DELIVERY: [
                CallbackQueryHandler(supplier_edit_delivery_selected, pattern=r"^conssuppliereditdelivery:(edo|paper)$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ITEM_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_name_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ITEM_UNIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, item_unit_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ITEM_DELETE_SELECT: [
                CallbackQueryHandler(delete_item_selected, pattern=r"^consdeleteitem:\d+$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            ITEM_DELETE_CONFIRM: [
                CallbackQueryHandler(delete_item_confirmed, pattern=r"^consdeleteitem:yes$"),
                CallbackQueryHandler(delete_item_start, pattern=r"^consdeleteitem:back$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            STOCK_ITEM_SELECT: [
                CallbackQueryHandler(stock_item_selected, pattern=r"^consstockitem:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            STOCK_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, stock_quantity_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RULE_CATEGORY: [
                CallbackQueryHandler(rule_category_selected, pattern=r"^conscat:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RULE_MODEL: [
                CallbackQueryHandler(rule_model_selected, pattern=r"^consmodel:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RULE_PRODUCT: [
                CallbackQueryHandler(rule_product_selected, pattern=r"^consprod:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RULE_ITEM_SELECT: [
                CallbackQueryHandler(rule_item_selected, pattern=r"^consruleitem:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RULE_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rule_quantity_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_ITEM_SELECT: [
                CallbackQueryHandler(receipt_item_selected, pattern=r"^consreceiptitem:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_quantity_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_LAYOUT_PHOTOS: [
                MessageHandler(filters.PHOTO, receipt_layout_photo_received),
                CallbackQueryHandler(receipt_layout_photos_finished, pattern=r"^receiptphotos:done$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_DOCUMENT_KIND: [
                CallbackQueryHandler(receipt_document_kind_selected, pattern=r"^receiptdoc:(no_paper|scan|photo)$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_DOCUMENT_FILES: [
                MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, receipt_document_file_received),
                MessageHandler(filters.ALL & ~filters.COMMAND, receipt_document_file_received),
                CallbackQueryHandler(receipt_document_files_finished, pattern=r"^receiptdocs:done$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_CONFIRM: [
                CallbackQueryHandler(receipt_confirm_received, pattern=r"^receipt:confirm$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_MANAGE_SELECT: [
                CallbackQueryHandler(receipt_manage_selected, pattern=r"^consreceiptmanage:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_EDIT_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_edit_quantity_received),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            RECEIPT_DELETE_CONFIRM: [
                CallbackQueryHandler(receipt_delete_confirmed, pattern=r"^consreceiptdelete:yes$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_ITEM_SELECT: [
                CallbackQueryHandler(inventory_category_selected, pattern=r"^consinventory:category:[a-z_]+$"),
                CallbackQueryHandler(inventory_categories_back, pattern=r"^consinventory:categories$"),
                CallbackQueryHandler(inventory_item_selected, pattern=r"^consinventoryitem:"),
                CallbackQueryHandler(inventory_count_finish, pattern=r"^consinventory:finish$"),
                CallbackQueryHandler(inventory_complete_confirmed, pattern=r"^consinventory:complete:(yes|no)$"),
                CallbackQueryHandler(inventory_exit_requested, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, inventory_quantity_received),
                CallbackQueryHandler(inventory_back_to_list, pattern=r"^consinventory:back$"),
                CallbackQueryHandler(inventory_exit_requested, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_EXIT_CONFIRM: [
                CallbackQueryHandler(inventory_exit_action, pattern=r"^consinventory:exit:(save|resume|discard)$"),
                CallbackQueryHandler(inventory_discard_confirmed, pattern=r"^consinventory:discard:yes$"),
            ],
            INVENTORY_PDF_SELECT: [
                CallbackQueryHandler(inventory_pdf_selected, pattern=r"^consinventorypdf:"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_COMPARE_SELECT: [
                CallbackQueryHandler(inventory_compare_toggle, pattern=r"^conscompare:toggle:"),
                CallbackQueryHandler(inventory_compare_apply, pattern=r"^conscompare:apply$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_REVIEW_SELECT: [
                CallbackQueryHandler(inventory_review_item_selected, pattern=r"^consreview:item:"),
                CallbackQueryHandler(inventory_review_page_selected, pattern=r"^consreview:page:\d+$"),
                CallbackQueryHandler(inventory_review_apply, pattern=r"^consreview:apply$"),
                CallbackQueryHandler(inventory_review_reject, pattern=r"^consreview:reject$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
            INVENTORY_REVIEW_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, inventory_review_quantity_received),
                CallbackQueryHandler(inventory_review_back, pattern=r"^consreview:back$"),
                CallbackQueryHandler(consumables_cancel, pattern=r"^cons:cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(consumables_back, pattern=r"^consback:(receipt_items|receipt_quantity|receipt_layout|receipt_document_kind|receipt_document_files|supply_items|supply_name|supply_organization|supply_organization_new|supply_amount|accept_supply)$"),
            CommandHandler("cancel", consumables_cancel),
        ],
    )


def get_consumables_handlers():
    return [
        CallbackQueryHandler(consumables_menu, pattern=r"^section:consumables$"),
        CallbackQueryHandler(consumables_receipt_menu, pattern=r"^cons:receipt_menu$"),
        CallbackQueryHandler(consumables_counting_menu, pattern=r"^cons:module_counting$"),
        get_consumables_conversation_handler(),
    ]
