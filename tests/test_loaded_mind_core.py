import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import BotCommandScopeAllPrivateChats

from bot import publish_private_commands
from core.keyboards import build_reply_main_keyboard
from modules.moysklad.search import compact_product_name, normalize_query, search_products
from modules.receiving.loaded_mind import SELECT_PRODUCT, save as save_receiving
from modules.receiving.postgres_storage import build_receiving_report_text
from modules.returns.loaded_mind import summary


class FakeMoySklad:
    def __init__(self):
        self.calls = []

    def list_entities(self, entity_type, params):
        self.calls.append((entity_type, params))
        if "filter" in params:
            return {"rows": [{"id": "product-1", "name": "LM HOODIE M", "barcodes": [{"ean13": "4601234567890"}]}]}
        return {"rows": []}


class ProductSearchTests(unittest.TestCase):
    def test_scanned_chz_uses_gtin_barcode_filter(self):
        client = FakeMoySklad()
        products = search_products("(01)04601234567890(21)SERIAL1234567", client=client)
        self.assertEqual(products[0]["id"], "product-1")
        self.assertEqual(client.calls[0][0], "assortment")
        self.assertEqual(client.calls[0][1]["filter"], "barcode=04601234567890")

    def test_name_uses_remote_search(self):
        client = FakeMoySklad()
        self.assertEqual(search_products("hoodie", client=client), [])
        self.assertEqual(client.calls[0][1]["search"], "hoodie")

    def test_name_search_keeps_only_requested_color_and_clean_size(self):
        class ColorClient:
            def list_entities(self, entity_type, params):
                return {"rows": [
                    {"id": "pink", "name": "LEO PUFFER PINK (L, розовый, полиэстр 100%)",
                     "characteristics": [{"name": "Размер", "value": "L"}]},
                    {"id": "parent", "name": "LEO PUFFER BLACK"},
                    {"id": "black-l", "name": "LEO PUFFER BLACK (L, черный, полиэстр 100%)",
                     "characteristics": [{"name": "Размер", "value": "L"}]},
                    {"id": "black-m", "name": "LEO PUFFER BLACK (M, черный, полиэстр 100%)",
                     "characteristics": [{"name": "Размер", "value": "M"}]},
                ]}

        products = search_products("Leo puffer black", client=ColorClient())
        self.assertEqual([item["display_name"] for item in products],
                         ["LEO PUFFER BLACK L", "LEO PUFFER BLACK M"])
        self.assertEqual(products[0]["base_name"], "LEO PUFFER BLACK")

    def test_variant_name_from_existing_record_is_compact(self):
        self.assertEqual(
            compact_product_name("LEO PUFFER BLACK (L, черный, полиэстр 100%)", "L"),
            "LEO PUFFER BLACK L",
        )

    def test_precise_search_scans_next_moysklad_page(self):
        class PagedClient:
            def __init__(self):
                self.offsets = []

            def list_entities(self, entity_type, params):
                self.offsets.append(params["offset"])
                if params["offset"] == 0:
                    return {"meta": {"size": 101}, "rows": [
                        {"id": f"pink-{index}", "name": "LEO PUFFER PINK"}
                        for index in range(100)
                    ]}
                return {"meta": {"size": 101}, "rows": [
                    {"id": "black-l", "name": "LEO PUFFER BLACK (L, черный)",
                     "characteristics": [{"name": "Размер", "value": "L"}]}
                ]}

        client = PagedClient()
        self.assertEqual(
            [item["display_name"] for item in search_products("Leo puffer black", client=client)],
            ["LEO PUFFER BLACK L"],
        )
        self.assertEqual(client.offsets, [0, 100])


class ReceivingFlowTests(unittest.IsolatedAsyncioTestCase):
    @patch("modules.receiving.loaded_mind.save_moysklad_incoming_good", return_value=42)
    async def test_save_returns_to_same_search_results(self, save_record):
        product = {
            "id": "black-l", "name": "LEO PUFFER BLACK (L, черный, полиэстр 100%)",
            "base_name": "LEO PUFFER BLACK", "display_name": "LEO PUFFER BLACK L", "size": "L",
        }
        data = {
            "report_type": "new_supply", "results": [product], "product": product,
            "record_date": "25.09.2026", "packed": 2, "defective": 0, "rework": 0,
        }
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=413489632, username="admin", full_name="Admin"),
        )
        context = SimpleNamespace(user_data={"lm_receiving": data})

        state = await save_receiving(update, context)

        self.assertEqual(state, SELECT_PRODUCT)
        self.assertEqual(context.user_data["lm_receiving"]["results"], [product])
        self.assertNotIn("product", context.user_data["lm_receiving"])
        self.assertEqual(save_record.call_args.kwargs["product_name"], "LEO PUFFER BLACK")
        buttons = query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].text, "LEO PUFFER BLACK L")


class ReceivingReportTests(unittest.TestCase):
    @patch("modules.receiving.postgres_storage.session_scope")
    def test_report_uses_compact_name_and_size(self, session_scope):
        record = SimpleNamespace(
            category_name="Новая поставка",
            product_name="LEO PUFFER BLACK (L, черный, полиэстр 100%)", size="L",
            packed=2, defective=0, rework=0,
        )
        session_scope.return_value.__enter__.return_value.execute.return_value.scalars.return_value.all.return_value = [record]

        report = build_receiving_report_text("25.09.2026", group_by_type=True)

        self.assertIn("📦 Новая поставка\nLEO PUFFER BLACK L", report)
        self.assertNotIn("полиэстр", report)


class KeyboardTests(unittest.TestCase):
    def test_schedule_and_admin_are_manager_only(self):
        enabled = {"receiving", "returns", "consumables", "marking", "payroll", "schedule", "employees"}
        employee = {"roles": ["warehouse_employee"], "role": "warehouse_employee"}
        manager = {"roles": ["warehouse_manager"], "role": "warehouse_manager"}
        staff_labels = [button.text for row in build_reply_main_keyboard(employee, enabled).keyboard for button in row]
        manager_labels = [button.text for row in build_reply_main_keyboard(manager, enabled).keyboard for button in row]
        self.assertNotIn("📅 Расписание", staff_labels)
        self.assertNotIn("⚙️ Управление ботом", staff_labels)
        self.assertIn("📅 Расписание", manager_labels)
        self.assertIn("⚙️ Управление ботом", manager_labels)


class CommandMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_commands_expose_working_menu(self):
        bot = SimpleNamespace(set_my_commands=AsyncMock())

        await publish_private_commands(SimpleNamespace(bot=bot))

        commands = bot.set_my_commands.await_args.args[0]
        self.assertEqual(
            [command.command for command in commands],
            ["start", "menu", "whoami", "whereami"],
        )
        self.assertIsInstance(
            bot.set_my_commands.await_args.kwargs["scope"],
            BotCommandScopeAllPrivateChats,
        )


class ReturnSummaryTests(unittest.TestCase):
    @patch("modules.returns.loaded_mind.get_employees", return_value=[])
    def test_defect_and_chz_are_shown(self, _employees):
        value = {
            "return_type": "cdek", "counterparty": "Тест", "track_number": "123",
            "items": [{"product_name": "LM HOODIE", "size": "M", "condition_key": "invoice_defect",
                       "condition_label": "брак по накладной", "condition_comment": "Пятно"}],
        }
        self.assertIn("Пятно", summary(value))
        self.assertIn("123", summary(value))


if __name__ == "__main__":
    unittest.main()
