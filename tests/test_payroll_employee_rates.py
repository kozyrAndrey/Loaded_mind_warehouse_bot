import unittest
from unittest.mock import patch

from modules.payroll.calculations import calculate_payroll_for_period, get_salary_fixed_parts
from modules.payroll.google_sheets import EMPLOYEE_HEADERS, safe_hourly_rate, sync_employees_sheet


class FakeEmployeeWorksheet:
    def __init__(self, rows):
        self.rows = [list(row) for row in rows]
        self.appended = []
        self.updated = []

    def get_all_values(self):
        return [list(EMPLOYEE_HEADERS), *self.rows]

    def append_rows(self, rows):
        self.appended.extend([list(row) for row in rows])

    def update(self, a1_range, values):
        self.updated.append((a1_range, values))


class EmployeeRateSourceTests(unittest.TestCase):
    def test_startup_sync_does_not_overwrite_edited_employee_values(self):
        edited_row = [
            "emp001",
            "Андрей Козырь",
            "",
            "413489632",
            "opulent_shooter",
            "warehouse_manager",
            "warehouse_manager,admin",
            "1250",
            "90000",
            "FALSE",
            "TRUE",
        ]
        worksheet = FakeEmployeeWorksheet([edited_row])

        with patch(
            "modules.payroll.google_sheets.PAYROLL_EMPLOYEES",
            [{
                "employee_id": "emp001",
                "full_name": "Андрей Козырь",
                "role": "warehouse_manager",
                "hourly_rate": 437.5,
                "fixed_salary": 70000,
                "include_in_common_fund": False,
                "is_active": True,
            }],
        ):
            sync_employees_sheet(worksheet)

        self.assertEqual(worksheet.rows, [edited_row])
        self.assertEqual(worksheet.updated, [])
        self.assertEqual(worksheet.appended, [])

    def test_hourly_rate_above_one_thousand_is_not_divided_by_ten(self):
        self.assertEqual(safe_hourly_rate("1250"), 1250)

    def test_current_fixed_salary_replaces_stale_configured_breakdown(self):
        employee = {"employee_id": "emp001", "fixed_salary": 90000}

        with patch(
            "modules.payroll.calculations.SALARY_FIXED_PARTS",
            {"emp001": [{"label": "old", "amount": 35000}]},
        ):
            self.assertEqual(
                get_salary_fixed_parts(employee),
                [{"label": "оклад", "amount": 45000}],
            )

    def test_payroll_uses_current_hourly_rate_and_fixed_salary(self):
        employee = {
            "employee_id": "emp001",
            "full_name": "Сотрудник",
            "hourly_rate": 1250,
            "fixed_salary": 90000,
            "include_in_common_fund": False,
        }
        report = {
            "employee": {"employee_id": "emp001"},
            "hours": 8,
            "shift_type": "",
            "kpi_sum": 0,
        }
        with (
            patch("modules.payroll.calculations.get_employees", return_value=[employee]),
            patch("modules.payroll.calculations.get_reports_in_period", return_value=[report]),
            patch("modules.payroll.calculations.get_expenses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_penalties_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_bonuses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_vacations_in_period", return_value=[]),
            patch("modules.payroll.calculations.SALARY_FIXED_PARTS", {}),
        ):
            total = calculate_payroll_for_period("01.09.2026", "15.09.2026")["emp001"]

        self.assertEqual(total["hourly_pay"], 10000)
        self.assertEqual(total["fixed_half"], 45000)
        self.assertEqual(total["salary_without_expenses"], 55000)


if __name__ == "__main__":
    unittest.main()
