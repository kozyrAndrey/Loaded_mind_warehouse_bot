import unittest
from types import SimpleNamespace

from modules.marking.moysklad_lookup import (
    barcode_matches,
    gtin_barcode_variants,
    lookup_rows_by_gtin,
    product_info_from_row,
)


class FakeMoySkladClient:
    def __init__(self, expected_barcode, row):
        self.expected_barcode = expected_barcode
        self.row = row
        self.filters = []

    def list_entities(self, entity_type, params=None):
        self.filters.append(params["filter"])
        if params["filter"] == f"barcode={self.expected_barcode}":
            return {"rows": [self.row]}
        return {"rows": []}


class MoySkladLookupTests(unittest.TestCase):
    def test_gtin_with_leading_zero_has_ean13_variant(self):
        self.assertEqual(
            gtin_barcode_variants("04670332747445"),
            ("04670332747445", "4670332747445"),
        )

    def test_ean13_has_gtin_with_leading_zero_variant(self):
        self.assertEqual(
            gtin_barcode_variants("4670332747445"),
            ("4670332747445", "04670332747445"),
        )

    def test_lookup_tries_gtin14_and_ean13(self):
        row = {"name": "CORSET BOMBER", "barcodes": [{"ean13": "4670332747445"}]}
        client = FakeMoySkladClient("4670332747445", row)

        rows = lookup_rows_by_gtin(client, "assortment", "04670332747445")

        self.assertEqual(rows, [row])
        self.assertIn("barcode=04670332747445", client.filters)
        self.assertIn("barcode=4670332747445", client.filters)

    def test_barcode_match_accepts_equivalent_gtin_formats(self):
        row = {"barcodes": [{"ean13": "4670332747445"}]}

        self.assertTrue(barcode_matches(row, "04670332747445"))

    def test_product_info_contains_large_label_fields(self):
        row = {
            "name": "HOMME LEATHER JACKET BLACK L",
            "article": "HLJB-L",
            "barcodes": [{"ean13": "4670332747445"}],
            "characteristics": [{"name": "Размер", "value": "L"}],
            "attributes": [
                {"name": "Цвет", "value": "черный"},
                {"name": "Состав", "value": "Полиуретан 100%"},
                {"name": "Производитель", "value": "Производитель"},
            ],
        }

        actual = product_info_from_row(SimpleNamespace(), row)

        self.assertEqual(actual["article"], "HLJB-L")
        self.assertEqual(actual["color"], "черный")
        self.assertEqual(actual["composition"], "Полиуретан 100%")
        self.assertEqual(actual["ean13"], "4670332747445")

    def test_large_label_requisites_come_from_parent_product_attributes(self):
        class Client:
            def get_href(self, href, params=None):
                self_href = "https://api.moysklad.ru/api/remap/1.2/entity/product/1"
                self_test.assertEqual(href, self_href)
                return {
                    "name": "Товар Loaded Mind",
                    "attributes": [
                        {"name": "Заказчик", "value": "ООО Лоадед Майнд"},
                        {"name": "Производитель", "value": {"name": "ООО Фабрика"}},
                    ],
                }

        self_test = self
        row = {
            "name": "Вариант",
            "product": {"meta": {"href": "https://api.moysklad.ru/api/remap/1.2/entity/product/1"}},
        }
        actual = product_info_from_row(Client(), row)
        self.assertEqual(actual["customer"], "ООО Лоадед Майнд")
        self.assertEqual(actual["manufacturer"], "ООО Фабрика")


if __name__ == "__main__":
    unittest.main()
