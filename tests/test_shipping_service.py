import unittest
from decimal import Decimal

from modules.shipping.service import (
    ShippingValidationError,
    build_order_units,
    parse_shipping_marking_code,
    position_unit_price,
    search_customer_orders,
)


VALID_GTIN = "04601234567893"
OTHER_GTIN = "04670332747445"
SERIAL = "ABCDEFGHIJKLM"
FULL_CODE = f"01{VALID_GTIN}21{SERIAL}\x1d91ABCD\x1d92SIGNATURE"


class FakeClient:
    def __init__(self, orders=None, positions=None):
        self.orders = orders or []
        self.positions = positions or []
        self.order_calls = []
        self.position_calls = []

    def list_entities(self, entity_type, params=None):
        self.order_calls.append((entity_type, dict(params or {})))
        return {"meta": {"size": len(self.orders)}, "rows": self.orders}

    def get_positions(self, entity_type, entity_id, params=None):
        self.position_calls.append((entity_type, entity_id, dict(params or {})))
        return {"meta": {"size": len(self.positions)}, "rows": self.positions}

    def get_href(self, href, params=None):
        raise AssertionError(f"Unexpected expansion: {href}")


class ShippingOrderTests(unittest.TestCase):
    def test_search_returns_multiple_orders_and_excludes_cancelled(self):
        client = FakeClient(orders=[
            {"id": "2", "name": "обмен-5098", "state": {"name": "Новый"}},
            {"id": "1", "name": "mind-5098", "state": {"name": "Подтвержден"}},
            {"id": "3", "name": "старый-5098", "state": {"name": "Отменён"}},
            {"id": "4", "name": "другой-7777", "state": {"name": "Новый"}},
        ])

        result = search_customer_orders(client, "5098")

        self.assertEqual([row["name"] for row in result["orders"]], ["mind-5098", "обмен-5098"])
        self.assertEqual(result["cancelled_count"], 1)
        self.assertEqual(client.order_calls[0][1]["search"], "5098")

    def test_search_requires_only_digits(self):
        with self.assertRaises(ShippingValidationError):
            search_customer_orders(FakeClient(), "mind-5098")

    def test_price_uses_position_discount(self):
        self.assertEqual(
            position_unit_price({"price": 129900, "discount": 10}),
            Decimal("1169.10"),
        )

    def test_each_unit_and_each_price_line_stays_separate(self):
        client = FakeClient(positions=[
            {
                "id": "p1",
                "quantity": 2,
                "price": 100000,
                "discount": 10,
                "assortment": {
                    "id": "a1",
                    "name": "Куртка",
                    "barcodes": [{"ean13": VALID_GTIN[1:]}],
                },
            },
            {
                "id": "p2",
                "quantity": 1,
                "price": 150000,
                "discount": 0,
                "assortment": {
                    "id": "a1",
                    "name": "Куртка",
                    "barcodes": [{"gtin": VALID_GTIN}],
                },
            },
            {
                "id": "p3",
                "quantity": 1,
                "price": 50000,
                "assortment": {"id": "a2", "name": "Сумка", "barcodes": []},
            },
        ])

        result = build_order_units(client, {"id": "order-1", "name": "mind-5098"})

        self.assertEqual(len(result["units"]), 3)
        self.assertEqual([row["price"] for row in result["units"]], ["900.00", "900.00", "1500.00"])
        self.assertEqual([row["position_id"] for row in result["units"]], ["p1", "p1", "p2"])
        self.assertEqual(result["unmarked_count"], 1)


class ShippingMarkingTests(unittest.TestCase):
    def test_full_code_is_reduced_to_31_characters(self):
        result = parse_shipping_marking_code(FULL_CODE, expected_gtin=VALID_GTIN)
        self.assertEqual(result["short_code"], f"01{VALID_GTIN}21{SERIAL}")
        self.assertEqual(len(result["short_code"]), 31)

    def test_parenthesized_code_is_supported(self):
        raw = f"(01){VALID_GTIN}(21){SERIAL}(91)ABCD(92)SIGNATURE"
        result = parse_shipping_marking_code(raw, expected_gtin=VALID_GTIN)
        self.assertEqual(result["short_code"], f"01{VALID_GTIN}21{SERIAL}")

    def test_invalid_gtin_check_digit_is_rejected(self):
        raw = f"010460123456789021{SERIAL}\x1d91ABCD\x1d92SIGNATURE"
        with self.assertRaisesRegex(ShippingValidationError, "контрольная"):
            parse_shipping_marking_code(raw)

    def test_missing_crypto_tail_is_rejected(self):
        with self.assertRaisesRegex(ShippingValidationError, "AI 91"):
            parse_shipping_marking_code(f"01{VALID_GTIN}21{SERIAL}")

    def test_missing_ai_92_is_rejected(self):
        with self.assertRaisesRegex(ShippingValidationError, "AI 92"):
            parse_shipping_marking_code(f"01{VALID_GTIN}21{SERIAL}\x1d91ABCD")

    def test_wrong_product_gtin_is_rejected(self):
        with self.assertRaisesRegex(ShippingValidationError, "не совпадает"):
            parse_shipping_marking_code(FULL_CODE, expected_gtin=OTHER_GTIN)


if __name__ == "__main__":
    unittest.main()
