import unittest
import uuid
from decimal import Decimal

from modules.shipping.service import (
    CDEK_ORDER_ATTRIBUTE_NAME,
    ShippingServiceError,
    ShippingValidationError,
    build_order_units,
    create_posted_demand_from_order,
    get_order_shipping_flag,
    order_is_already_shipped,
    parse_order_lookup_query,
    parse_shipping_marking_code,
    position_unit_price,
    search_customer_orders,
    set_order_shipping_flag,
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
        self.metadata_attributes = []
        self.updates = []
        self.order_detail = {}
        self.template = {}
        self.put_calls = []
        self.create_calls = []
        self.base_url = "https://api.moysklad.test/api/remap/1.2"

    def list_entities(self, entity_type, params=None):
        self.order_calls.append((entity_type, dict(params or {})))
        return {"meta": {"size": len(self.orders)}, "rows": self.orders}

    def get_positions(self, entity_type, entity_id, params=None):
        self.position_calls.append((entity_type, entity_id, dict(params or {})))
        return {"meta": {"size": len(self.positions)}, "rows": self.positions}

    def get_href(self, href, params=None):
        raise AssertionError(f"Unexpected expansion: {href}")

    def get_entity(self, entity_type, entity_id, params=None):
        self.entity_call = (entity_type, entity_id, dict(params or {}))
        return self.order_detail

    def get(self, path, params=None):
        self.asserted_metadata_path = path
        return {"rows": self.metadata_attributes}

    def update_entity(self, entity_type, entity_id, payload, params=None):
        self.updates.append((entity_type, entity_id, payload))
        return {"id": entity_id, **payload}

    def put(self, path, payload, params=None):
        self.put_calls.append((path, payload, dict(params or {})))
        return self.template

    def create_entity(self, entity_type, payload, params=None):
        self.create_calls.append((entity_type, payload, dict(params or {})))
        return {"id": "demand-1", "name": "00001", "applicable": True}


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

    def test_cdek_scan_strips_prefix_and_searches_by_tracking_number(self):
        client = FakeClient(orders=[
            {"id": "1", "name": "mind-5098", "state": {"name": "Подтвержден"}},
        ])
        attribute_href = (
            "https://api.moysklad.test/api/remap/1.2/"
            "entity/customerorder/metadata/attributes/cdek-track"
        )
        client.metadata_attributes = [{
            "id": "cdek-track",
            "name": CDEK_ORDER_ATTRIBUTE_NAME,
            "type": "string",
            "meta": {"href": attribute_href},
        }]

        result = search_customer_orders(client, "[CDK]10310786311")

        self.assertEqual([row["name"] for row in result["orders"]], ["mind-5098"])
        self.assertEqual(result["query"], "10310786311")
        self.assertEqual(result["query_kind"], "cdek_track")
        self.assertEqual(
            client.order_calls[0][1]["filter"],
            f"{attribute_href}=10310786311",
        )
        self.assertNotIn("search", client.order_calls[0][1])

    def test_cdek_search_reports_missing_moysklad_attribute(self):
        with self.assertRaisesRegex(ShippingServiceError, CDEK_ORDER_ATTRIBUTE_NAME):
            search_customer_orders(FakeClient(), "[CDK]10310786311")

    def test_cdek_scan_is_case_insensitive_and_allows_space(self):
        self.assertEqual(
            parse_order_lookup_query(" [cdk] 10310786311 "),
            {"value": "10310786311", "kind": "cdek_track"},
        )

    def test_cdek_scan_requires_numeric_tracking_number(self):
        with self.assertRaises(ShippingValidationError):
            parse_order_lookup_query("[CDK]10310ABC")

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

    def test_variant_without_own_gtin_is_unmarked_even_if_parent_has_gtin(self):
        client = FakeClient(positions=[
            {
                "id": "p-cdek",
                "quantity": 1,
                "price": 50000,
                "assortment": {
                    "id": "variant-cdek",
                    "name": "Доставка СДЭК",
                    "meta": {"type": "variant"},
                    "barcodes": [],
                    "product": {
                        "id": "parent-product",
                        "name": "Родительский товар",
                        "barcodes": [{"gtin": VALID_GTIN}],
                    },
                },
            },
        ])

        result = build_order_units(client, {"id": "order-1", "name": "mind-5098"})

        self.assertEqual(result["units"], [])
        self.assertEqual(result["unmarked_count"], 1)

    def test_completed_shipping_sets_cloudpayments_link_to_departure_value(self):
        client = FakeClient()
        client.metadata_attributes = [{
            "id": "attribute-1",
            "name": "[CloudPayments] Ссылка на оплату",
            "type": "link",
            "meta": {
                "href": "https://api.moysklad.test/attribute-1",
                "type": "attributemetadata",
                "mediaType": "application/json",
            },
        }]

        set_order_shipping_flag(client, "order-1")

        self.assertEqual(client.asserted_metadata_path, "entity/customerorder/metadata/attributes")
        self.assertEqual(client.updates, [
            (
                "customerorder",
                "order-1",
                {
                    "attributes": [{
                        "meta": client.metadata_attributes[0]["meta"],
                        "value": "уедет",
                    }],
                },
            ),
        ])

    def test_already_shipped_flag_is_read_from_order(self):
        client = FakeClient()
        client.order_detail = {
            "attributes": [{
                "name": "[CloudPayments] Ссылка на оплату",
                "value": " УЕХАЛ ",
            }],
        }

        value = get_order_shipping_flag(client, "order-1")

        self.assertEqual(value, "УЕХАЛ")
        self.assertTrue(order_is_already_shipped(value))
        self.assertFalse(order_is_already_shipped("уедет"))

    def test_posted_demand_is_created_from_order_template_without_marking_codes(self):
        client = FakeClient()
        order_meta = {
            "href": f"{client.base_url}/entity/customerorder/order-1",
            "type": "customerorder",
        }
        assortment_meta = {
            "href": f"{client.base_url}/entity/product/product-1",
            "type": "product",
        }
        client.order_detail = {"id": "order-1", "meta": order_meta}
        client.template = {
            "name": "00001",
            "applicable": False,
            "agent": {"meta": {"href": "agent-href", "type": "counterparty"}, "name": "Покупатель"},
            "customerOrder": {"meta": order_meta, "name": "mind-5098"},
            "sum": 100000,
            "payedSum": 100000,
            "printed": False,
            "files": {"meta": {"size": 0}},
            "positions": {"rows": [{
                "id": "template-position",
                "quantity": 1,
                "price": 100000,
                "assortment": {"meta": assortment_meta, "name": "Куртка"},
                "overhead": 0,
                "trackingCodes": [{"cis": "must-not-be-copied"}],
            }]},
        }

        sync_id = "11111111-1111-4111-8111-111111111111"
        result = create_posted_demand_from_order(client, "order-1", sync_id)

        self.assertEqual(result["id"], "demand-1")
        self.assertEqual(client.put_calls, [(
            "entity/demand/new",
            {"customerOrder": {"meta": order_meta}},
            {},
        )])
        entity_type, payload, params = client.create_calls[0]
        self.assertEqual(entity_type, "demand")
        self.assertEqual(params, {})
        self.assertTrue(payload["applicable"])
        self.assertEqual(payload["customerOrder"], {"meta": order_meta})
        self.assertEqual(payload["agent"], {
            "meta": {"href": "agent-href", "type": "counterparty"},
        })
        self.assertEqual(payload["positions"], [{
            "quantity": 1,
            "price": 100000,
            "assortment": {"meta": assortment_meta},
        }])
        self.assertNotIn("sum", payload)
        self.assertNotIn("payedSum", payload)
        self.assertNotIn("files", payload)
        self.assertEqual(str(uuid.UUID(payload["syncId"])), sync_id)


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
