import unittest
from datetime import date
from io import BytesIO

from pypdf import PdfReader

from modules.lamoda_fbs.cancellation_notices import (
    RETURNS,
    SHIPMENT,
    cancellation_document,
    cancellation_notice,
    count_outbound_orders,
    count_ready_returns,
    normalize_status,
)


class LamodaCancellationTests(unittest.TestCase):
    def test_status_normalization_supports_api_spelling_variants(self):
        self.assertEqual(normalize_status("Ready for shipment"), "READY_FOR_SHIPMENT")
        self.assertEqual(normalize_status("ready_for_shipment"), "READY_FOR_SHIPMENT")

    def test_outbound_count_includes_new_and_already_prepared_orders(self):
        orders = [
            {"status": "confirmed"},
            {"status": "AWAITING_SHIPMENT"},
            {"status": "Ready for shipment"},
            {"status": "SHIPPED_TO_WH"},
            {"status": "CANCELED"},
        ]
        self.assertEqual(count_outbound_orders(orders), 3)

    def test_only_ready_to_return_items_prevent_return_cancellation(self):
        rows = [
            {"status": "CREATED"},
            {"status": "READY_TO_RETURN"},
            {"status": "SHIPPED"},
        ]
        self.assertEqual(count_ready_returns(rows), 1)

    def test_separate_telegram_messages_are_clear(self):
        service_date = date(2026, 8, 11)
        shipment_title, shipment_text = cancellation_notice(SHIPMENT, service_date)
        returns_title, returns_text = cancellation_notice(RETURNS, service_date)

        self.assertEqual(shipment_title, "Требуется отмена отгрузочной машины Lamoda")
        self.assertIn("нет заказов для отгрузки", shipment_text)
        self.assertIn("11.08.2026", shipment_text)
        self.assertEqual(returns_title, "Требуется отмена возвратной машины Lamoda")
        self.assertIn("нет товаров, готовых к возврату", returns_text)
        self.assertIn("11.08.2026", returns_text)

    def test_separate_pdf_templates_are_created(self):
        service_date = date(2026, 8, 11)
        shipment_name, shipment_pdf = cancellation_document(SHIPMENT, service_date)
        returns_name, returns_pdf = cancellation_document(RETURNS, service_date)

        self.assertEqual(shipment_name, "otmena_otgruzochnoy_mashiny_2026-08-11.pdf")
        self.assertEqual(returns_name, "otmena_vozvratnoy_mashiny_2026-08-11.pdf")
        self.assertTrue(shipment_pdf.startswith(b"%PDF"))
        self.assertTrue(returns_pdf.startswith(b"%PDF"))
        shipment_text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(shipment_pdf)).pages)
        returns_text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(returns_pdf)).pages)
        self.assertIn("отгрузочной машины", shipment_text)
        self.assertIn("возвратной машины", returns_text)


if __name__ == "__main__":
    unittest.main()
