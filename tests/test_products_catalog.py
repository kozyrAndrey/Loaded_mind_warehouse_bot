import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.receiving import products


class ProductCatalogEditingTests(unittest.TestCase):
    def tearDown(self):
        products.reload_product_catalog()

    def test_builtin_product_edit_is_persistent_and_keeps_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "custom_products.json"
            with patch.object(products, "CUSTOM_PRODUCTS_PATH", catalog_path):
                products.reload_product_catalog()

                updated = products.update_catalog_product(
                    "h001",
                    category_name="Толстовки",
                    model_name="CULTURE TOP",
                    color="red",
                )
                products.reload_product_catalog()
                persisted = products.get_catalog_product("h001")

                self.assertEqual(updated["product_id"], "h001")
                self.assertEqual(persisted["category_id"], "hoodies")
                self.assertEqual(persisted["model_id"], "culture_hoodie")
                self.assertEqual(persisted["product_name"], "CULTURE TOP RED")

    def test_model_rename_updates_all_of_its_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "custom_products.json"
            with patch.object(products, "CUSTOM_PRODUCTS_PATH", catalog_path):
                products.reload_product_catalog()

                products.update_catalog_product(
                    "h001",
                    category_name="Худи / Зипы",
                    model_name="CULTURE TOP",
                    color="GREY",
                )

                self.assertEqual(products.get_catalog_product("h002")["product_name"], "CULTURE TOP BLUE")
                self.assertEqual(products.get_catalog_product("h003")["product_name"], "CULTURE TOP BLACK")

    def test_duplicate_color_in_same_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "custom_products.json"
            with patch.object(products, "CUSTOM_PRODUCTS_PATH", catalog_path):
                products.reload_product_catalog()

                with self.assertRaisesRegex(ValueError, "такого цвета"):
                    products.update_catalog_product(
                        "h001",
                        category_name="Худи / Зипы",
                        model_name="CULTURE HOODIE",
                        color="BLUE",
                    )

    def test_new_color_is_added_to_existing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "custom_products.json"
            with patch.object(products, "CUSTOM_PRODUCTS_PATH", catalog_path):
                products.reload_product_catalog()

                added = products.add_custom_product(
                    category_name="Худи / Зипы",
                    model_name="CULTURE HOODIE",
                    color="RED",
                )
                products.reload_product_catalog()

                self.assertEqual(added["category_id"], "hoodies")
                self.assertEqual(added["model_id"], "culture_hoodie")
                self.assertEqual(
                    products.get_catalog_product(added["product_id"])["product_name"],
                    "CULTURE HOODIE RED",
                )

    def test_adding_duplicate_color_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "custom_products.json"
            with patch.object(products, "CUSTOM_PRODUCTS_PATH", catalog_path):
                products.reload_product_catalog()

                with self.assertRaisesRegex(ValueError, "такого цвета"):
                    products.add_custom_product(
                        category_name="Худи / Зипы",
                        model_name="CULTURE HOODIE",
                        color="GREY",
                    )


if __name__ == "__main__":
    unittest.main()
