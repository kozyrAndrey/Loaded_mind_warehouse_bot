from datetime import date
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.schedule.handlers import schedule_confirm


def warehouse_employee(employee_id="employee-1"):
    return {
        "employee_id": employee_id,
        "full_name": "Сотрудник склада",
        "roles": ["warehouse_employee"],
        "role": "warehouse_employee",
        "is_active": True,
    }


class ScheduleEmployeeSubmissionTests(IsolatedAsyncioTestCase):
    async def test_employee_cannot_save_second_submission_for_same_week(self):
        employee = warehouse_employee()
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(
            user_data={
                "schedule_employee_id": employee["employee_id"],
                "schedule_week_start": "19.10.2026",
                "schedule_shifts": {"19.10.2026": "10:00"},
            }
        )

        with (
            patch("modules.schedule.handlers.current_employee", return_value=employee),
            patch("modules.schedule.handlers.get_employee_by_id", return_value=employee),
            patch("modules.schedule.handlers.schedule_has_submission", return_value=True),
            patch("modules.schedule.handlers.upsert_employee_week_schedule") as save_schedule,
        ):
            state = await schedule_confirm(update, context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(context.user_data, {})
        save_schedule.assert_not_called()
        self.assertIn("уже отправили расписание", query.edit_message_text.await_args.args[0])

    async def test_employee_can_save_first_submission_for_own_schedule(self):
        employee = warehouse_employee()
        shifts = {"19.10.2026": "10:00"}
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(
            user_data={
                "schedule_employee_id": employee["employee_id"],
                "schedule_week_start": "19.10.2026",
                "schedule_shifts": shifts,
            }
        )

        with (
            patch("modules.schedule.handlers.current_employee", return_value=employee),
            patch("modules.schedule.handlers.get_employee_by_id", return_value=employee),
            patch("modules.schedule.handlers.schedule_has_submission", return_value=False),
            patch("modules.schedule.handlers.upsert_employee_week_schedule") as save_schedule,
        ):
            state = await schedule_confirm(update, context)

        self.assertEqual(state, ConversationHandler.END)
        save_schedule.assert_called_once_with(
            employee,
            date(2026, 10, 19),
            shifts,
            "Сотрудник склада",
        )
        self.assertEqual(context.user_data, {})

    async def test_employee_cannot_save_another_employees_schedule(self):
        caller = warehouse_employee("employee-1")
        target = warehouse_employee("employee-2")
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=42),
        )
        context = SimpleNamespace(
            user_data={
                "schedule_employee_id": target["employee_id"],
                "schedule_week_start": "19.10.2026",
                "schedule_shifts": {},
            }
        )

        with (
            patch("modules.schedule.handlers.current_employee", return_value=caller),
            patch("modules.schedule.handlers.get_employee_by_id", return_value=target),
            patch("modules.schedule.handlers.upsert_employee_week_schedule") as save_schedule,
        ):
            state = await schedule_confirm(update, context)

        self.assertEqual(state, ConversationHandler.END)
        save_schedule.assert_not_called()
        self.assertIn("Недостаточно прав", query.edit_message_text.await_args.args[0])
