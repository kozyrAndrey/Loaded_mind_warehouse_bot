import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.consumables.handlers import (
    SUPPLIER_ADD_DELIVERY,
    SUPPLIER_ADD_NAME,
    SUPPLIER_EDIT_DELIVERY,
    SUPPLIER_EDIT_NAME,
    SUPPLIER_EDIT_SELECT,
    add_supplier_start,
    edit_supplier_start,
    supplier_add_delivery_selected,
    supplier_add_name_received,
    supplier_edit_delivery_selected,
    supplier_edit_name_received,
    supplier_edit_selected,
)


class ConsumablesSupplierTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_manager_can_start_adding_supplier(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with (
            patch("modules.consumables.handlers.current_employee_or_none", return_value={"role": "warehouse_employee"}),
            patch("modules.consumables.handlers.is_manager", return_value=False),
        ):
            state = await add_supplier_start(update, context)

        self.assertEqual(state, ConversationHandler.END)
        query.edit_message_text.assert_awaited_once_with("Недостаточно прав.")

    async def test_manager_adds_supplier_with_document_delivery_method(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with (
            patch("modules.consumables.handlers.current_employee_or_none", return_value={"role": "admin"}),
            patch("modules.consumables.handlers.is_manager", return_value=True),
            patch("modules.consumables.handlers.consumables_back_keyboard", return_value="cancel"),
        ):
            state = await add_supplier_start(update, context)

        self.assertEqual(state, SUPPLIER_ADD_NAME)

        message = SimpleNamespace(text="ИП Бакиров", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        with patch(
            "modules.consumables.handlers.supplier_documents_delivery_keyboard",
            return_value="delivery_keyboard",
        ):
            state = await supplier_add_name_received(update, context)

        self.assertEqual(state, SUPPLIER_ADD_DELIVERY)
        self.assertEqual(context.user_data["supplier_name"], "ИП Бакиров")

        query = SimpleNamespace(
            data="conssupplieradddelivery:edo",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        supplier = {
            "id": 7,
            "name": "ИП Бакиров",
            "closing_documents_delivery": "edo",
        }
        with (
            patch("modules.consumables.handlers.create_supplier", return_value=supplier) as create,
            patch("modules.consumables.handlers.consumables_suppliers_keyboard", return_value="menu"),
        ):
            state = await supplier_add_delivery_selected(update, context)

        self.assertEqual(state, ConversationHandler.END)
        create.assert_called_once_with("ИП Бакиров", "edo")
        self.assertEqual(context.user_data, {})

    async def test_manager_can_rename_supplier_and_change_delivery_method(self):
        supplier = {
            "id": 7,
            "name": "ИП Бакиров",
            "closing_documents_delivery": "paper",
        }
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        with (
            patch("modules.consumables.handlers.current_employee_or_none", return_value={"role": "admin"}),
            patch("modules.consumables.handlers.is_manager", return_value=True),
            patch("modules.consumables.handlers.get_supplier_records", return_value=[supplier]),
            patch("modules.consumables.handlers.supplier_records_keyboard", return_value="list_keyboard"),
        ):
            state = await edit_supplier_start(update, context)

        self.assertEqual(state, SUPPLIER_EDIT_SELECT)

        query = SimpleNamespace(
            data="conssupplieredit:7",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        with patch("modules.consumables.handlers.consumables_back_keyboard", return_value="cancel"):
            state = await supplier_edit_selected(update, context)
        self.assertEqual(state, SUPPLIER_EDIT_NAME)

        message = SimpleNamespace(text="ООО Бакиров", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        with patch(
            "modules.consumables.handlers.supplier_documents_delivery_keyboard",
            return_value="delivery_keyboard",
        ):
            state = await supplier_edit_name_received(update, context)
        self.assertEqual(state, SUPPLIER_EDIT_DELIVERY)

        query = SimpleNamespace(
            data="conssuppliereditdelivery:edo",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        updated = {
            "id": 7,
            "name": "ООО Бакиров",
            "closing_documents_delivery": "edo",
        }
        with (
            patch("modules.consumables.handlers.update_supplier", return_value=updated) as update_record,
            patch("modules.consumables.handlers.consumables_suppliers_keyboard", return_value="menu"),
        ):
            state = await supplier_edit_delivery_selected(update, context)

        self.assertEqual(state, ConversationHandler.END)
        update_record.assert_called_once_with(
            7,
            name="ООО Бакиров",
            closing_documents_delivery="edo",
        )
        self.assertEqual(context.user_data, {})


if __name__ == "__main__":
    unittest.main()
