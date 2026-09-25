import unittest

from modules.lamoda_fbs.moysklad_names import find_product_names_by_articles


class LamodaMoySkladNameTests(unittest.TestCase):
    def test_article_is_matched_from_variant_characteristic(self):
        rows = [{
            "meta": {"type": "variant"},
            "name": "HOMME BIRKIN MESSENGER (19x25x10, черная, HBM-BAG)",
            "characteristics": [
                {"name": "Размер", "value": "19x25x10"},
                {"name": "Артикул", "value": "HBM-BAG"},
            ],
        }]

        result = find_product_names_by_articles(
            object(), ["hbm-bag", "UNKNOWN"], assortment_rows=rows,
        )

        self.assertEqual(
            result,
            {"hbm-bag": "HOMME BIRKIN MESSENGER (19x25x10, черная, HBM-BAG)"},
        )

    def test_similar_article_is_not_used(self):
        rows = [{
            "meta": {"type": "variant"},
            "name": "Другой товар",
            "characteristics": [{"name": "Артикул", "value": "HBM-BAG-2"}],
        }]

        self.assertEqual(
            find_product_names_by_articles(object(), ["HBM-BAG"], assortment_rows=rows),
            {},
        )


if __name__ == "__main__":
    unittest.main()
