from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Начать сборку", callback_data="lamoda:assembly:start")],
        [InlineKeyboardButton("▶️ Продолжить сборку", callback_data="lamoda:assembly:continue")],
        [InlineKeyboardButton("🚚 Грузовые места", callback_data="lamoda:cargo:menu")],
        [InlineKeyboardButton("📤 Отгрузки", callback_data="lamoda:shipments")],
        [InlineKeyboardButton("↩️ Возвраты Lamoda", callback_data="lamoda:returns")],
        [InlineKeyboardButton("🏷 Операции ЧЗ Lamoda", callback_data="lamoda:marking")],
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu:start")],
    ])


def back_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Lamoda FBS", callback_data="section:lamoda")]])


def assembly_confirm():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Начать подготовку", callback_data="lamoda:assembly:prepare")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:lamoda")],
    ])


def cargo_menu_keyboard(session_id, manifest):
    rows = [[InlineKeyboardButton("➕ Создать грузовое место", callback_data=f"lamoda:cargo:new:{session_id}")]]
    for cargo in manifest:
        if cargo["status"] == "CLOSED" and not cargo.get("pallet_id"):
            rows.append([InlineKeyboardButton(
                f"🔓 Открыть место №{cargo['local_number']}",
                callback_data=f"lamoda:cargo:reopen:{cargo['id']}",
            )])
    rows.append([InlineKeyboardButton("🚚 Создать отгрузку Lamoda", callback_data=f"lamoda:shipment:create:{session_id}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="section:lamoda")])
    return InlineKeyboardMarkup(rows)


def cargo_scan_keyboard(cargo_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Грузовое место заполнено", callback_data=f"lamoda:cargo:close:{cargo_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="section:lamoda")],
    ])


def returns_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Оприходовать возврат", callback_data="lamoda:return:start")],
        [InlineKeyboardButton("📦 Ожидаемые возвраты", callback_data="lamoda:return:expected")],
        [InlineKeyboardButton("📋 Последние приёмки", callback_data="lamoda:return:recent")],
        [InlineKeyboardButton("⚠️ Проблемные возвраты", callback_data="lamoda:return:problems")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:lamoda")],
    ])


def return_condition():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Норм", callback_data="lamoda:return:condition:NORMAL")],
        [InlineKeyboardButton("❌ Брак", callback_data="lamoda:return:condition:DEFECT")],
    ])


def return_pack_optional():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Паковой этикетки нет", callback_data="lamoda:return:pack:skip")],
        [InlineKeyboardButton("❌ Отмена", callback_data="section:lamoda")],
    ])


def return_kiz_optional():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Товар без ЧЗ / пропустить", callback_data="lamoda:return:kiz:skip")],
        [InlineKeyboardButton("❌ Отмена", callback_data="section:lamoda")],
    ])


def defect_photos():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Готово", callback_data="lamoda:return:defect:done")],
        [InlineKeyboardButton("❌ Отмена", callback_data="section:lamoda")],
    ])


def marking_menu(counts):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📤 Вывод из оборота — {counts.get('WAITING_WITHDRAWAL', 0)}", callback_data="lamoda:marking:export:WITHDRAWAL")],
        [InlineKeyboardButton(f"↩️ Возврат в оборот — {counts.get('WAITING_REINTRODUCTION', 0)}", callback_data="lamoda:marking:export:REINTRODUCTION")],
        [InlineKeyboardButton(f"📦 Ожидаются обратно — {counts.get('RETURN_EXPECTED', 0)}", callback_data="lamoda:return:expected")],
        [InlineKeyboardButton(f"⚠️ Проблемные — {counts.get('NEEDS_RECONCILIATION', 0)}", callback_data="lamoda:return:problems")],
        [InlineKeyboardButton("📚 История", callback_data="lamoda:marking:history")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="section:lamoda")],
    ])


def batch_actions(batch_id, batch_type):
    confirmation = "возвращены в оборот" if batch_type == "REINTRODUCTION" else "выведены"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Все коды {confirmation}", callback_data=f"lamoda:batch:confirm:{batch_id}")],
        [InlineKeyboardButton("⚠️ Есть ошибки", callback_data=f"lamoda:batch:errors:{batch_id}")],
        [InlineKeyboardButton("❌ Отменить выгрузку", callback_data=f"lamoda:batch:cancel:{batch_id}")],
    ])
