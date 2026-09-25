import unittest

from modules.schedule.config import SHIFT_TIMES
from modules.schedule.handlers import shift_time_keyboard


class ScheduleShiftTimeTests(unittest.TestCase):
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
