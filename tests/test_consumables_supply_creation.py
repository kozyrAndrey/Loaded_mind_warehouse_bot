import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.consumables.handlers import (
    SUPPLY_AMOUNT,
    SUPPLY_INVOICE_DOCUMENT,
    SUPPLY_ITEM_SELECT,
    SUPPLY_NAME,
    SUPPLY_ORGANIZATION,
    SUPPLY_ORGANIZATION_NEW,
    add_supply_start,
    invoice_caption_text,
    organization_keyboard,
    send_invoice_to_document_workflow_topic,
    supply_amount_received,
    supply_invoice_document_received,
    supply_items_done,
    supply_name_received,
    supply_organization_selected,
)


class ConsumablesSupplyCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_supply_starts_with_multiple_item_selection(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})
        items = [{"item_id": 1, "name": "Карточка", "unit": "шт", "is_active": True}]

        with (
            patch("modules.consumables.handlers.current_employee_or_none", return_value={"role": "manager"}),
            patch("modules.consumables.handlers.is_manager", return_value=True),
            patch("modules.consumables.handlers.get_consumable_items", return_value=items),
            patch("modules.consumables.handlers.supply_items_text", return_value="Выберите расходник"),
            patch("modules.consumables.handlers.supply_items_keyboard", return_value="items_keyboard"),
        ):
            state = await add_supply_start(update, context)

        self.assertEqual(state, SUPPLY_ITEM_SELECT)
        self.assertEqual(
            context.user_data,
            {"consumables_module": "supplies", "supply_items": {}},
        )
        query.edit_message_text.assert_awaited_once_with(
            "Выберите расходник",
            reply_markup="items_keyboard",
        )

    async def test_selected_items_continue_to_invoice_name(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={
                "supply_items": {
                    "1": {"item_id": 1, "item_name": "Карточка", "unit": "шт", "quantity": 2}
                }
            }
        )

        with patch("modules.consumables.handlers.consumables_back_keyboard", return_value="cancel"):
            state = await supply_items_done(update, context)

        self.assertEqual(state, SUPPLY_NAME)
        query.edit_message_text.assert_awaited_once_with(
            "Введите наименование счета:",
            reply_markup="cancel",
        )

    async def test_invoice_name_continues_to_supplier_selection_with_add_button(self):
        message = SimpleNamespace(text="Счет на оплату № 403", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})
        suppliers = ["ИП Бакиров Динис Ирекович"]

        with (
            patch("modules.consumables.handlers.get_active_suppliers", return_value=suppliers),
            patch("modules.consumables.handlers.organization_keyboard", return_value="supplier_keyboard") as keyboard,
        ):
            state = await supply_name_received(update, context)

        self.assertEqual(state, SUPPLY_ORGANIZATION)
        self.assertEqual(context.user_data["supply_name"], "Счет на оплату № 403")
        self.assertEqual(context.user_data["organizations"], suppliers)
        keyboard.assert_called_once_with(suppliers, allow_new=True)
        message.reply_text.assert_awaited_once_with(
            "Выберите поставщика:",
            reply_markup="supplier_keyboard",
        )

    async def test_supplier_selection_allows_adding_first_supplier(self):
        message = SimpleNamespace(text="Счет № 1", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        with (
            patch("modules.consumables.handlers.get_active_suppliers", return_value=[]),
            patch("modules.consumables.handlers.organization_keyboard", return_value="supplier_keyboard") as keyboard,
        ):
            state = await supply_name_received(update, context)

        self.assertEqual(state, SUPPLY_ORGANIZATION)
        keyboard.assert_called_once_with([], allow_new=True)
        message.reply_text.assert_awaited_once_with(
            "Выберите поставщика:",
            reply_markup="supplier_keyboard",
        )

    def test_supplier_keyboard_has_new_supplier_button(self):
        keyboard = organization_keyboard([], allow_new=True)
        buttons = [button for row in keyboard.inline_keyboard for button in row]

        self.assertIn(
            ("➕ Новый поставщик", "consorg:new"),
            [(button.text, button.callback_data) for button in buttons],
        )

    async def test_new_supplier_button_requests_supplier_name(self):
        query = SimpleNamespace(
            data="consorg:new",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with patch("modules.consumables.handlers.consumables_back_keyboard", return_value="cancel"):
            state = await supply_organization_selected(update, context)

        self.assertEqual(state, SUPPLY_ORGANIZATION_NEW)
        query.edit_message_text.assert_awaited_once_with(
            "Введите наименование нового поставщика:",
            reply_markup="cancel",
        )

    async def test_amount_requests_invoice_file(self):
        message = SimpleNamespace(text="470", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        with patch("modules.consumables.handlers.consumables_back_keyboard", return_value="cancel"):
            state = await supply_amount_received(update, context)

        self.assertEqual(state, SUPPLY_INVOICE_DOCUMENT)
        self.assertEqual(context.user_data["supply_amount"], 470)
        message.reply_text.assert_awaited_once_with(
            "Отправьте файл счета PDF, документом или фото.",
            reply_markup="cancel",
        )

    async def test_invoice_file_creates_pending_supply_and_sends_it_to_workflow(self):
        message = SimpleNamespace(
            photo=[],
            document=SimpleNamespace(file_id="invoice-file"),
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=42),
        )
        items = [{"item_id": 1, "item_name": "Карточка", "unit": "шт", "quantity": 2}]
        context = SimpleNamespace(
            user_data={
                "supply_name": "Счет на оплату № 403 от 14 июля 2026 г.",
                "organization": "ИП Бакиров Динис Ирекович",
                "supply_amount": 470,
                "supply_items": {"1": items[0]},
            }
        )
        supply = {
            "id": 17,
            "status": "pending",
            "consumable_name": context.user_data["supply_name"],
            "organization": context.user_data["organization"],
            "amount": 470,
            "supply_items": items,
        }

        with (
            patch("modules.consumables.handlers.current_employee_name", return_value="Сотрудник склада"),
            patch("modules.consumables.handlers.create_supply", return_value=supply) as create,
            patch(
                "modules.consumables.handlers.send_invoice_to_document_workflow_topic",
                new=AsyncMock(return_value="Счет отправлен в документооборот ✅"),
            ) as send_invoice,
            patch("modules.consumables.handlers.consumables_supplies_keyboard", return_value="menu"),
        ):
            state = await supply_invoice_document_received(update, context)

        self.assertEqual(state, ConversationHandler.END)
        create.assert_called_once_with(
            consumable_name="Счет на оплату № 403 от 14 июля 2026 г.",
            organization="ИП Бакиров Динис Ирекович",
            amount=470,
            supply_items=items,
            created_by_user_id=42,
            created_by_name="Сотрудник склада",
            invoice_document_file_id="invoice-file",
            invoice_document_kind="document",
        )
        send_invoice.assert_awaited_once_with(
            context,
            supply=supply,
            invoice_file_id="invoice-file",
            invoice_kind="document",
        )
        self.assertEqual(context.user_data, {})

    def test_invoice_caption_matches_required_template(self):
        supply = {
            "consumable_name": "Счет на оплату № 403 от 14 июля 2026 г.",
            "organization": "ИП Бакиров Динис Ирекович",
            "amount": 470,
            "supply_items": [
                {"item_name": "Карточка", "quantity": 2, "unit": "шт"},
            ],
        }

        self.assertEqual(
            invoice_caption_text(supply),
            "1 - Карточка 2 шт.\n"
            "2 - Счет на оплату № 403 от 14 июля 2026 г.\n"
            "<b>3 - ИП Бакиров Динис Ирекович</b>\n"
            "<b>4 - 470</b>",
        )

    async def test_invoice_is_sent_to_warehouse_invoices_topic(self):
        bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock())
        context = SimpleNamespace(bot=bot)
        supply = {
            "consumable_name": "Счет № 403",
            "organization": "ИП Бакиров",
            "amount": 470,
            "supply_items": [{"item_name": "Карточка", "quantity": 2, "unit": "шт"}],
        }

        with (
            patch("modules.consumables.handlers.DOCUMENT_WORKFLOW_CHAT_ID", "-100123"),
            patch("modules.consumables.handlers.WAREHOUSE_INVOICES_TOPIC_ID", "456"),
        ):
            result = await send_invoice_to_document_workflow_topic(
                context,
                supply=supply,
                invoice_file_id="invoice-file",
                invoice_kind="document",
            )

        self.assertEqual(result, "Счет отправлен в документооборот ✅")
        bot.send_document.assert_awaited_once_with(
            chat_id=-100123,
            message_thread_id=456,
            document="invoice-file",
            caption=invoice_caption_text(supply),
            parse_mode="HTML",
        )


if __name__ == "__main__":
    unittest.main()
