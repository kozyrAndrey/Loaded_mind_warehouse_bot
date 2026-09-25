import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.payroll.calculations import calculate_payroll_for_period
from modules.payroll.google_sheets import (
    PAYMENT_MODE_HOURLY,
    PAYMENT_MODE_SHIFT,
    SHIFT_TYPE_FULL,
    SHIFT_TYPE_HALF,
    calculate_daily_salary_total,
    get_period_for_date,
)
from modules.payroll.handlers import (
    CREATE_INTERVAL,
    CREATE_SHIFT_TYPE,
    EDIT_FIELD,
    create_date_selected,
    edit_shift_type_selected,
)


def employee():
    return {
        "employee_id": "employee-1",
        "full_name": "Сотрудник",
        "hourly_rate": 500,
        "fixed_salary": 0,
        "include_in_common_fund": True,
    }


def empty_payroll_dependencies(reports):
    return (
        patch("modules.payroll.calculations.get_employees", return_value=[employee()]),
        patch("modules.payroll.calculations.get_reports_in_period", return_value=reports),
        patch("modules.payroll.calculations.get_expenses_in_period", return_value=[]),
        patch("modules.payroll.calculations.get_penalties_in_period", return_value=[]),
        patch("modules.payroll.calculations.get_bonuses_in_period", return_value=[]),
        patch("modules.payroll.calculations.get_vacations_in_period", return_value=[]),
        patch("modules.payroll.calculations.SALARY_FIXED_PARTS", {}),
    )


class PayrollShiftCalculationTests(unittest.TestCase):
    def test_hourly_period_uses_actual_hours(self):
        reports = [
            {
                "employee": employee(),
                "hours": 6.5,
                "shift_type": SHIFT_TYPE_HALF,
                "kpi_sum": 100,
            }
        ]
        dependencies = empty_payroll_dependencies(reports)
        with dependencies[0], dependencies[1], dependencies[2], dependencies[3], dependencies[4], dependencies[5], dependencies[6]:
            total = calculate_payroll_for_period(
                "01.08.2026",
                "15.08.2026",
                PAYMENT_MODE_HOURLY,
            )["employee-1"]

        self.assertEqual(total["hours"], 6.5)
        self.assertEqual(total["paid_hours"], 6.5)
        self.assertEqual(total["hourly_pay"], 3250)
        self.assertEqual(total["warehouse_gross"], 3350)

    def test_shift_period_uses_explicit_full_and_half_shift_types(self):
        reports = [
            {
                "employee": employee(),
                "hours": 6.5,
                "shift_type": SHIFT_TYPE_FULL,
                "kpi_sum": 100,
            },
            {
                "employee": employee(),
                "hours": 9,
                "shift_type": SHIFT_TYPE_HALF,
                "kpi_sum": 200,
            },
        ]
        dependencies = empty_payroll_dependencies(reports)
        with dependencies[0], dependencies[1], dependencies[2], dependencies[3], dependencies[4], dependencies[5], dependencies[6]:
            total = calculate_payroll_for_period(
                "01.08.2026",
                "15.08.2026",
                PAYMENT_MODE_SHIFT,
            )["employee-1"]

        self.assertEqual(total["hours"], 15.5)
        self.assertEqual(total["paid_hours"], 12)
        self.assertEqual(total["full_shifts"], 1)
        self.assertEqual(total["half_shifts"], 1)
        self.assertEqual(total["hourly_pay"], 6000)
        self.assertEqual(total["warehouse_gross"], 6300)

    def test_legacy_shift_report_without_type_is_full_shift(self):
        reports = [
            {
                "employee": employee(),
                "hours": 3,
                "shift_type": "",
                "kpi_sum": 0,
            }
        ]
        dependencies = empty_payroll_dependencies(reports)
        with dependencies[0], dependencies[1], dependencies[2], dependencies[3], dependencies[4], dependencies[5], dependencies[6]:
            total = calculate_payroll_for_period(
                "01.08.2026",
                "15.08.2026",
                PAYMENT_MODE_SHIFT,
            )["employee-1"]

        self.assertEqual(total["paid_hours"], 8)
        self.assertEqual(total["full_shifts"], 1)
        self.assertEqual(total["half_shifts"], 0)

    def test_daily_total_uses_paid_hours_but_not_statistical_hours(self):
        self.assertEqual(
            calculate_daily_salary_total(
                employee(),
                hours=2.5,
                kpi_items=[{"qty": 2, "rate": 25}],
                payment_mode=PAYMENT_MODE_SHIFT,
                shift_type=SHIFT_TYPE_HALF,
            ),
            2050,
        )

    def test_period_is_resolved_by_report_date(self):
        periods = [
            {
                "period_id": "old",
                "start_date": "16.07.2026",
                "end_date": "31.07.2026",
                "payment_mode": PAYMENT_MODE_HOURLY,
            },
            {
                "period_id": "new",
                "start_date": "01.08.2026",
                "end_date": "15.08.2026",
                "payment_mode": PAYMENT_MODE_SHIFT,
            },
        ]
        with patch("modules.payroll.google_sheets.get_periods", return_value=periods):
            self.assertEqual(get_period_for_date("31.07.2026")["period_id"], "old")
            self.assertEqual(get_period_for_date("01.08.2026")["period_id"], "new")
            self.assertIsNone(get_period_for_date("16.08.2026"))


class PayrollShiftHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_hourly_period_keeps_original_interval_flow(self):
        query = SimpleNamespace(
            data="crdate:31.07.2026",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
        )
        context = SimpleNamespace(user_data={"employee_id": "employee-1"})

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=employee()),
            patch("modules.payroll.handlers.find_report_row", return_value=(None, None)),
            patch(
                "modules.payroll.handlers.get_period_for_date",
                return_value={"payment_mode": PAYMENT_MODE_HOURLY},
            ),
        ):
            state = await create_date_selected(update, context)

        self.assertEqual(state, CREATE_INTERVAL)
        self.assertIn(
            "Введите рабочий временной промежуток",
            query.edit_message_text.await_args.args[0],
        )

    async def test_shift_period_requests_explicit_shift_type(self):
        query = SimpleNamespace(
            data="crdate:01.08.2026",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
        )
        context = SimpleNamespace(user_data={"employee_id": "employee-1"})
        period = {
            "start_date": "01.08.2026",
            "end_date": "15.08.2026",
            "payment_mode": PAYMENT_MODE_SHIFT,
        }

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=employee()),
            patch("modules.payroll.handlers.find_report_row", return_value=(None, None)),
            patch("modules.payroll.handlers.get_period_for_date", return_value=period),
        ):
            state = await create_date_selected(update, context)

        self.assertEqual(state, CREATE_SHIFT_TYPE)
        self.assertEqual(context.user_data["report_period"], period)
        callbacks = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("crshift:full", callbacks)
        self.assertIn("crshift:half", callbacks)

    async def test_report_is_blocked_when_period_is_missing(self):
        query = SimpleNamespace(
            data="crdate:16.08.2026",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=7),
        )
        context = SimpleNamespace(user_data={"employee_id": "employee-1"})

        with (
            patch("modules.payroll.handlers.get_employee_by_id", return_value=employee()),
            patch("modules.payroll.handlers.find_report_row", return_value=(None, None)),
            patch("modules.payroll.handlers.get_period_for_date", return_value=None),
            patch("modules.payroll.handlers.current_employee_or_none", return_value=employee()),
        ):
            state = await create_date_selected(update, context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertIn("не настроен расчетный период", query.edit_message_text.await_args.args[0])

    async def test_shift_type_edit_does_not_change_actual_hours(self):
        query = SimpleNamespace(
            data="edshift:half",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        report_data = {
            "Дата": "01.08.2026",
            "employee_id": "employee-1",
            "ФИО": "Сотрудник",
            "Рабочий промежуток": "10:00-18:00",
            "Отработано часов": 6.5,
            "Тип смены": SHIFT_TYPE_FULL,
            "Задачи": "Работа",
            "KPI данные": "[]",
            "KPI сумма": 0,
        }
        context = SimpleNamespace(
            user_data={
                "edit_period": {"payment_mode": PAYMENT_MODE_SHIFT},
                "edit_report_data": report_data,
            }
        )

        with (
            patch("modules.payroll.google_sheets.get_employee_by_id", return_value=employee()),
            patch(
                "modules.payroll.google_sheets.get_period_for_date",
                return_value={"payment_mode": PAYMENT_MODE_SHIFT},
            ),
        ):
            state = await edit_shift_type_selected(
                SimpleNamespace(callback_query=query),
                context,
            )

        self.assertEqual(state, EDIT_FIELD)
        self.assertEqual(report_data["Тип смены"], SHIFT_TYPE_HALF)
        self.assertEqual(report_data["Отработано часов"], 6.5)


if __name__ == "__main__":
    unittest.main()
