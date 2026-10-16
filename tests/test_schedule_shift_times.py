import unittest

from modules.schedule.config import SHIFT_TIMES
from modules.schedule.handlers import schedule_menu_keyboard, shift_time_keyboard


class ScheduleShiftTimeTests(unittest.TestCase):
    def test_employee_menu_allows_own_schedule_only(self):
        employee = {
            "role": "warehouse_employee",
            "roles": ["warehouse_employee"],
            "is_active": True,
        }

        callbacks = [
            button.callback_data
            for row in schedule_menu_keyboard(employee).inline_keyboard
            for button in row
        ]

        self.assertIn("sch:create", callbacks)
        self.assertNotIn("sch:edit", callbacks)
        self.assertNotIn("sch:view_all", callbacks)
        self.assertNotIn("sch:export_topic", callbacks)
        self.assertNotIn("sch:duties", callbacks)

    def test_schedule_offers_required_shift_times(self):
        self.assertEqual(SHIFT_TIMES, ["10:00", "13:00", "14:00", "15:00"])

        keyboard = shift_time_keyboard("07.09.2026")
        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data.startswith("schtime:")
        ]
        self.assertEqual([button.text for button in buttons], SHIFT_TIMES)
        self.assertEqual(
            [button.callback_data for button in buttons],
            [
                "schtime:07.09.2026:1000",
                "schtime:07.09.2026:1300",
                "schtime:07.09.2026:1400",
                "schtime:07.09.2026:1500",
            ],
        )


if __name__ == "__main__":
    unittest.main()
