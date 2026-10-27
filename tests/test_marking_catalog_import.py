import csv
import io
import unittest

from openpyxl import Workbook

from modules.marking.catalog_import import CatalogImportError, parse_catalog_file


def workbook_bytes(rows, title="Товары"):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title
    for row in rows:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class MarkingCatalogImportTests(unittest.TestCase):
    def test_detects_headers_after_title_rows_and_normalizes_gtin(self):
        content = workbook_bytes(
            [
                ["Отчёт по товарам"],
                [],
                ["Артикул", "Штрихкод товара", "Наименование товара"],
                ["A-1", 4670332742266, "HOODIE BLACK M"],
                ["A-2", "04670332742273", "HOODIE BLACK L"],
            ]
        )

        result = parse_catalog_file(content, "catalog.xlsx")

        self.assertEqual(result.header_row, 3)
        self.assertEqual(result.gtin_column, "B")
        self.assertEqual(result.name_column, "C")
        self.assertEqual(
            result.rows,
            [
                {"gtin": "04670332742266", "honest_sign_name": "HOODIE BLACK M", "size": ""},
                {"gtin": "04670332742273", "honest_sign_name": "HOODIE BLACK L", "size": ""},
            ],
        )

    def test_detects_two_columns_without_headers(self):
        content = workbook_bytes(
            [
                ["04670332742266", "HOODIE BLACK M"],
                ["04670332742273", "HOODIE BLACK L"],
            ]
        )

        result = parse_catalog_file(content, "catalog.xlsx")

        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.gtin_column, "A")
        self.assertEqual(result.name_column, "B")

    def test_reads_semicolon_csv(self):
        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["GTIN", "Название"])
        writer.writerow(["04670332742266", "HOODIE BLACK M"])

        result = parse_catalog_file(stream.getvalue().encode("utf-8-sig"), "catalog.csv")

        self.assertEqual(result.rows[0]["gtin"], "04670332742266")

    def test_rejects_conflicting_duplicate_gtin(self):
        content = workbook_bytes(
            [
                ["GTIN", "Название"],
                ["04670332742266", "FIRST NAME"],
                ["04670332742266", "SECOND NAME"],
            ]
        )

        with self.assertRaises(CatalogImportError) as caught:
            parse_catalog_file(content, "catalog.xlsx")

        self.assertIn("другим названием", str(caught.exception))

    def test_rejects_missing_name_without_partial_import(self):
        content = workbook_bytes(
            [
                ["GTIN", "Название"],
                ["04670332742266", "HOODIE BLACK M"],
                ["04670332742273", ""],
            ]
        )

        with self.assertRaises(CatalogImportError) as caught:
            parse_catalog_file(content, "catalog.xlsx")

        self.assertIn("не указано название", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
