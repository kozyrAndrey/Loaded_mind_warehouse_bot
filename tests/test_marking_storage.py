import csv
import unittest

from modules.marking.storage import CATALOG_SEED_PATH, HonestSignProduct


class MarkingStorageTests(unittest.TestCase):
    def test_catalog_schema_supports_products_without_gtin(self):
        self.assertTrue(HonestSignProduct.__table__.c.id.primary_key)
        self.assertTrue(HonestSignProduct.__table__.c.gtin.nullable)
        self.assertIn("size", HonestSignProduct.__table__.c)

    def test_seed_contains_requested_unmarked_products(self):
        if not CATALOG_SEED_PATH.is_file():
            self.skipTest("Каталог HMMLS не переносится в Loaded Mind")
        with CATALOG_SEED_PATH.open(encoding="utf-8", newline="") as file:
            products = [
                row
                for row in csv.DictReader(file)
                if not str(row.get("gtin") or "").strip()
            ]

        actual = {
            (product["honest_sign_name"], product["size"])
            for product in products
        }
        expected = {
            (
                'СУМКА МЕССЕНДЖЕР "HOMME BIRKIN MESSENGER" ЧЕРНАЯ',
                "250x190x100",
            ),
            (
                'СУМКА "MILLION DOLLAR BIRKIN" ЧЕРНАЯ',
                "550x470x260",
            ),
            (
                'СУМКА МЕССЕНДЖЕР "HM MESSENGER" ЧЕРНАЯ',
                "240x150x80",
            ),
            (
                'СУМКА ЧЕРЕЗ ПЛЕЧО "HOMME BIRKIN SHOULDER" ЧЕРНАЯ',
                "270x190x90",
            ),
        }

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
