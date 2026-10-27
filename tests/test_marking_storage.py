import unittest

from modules.marking.storage import HonestSignProduct


class MarkingStorageTests(unittest.TestCase):
    def test_catalog_schema_supports_products_without_gtin(self):
        self.assertTrue(HonestSignProduct.__table__.c.id.primary_key)
        self.assertTrue(HonestSignProduct.__table__.c.gtin.nullable)
        self.assertIn("size", HonestSignProduct.__table__.c)

    def test_catalog_is_not_seeded_from_another_organization(self):
        from modules.marking import storage

        self.assertFalse(hasattr(storage, "CATALOG_SEED_PATH"))


if __name__ == "__main__":
    unittest.main()
