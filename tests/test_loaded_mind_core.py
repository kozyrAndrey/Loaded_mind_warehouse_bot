import unittest
from unittest.mock import patch

from core.keyboards import build_reply_main_keyboard
from modules.moysklad.search import normalize_query, search_products
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
