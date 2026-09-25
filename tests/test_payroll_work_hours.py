import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.payroll.handlers import (
    CREATE_INTERVAL,
    CREATE_LUNCH,
    CREATE_TASKS,
    calculate_worked_hours,
    create_interval_received,
    create_lunch_selected,
    parse_work_interval,
)


class WorkIntervalTests(unittest.TestCase):
    def test_interval_is_normalized_and_lunch_is_subtracted(self):
        self.assertEqual(parse_work_interval("10:00 – 19:00"), ("10:00-19:00", 9))
        self.assertEqual(calculate_worked_hours("10:00-19:00", 1), 8)
        self.assertEqual(calculate_worked_hours("13:00-20:00", 0.5), 6.5)

    def test_interval_must_use_half_hour_step(self):
        self.assertIsNone(parse_work_interval("10:00-19:20"))

    def test_interval_can_cross_midnight(self):
        self.assertEqual(parse_work_interval("10:00-02:00"), ("10:00-02:00", 16))
        self.assertEqual(calculate_worked_hours("10:00-02:00", 1), 15)


class DailyReportHoursFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_interval_step_leads_to_lunch_choice(self):
        message = SimpleNamespace(text="10:00-19:00", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        state = await create_interval_received(update, context)

        self.assertEqual(state, CREATE_LUNCH)
        self.assertEqual(context.user_data["interval"], "10:00-19:00")

    async def test_lunch_selection_calculates_hours(self):
        query = SimpleNamespace(
            data="crlunch:1",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={"interval": "10:00-19:00"})

        state = await create_lunch_selected(update, context)

        self.assertEqual(state, CREATE_TASKS)
        self.assertEqual(context.user_data["lunch_hours"], 1)
        self.assertEqual(context.user_data["hours"], 8)

    async def test_invalid_interval_stays_on_interval_step(self):
        message = SimpleNamespace(text="10:00-19:20", reply_text=AsyncMock())
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        state = await create_interval_received(update, context)

        self.assertEqual(state, CREATE_INTERVAL)


if __name__ == "__main__":
    unittest.main()
