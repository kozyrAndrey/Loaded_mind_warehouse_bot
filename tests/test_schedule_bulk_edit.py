import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.schedule.handlers import (
    SCHEDULE_EDIT_DAY,
    SCHEDULE_EDIT_NEXT,
    bulk_edit_keyboard,
    schedule_bulk_edit_save,
    schedule_bulk_edit_selected,
)


class ScheduleBulkEditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.week_start = date(2026, 8, 10)
        self.employee = {"employee_id": "emp1", "full_name": "Сотрудник"}

    def test_keyboard_shows_checked_days_and_times(self):
        shifts = {"10.08.2026": "10:00", "11.08.2026": ""}
        keyboard = bulk_edit_keyboard(self.week_start, shifts)
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        self.assertIn("✅ ПН 10.08", labels)
        self.assertIn("⬜️ ВТ 11.08", labels)
        self.assertIn("10:00", labels)

    async def test_toggle_and_time_changes_stay_pending_until_save(self):
        query = SimpleNamespace(
            data="scheditbulk:toggle:10.08.2026",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={
                "edit_employee_id": "emp1",
                "schedule_week_start": "10.08.2026",
                "edit_pending_shifts": {"10.08.2026": "10:00"},
                "edit_preferred_times": {"10.08.2026": "10:00"},
            }
        )
        with patch("modules.schedule.handlers.get_schedule_employee_by_id", return_value=self.employee):
            state = await schedule_bulk_edit_selected(update, context)
        self.assertEqual(state, SCHEDULE_EDIT_DAY)
        self.assertEqual(context.user_data["edit_pending_shifts"]["10.08.2026"], "")

    async def test_save_applies_whole_week_and_removes_obsolete_duty(self):
        query = SimpleNamespace(from_user=SimpleNamespace(id=1), edit_message_text=AsyncMock())
        context = SimpleNamespace(
            user_data={
                "edit_original_shifts": {"10.08.2026": "10:00", "11.08.2026": "15:00"},
                "edit_pending_shifts": {"10.08.2026": "", "11.08.2026": "10:00"},
            }
        )
        with (
            patch("modules.schedule.handlers.find_employee_for_telegram_user", return_value={"full_name": "Менеджер"}),
            patch("modules.schedule.handlers.upsert_employee_week_schedule") as upsert_week,
            patch("modules.schedule.handlers.get_schedule_matrix", return_value=([], [], {}, {"10.08.2026": "emp1"})),
            patch("modules.schedule.handlers.set_duty_for_day") as set_duty,
            patch("modules.schedule.handlers.rebuild_current_schedule_sheet"),
        ):
            state = await schedule_bulk_edit_save(query, context, self.employee, self.week_start)
        self.assertEqual(state, SCHEDULE_EDIT_NEXT)
        upsert_week.assert_called_once()
        set_duty.assert_called_once()


if __name__ == "__main__":
    unittest.main()
