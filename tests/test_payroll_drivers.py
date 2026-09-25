import unittest
from unittest.mock import patch

from modules.payroll import drivers
from modules.payroll.calculations import build_full_payroll_text


class FakeWorksheet:
    def __init__(self, headers):
        self.headers = headers
        self.rows = []

    def get_all_records(self, numericise_ignore=None):
        return [dict(zip(self.headers, row)) for row in self.rows]

    def append_row(self, row):
        self.rows.append(list(row))

    def delete_rows(self, row_number):
        del self.rows[int(row_number) - 2]


class DriverStorageTests(unittest.TestCase):
    def setUp(self):
        self.driver_ws = FakeWorksheet(drivers.DRIVER_HEADERS)
        self.payment_ws = FakeWorksheet(drivers.DRIVER_PAYMENT_HEADERS)
        self.write_off_ws = FakeWorksheet(drivers.DRIVER_WRITE_OFF_HEADERS)

        def worksheet(name):
            if name == drivers.DRIVERS_SHEET:
                return self.driver_ws
            if name == drivers.DRIVER_PAYMENTS_SHEET:
                return self.payment_ws
            return self.write_off_ws

        self.worksheet_patch = patch("modules.payroll.drivers.get_worksheet", side_effect=worksheet)
        self.worksheet_patch.start()

    def tearDown(self):
        self.worksheet_patch.stop()

    def test_create_select_and_filter_driver_payments(self):
        driver = drivers.create_driver(
            "Иван Иванов", "+79990000000", "А123ВС777", created_by="Менеджер"
        )
        self.assertEqual(drivers.get_driver(driver["driver_id"])["full_name"], "Иван Иванов")
        self.assertEqual(drivers.get_driver(driver["driver_id"])["vehicle_number"], "А123ВС777")

        included = drivers.add_driver_payment(driver, "05.09.2026", 3500, "Доставка", "Менеджер")
        drivers.add_driver_payment(driver, "20.09.2026", 1000, "Поздняя", "Менеджер")

        actual = drivers.get_driver_payments("01.09.2026", "15.09.2026")
        self.assertEqual([item["driver_payment_id"] for item in actual], [included["driver_payment_id"]])
        self.assertEqual(actual[0]["vehicle_number"], "А123ВС777")

    def test_create_filter_and_delete_driver_write_off(self):
        driver = drivers.create_driver("Иван Иванов", vehicle_number="А123ВС777")
        included = drivers.add_driver_write_off(driver, "10.09.2026", 1000, "Аванс", "Менеджер")
        drivers.add_driver_write_off(driver, "20.09.2026", 500, "Другой период", "Менеджер")

        actual = drivers.get_driver_write_offs("01.09.2026", "15.09.2026")

        self.assertEqual([item["driver_write_off_id"] for item in actual], [included["driver_write_off_id"]])
        self.assertEqual(actual[0]["amount"], 1000)
        self.assertTrue(drivers.delete_driver_write_off(included["driver_write_off_id"]))
        self.assertEqual(drivers.get_driver_write_offs("01.09.2026", "15.09.2026"), [])

    def test_driver_payment_has_separate_total_in_payroll_statement(self):
        payment = {
            "driver_name": "Иван Иванов", "amount": 3500,
            "date": "05.09.2026", "vehicle_number": "А123ВС777", "comment": "Доставка",
        }
        period = {"start_date": "01.09.2026", "end_date": "15.09.2026", "payment_mode": "hourly"}
        with (
            patch("modules.payroll.calculations.calculate_payroll_for_period", return_value={}),
            patch("modules.payroll.drivers.get_driver_payments", return_value=[payment]),
            patch("modules.payroll.drivers.get_driver_write_offs", return_value=[]),
        ):
            text = build_full_payroll_text(period)

        self.assertIn("Водители:", text)
        self.assertIn("Иван Иванов: 3 500,00", text)
        self.assertIn("машина А123ВС777", text)
        self.assertIn("НАЧИСЛЕНО ВОДИТЕЛЯМ: 3 500,00", text)
        self.assertIn("СПИСАНО ВОДИТЕЛЯМ: 0,00", text)
        self.assertIn("ИТОГО ВОДИТЕЛИ: 3 500,00", text)
        self.assertIn("ОБЩИЙ ИТОГ: 0,00", text)
        self.assertLess(text.index("ОБЩИЙ ИТОГ"), text.index("Водители:"))

    def test_driver_write_off_reduces_only_driver_total(self):
        employee = {"full_name": "Склад", "include_in_common_fund": True}
        warehouse_total = {"employee": employee, "salary_with_expenses": 12000}
        payment = {
            "driver_name": "Иван Иванов", "amount": 3500,
            "date": "05.09.2026", "vehicle_number": "", "comment": "Доставка",
        }
        write_off = {
            "driver_name": "Иван Иванов", "amount": 1000,
            "date": "10.09.2026", "vehicle_number": "", "comment": "Выдано заранее",
        }
        period = {"start_date": "01.09.2026", "end_date": "15.09.2026", "payment_mode": "hourly"}
        with (
            patch("modules.payroll.calculations.calculate_payroll_for_period", return_value={"employee": warehouse_total}),
            patch("modules.payroll.calculations.format_payroll_statement_line", return_value="Склад: 12 000,00"),
            patch("modules.payroll.drivers.get_driver_payments", return_value=[payment]),
            patch("modules.payroll.drivers.get_driver_write_offs", return_value=[write_off]),
        ):
            text = build_full_payroll_text(period)

        self.assertIn("ОБЩИЙ ИТОГ: 12 000,00", text)
        self.assertIn("Списания (выдано ранее):", text)
        self.assertIn("Иван Иванов: −1 000,00", text)
        self.assertIn("НАЧИСЛЕНО ВОДИТЕЛЯМ: 3 500,00", text)
        self.assertIn("СПИСАНО ВОДИТЕЛЯМ: 1 000,00", text)
        self.assertIn("ИТОГО ВОДИТЕЛИ: 2 500,00", text)
