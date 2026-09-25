import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modules.ai_agent.weather import setup_ai_agent_jobs
from core.keyboards import build_main_menu_keyboard
from core.module_control import MODULES
from modules.employees.handlers import cancel_keyboard as employee_navigation_keyboard
from modules.payroll.handlers import payroll_back_keyboard
from modules.products.handlers import PRODUCT_ADD_GTIN, product_add_back
from modules.recruitment.handlers import build_confirm_keyboard
from modules.schedule.handlers import (
    SCHEDULE_SELECT_TIME,
    schedule_back,
    setup_schedule_jobs,
)
from modules.tasks.handlers import deadline_keyboard, setup_tasks_jobs


def callback_values(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_daily(self, callback, **kwargs):
        self.jobs.append((callback, kwargs))


class MorningJobsTests(unittest.TestCase):
    def test_daily_messages_are_scheduled_one_hour_earlier(self):
        task_queue = FakeJobQueue()
        setup_tasks_jobs(SimpleNamespace(job_queue=task_queue))
        task_times = {kwargs["name"]: kwargs["time"] for _, kwargs in task_queue.jobs}
        self.assertEqual((task_times["daily_staff_message"].hour, task_times["daily_staff_message"].minute), (9, 30))
        self.assertEqual((task_times["template_task_auto_assignment"].hour, task_times["template_task_auto_assignment"].minute), (9, 30))
        self.assertEqual((task_times["daily_tasks_export"].hour, task_times["daily_tasks_export"].minute), (9, 35))

        weather_queue = FakeJobQueue()
        setup_ai_agent_jobs(SimpleNamespace(job_queue=weather_queue))
        weather_time = weather_queue.jobs[0][1]["time"]
        self.assertEqual((weather_time.hour, weather_time.minute), (9, 40))

    def test_friday_schedule_reminders_start_at_nine(self):
        queue = FakeJobQueue()
        setup_schedule_jobs(SimpleNamespace(job_queue=queue))
        reminder_hours = [
            kwargs["time"].hour
            for _, kwargs in queue.jobs
            if kwargs["name"].startswith("schedule_missing_reminder_")
        ]
        self.assertEqual(reminder_hours, [9, 11, 13, 15, 17])
        overdue = next(kwargs["time"] for _, kwargs in queue.jobs if kwargs["name"] == "schedule_manager_overdue")
        self.assertEqual((overdue.hour, overdue.minute), (19, 0))


class NavigationKeyboardTests(unittest.TestCase):
    def test_hidden_modules_are_absent_for_employees_and_managers(self):
        hidden_callbacks = {
            "section:receiving",
            "section:returns",
            "section:lamoda",
        }
        for manager in (False, True):
            callbacks = callback_values(
                build_main_menu_keyboard(
                    manager=manager,
                    enabled_modules=set(MODULES),
                )
            )
            self.assertTrue(hidden_callbacks.isdisjoint(callbacks))
            self.assertEqual(callbacks, ["menu:start"])

    def test_major_wizards_expose_previous_step_callbacks(self):
        self.assertIn("empback:phone", callback_values(employee_navigation_keyboard("phone")))
        self.assertIn("payback:create_hours", callback_values(payroll_back_keyboard("create_hours")))
        self.assertIn("taskback:add_assignees", callback_values(deadline_keyboard("add_assignees")))
        self.assertIn("recruit:back:hours", callback_values(build_confirm_keyboard()))


class StatefulNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_product_gtin_back_removes_only_last_size_value(self):
        query = SimpleNamespace(
            data="prodback:gtin",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "product_add": {
                    "sizes": ["S", "M", "L"],
                    "marking": [
                        {"size": "S", "gtin": "1"},
                        {"size": "M", "gtin": "2"},
                    ],
                }
            }
        )

        state = await product_add_back(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, PRODUCT_ADD_GTIN)
        self.assertEqual(context.user_data["product_add"]["marking"], [{"size": "S", "gtin": "1"}])
        self.assertEqual(context.user_data["product_add"]["marking_index"], 1)

    async def test_schedule_confirm_back_reopens_only_last_shift(self):
        query = SimpleNamespace(
            data="sch:back:confirm",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "schedule_week_start": "04.08.2026",
                "schedule_selected_dates": ["04.08.2026", "05.08.2026"],
                "schedule_current_time_index": 2,
                "schedule_shifts": {
                    "04.08.2026": "10:00",
                    "05.08.2026": "15:00",
                },
            }
        )

        with patch("modules.schedule.handlers.day_label", side_effect=lambda value: value):
            state = await schedule_back(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, SCHEDULE_SELECT_TIME)
        self.assertEqual(context.user_data["schedule_current_time_index"], 1)
        self.assertEqual(context.user_data["schedule_shifts"], {"04.08.2026": "10:00"})


if __name__ == "__main__":
    unittest.main()
