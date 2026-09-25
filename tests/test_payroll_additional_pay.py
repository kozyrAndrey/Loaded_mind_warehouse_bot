import re
import unittest
from unittest.mock import patch

from modules.payroll.additional_pay import (
    ADDITIONAL_PAY_HEADERS,
    AdditionalPayValidationError,
    append_trend_island_payment,
    calculate_trend_island_pay,
    can_manage_additional_pay,
    previous_completed_week,
)
from modules.payroll.calculations import calculate_payroll_for_period
from modules.payroll.handlers import payroll_main_keyboard


class FakeWorksheet:
    def __init__(self):
        self.rows = []

    def get_all_records(self, numericise_ignore=None):
        return [dict(zip(ADDITIONAL_PAY_HEADERS, row)) for row in self.rows]

    def get_all_values(self):
        return [list(ADDITIONAL_PAY_HEADERS)] + [list(row) for row in self.rows]

    def append_row(self, row):
        self.rows.append(list(row))

    def update(self, a1_range, values):
        match = re.match(r"^A(\d+):T\d+$", a1_range)
        if not match:
            raise AssertionError(f"Unexpected range: {a1_range}")
        self.rows[int(match.group(1)) - 2] = list(values[0])

    def delete_rows(self, row_number):
        del self.rows[int(row_number) - 2]


def warehouse_manager():
    return {
        "employee_id": "warehouse-manager",
        "full_name": "Руководитель склада",
        "role": "warehouse_manager",
        "hourly_rate": 0,
        "fixed_salary": 0,
        "include_in_common_fund": False,
        "is_active": True,
        "telegram_user_id": "100",
    }


class AdditionalPayRuleTests(unittest.TestCase):
    def test_trend_island_formula_includes_weekly_rate_and_error_penalty(self):
        result = calculate_trend_island_pay(2, 500)

        self.assertEqual(result["gross_amount"], 5500)
        self.assertEqual(result["total_amount"], 5000)
        self.assertEqual(calculate_trend_island_pay(0)["total_amount"], 0)

    def test_error_penalty_cannot_turn_additional_pay_negative(self):
        with self.assertRaisesRegex(AdditionalPayValidationError, "не может превышать"):
            calculate_trend_island_pay(1, 4000)

    def test_previous_week_is_always_monday_to_sunday(self):
        week_start, week_end = previous_completed_week("06.08.2026")

        self.assertEqual(week_start.strftime("%d.%m.%Y"), "27.07.2026")
        self.assertEqual(week_end.strftime("%d.%m.%Y"), "02.08.2026")

    def test_additional_pay_is_hidden_from_payroll_menu(self):
        self.assertTrue(can_manage_additional_pay({"role": "brand_manager"}))
        self.assertTrue(can_manage_additional_pay({"role": "admin"}))
        self.assertFalse(can_manage_additional_pay({"role": "warehouse_manager"}))

        callbacks = {
            button.callback_data
            for row in payroll_main_keyboard(
                manager=True,
            ).inline_keyboard
            for button in row
        }
        self.assertNotIn("pay:additional_pay", callbacks)

    def test_only_one_trend_island_record_is_allowed_per_employee_and_week(self):
        worksheet = FakeWorksheet()
        kwargs = {
            "employee": warehouse_manager(),
            "week_start": "27.07.2026",
            "quantity": 2,
            "has_errors": True,
            "error_comment": "Ошибка в документах",
            "error_penalty": 500,
            "comment": "Проверено",
            "assigned_by": "Бренд-менеджер",
        }
        with patch(
            "modules.payroll.additional_pay.get_worksheet",
            return_value=worksheet,
        ):
            item = append_trend_island_payment(**kwargs)
            with self.assertRaisesRegex(AdditionalPayValidationError, "уже существует"):
                append_trend_island_payment(**kwargs)

        self.assertEqual(item["accrual_date"], "02.08.2026")
        self.assertEqual(item["unit_rate"], 2000)
        self.assertEqual(item["weekly_rate"], 1500)
        self.assertEqual(item["total_amount"], 5000)

    def test_saved_trend_island_payment_is_not_included_in_salary(self):
        employee = warehouse_manager()
        with (
            patch("modules.payroll.calculations.get_employees", return_value=[employee]),
            patch("modules.payroll.calculations.get_reports_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_expenses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_penalties_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_bonuses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_vacations_in_period", return_value=[]),
            patch("modules.payroll.calculations.SALARY_FIXED_PARTS", {}),
        ):
            total = calculate_payroll_for_period("16.08.2026", "31.08.2026")[employee["employee_id"]]

        self.assertNotIn("additional_pay_total", total)
        self.assertEqual(total["salary_without_expenses"], 0)


if __name__ == "__main__":
    unittest.main()
