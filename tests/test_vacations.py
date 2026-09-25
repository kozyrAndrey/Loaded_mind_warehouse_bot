import unittest
from types import SimpleNamespace
from unittest.mock import patch

from telegram.ext import ConversationHandler

from modules.payroll.calculations import calculate_payroll_for_period
from modules.payroll.handlers import vacations_menu_start
from modules.payroll.vacations import vacation_amount_for_period


class VacationCalculationTests(unittest.TestCase):
    def test_vacation_amount_uses_inclusive_days_and_saved_rate(self):
        vacation = {
            "start_date": "01.07.2026",
            "end_date": "03.07.2026",
            "hourly_rate": 250,
        }

        days, amount = vacation_amount_for_period(vacation, "02.07.2026", "10.07.2026")

        self.assertEqual(days, 2)
        self.assertEqual(amount, 4000)

    def test_vacation_pay_is_added_without_reducing_fixed_salary(self):
        employee = {
            "employee_id": "employee-1",
            "full_name": "Тестовый сотрудник",
            "hourly_rate": 500,
            "fixed_salary": 20000,
        }
        vacation = {
            "employee_id": "employee-1",
            "start_date": "01.07.2026",
            "end_date": "01.07.2026",
            "hourly_rate": 200,
        }
        with (
            patch("modules.payroll.calculations.get_employees", return_value=[employee]),
            patch("modules.payroll.calculations.get_reports_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_expenses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_penalties_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_bonuses_in_period", return_value=[]),
            patch("modules.payroll.calculations.get_vacations_in_period", return_value=[vacation]),
            patch("modules.payroll.calculations.SALARY_FIXED_PARTS", {}),
        ):
            total = calculate_payroll_for_period("01.07.2026", "15.07.2026")["employee-1"]

        self.assertEqual(total["fixed_half"], 10000)
        self.assertEqual(total["vacation_days"], 1)
        self.assertEqual(total["vacation_pay"], 1600)
        self.assertEqual(total["salary_without_expenses"], 11600)


class VacationHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_vacation_management_is_manager_only(self):
        from unittest.mock import AsyncMock

        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        with patch("modules.payroll.handlers.ensure_vacation_manager", return_value=False):
            state = await vacations_menu_start(SimpleNamespace(callback_query=query), SimpleNamespace(user_data={}))

        self.assertEqual(state, ConversationHandler.END)
        query.edit_message_text.assert_awaited_once_with("Недостаточно прав.")


if __name__ == "__main__":
    unittest.main()
