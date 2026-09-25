import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from modules.tasks.handlers import (
    REG_ADD_TYPE,
    REG_ADD_WEEKDAY,
    REG_EDIT_FIELD,
    REG_EDIT_SELECT,
    REG_EDIT_WEEKDAY,
    REG_MANAGE_ACTION,
    regular_add_weekday_selected,
    regular_manage_action_selected,
    regular_manage_selected,
    regular_manage_start,
    regular_edit_weekday_selected,
    regular_view,
    regular_template_series_list,
    weekday_multiselect_keyboard,
)
from modules.tasks.formatting import format_regular_tasks_view


class TaskTemplateSeriesHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_template_view_is_sent_as_weekday_text_file(self):
        query = SimpleNamespace(
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(reply_document=AsyncMock()),
        )
        context = SimpleNamespace(user_data={"old": "value"})
        long_text = "📋 Шаблоны регулярных задач\n\n📅 Понедельник\n" + "Задача\n" * 700

        with (
            patch("modules.tasks.handlers.get_task_templates", return_value=[{"template_id": "1"}]),
            patch("modules.tasks.handlers.format_regular_tasks_view", return_value=long_text),
        ):
            state = await regular_view(SimpleNamespace(callback_query=query), context)

        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(context.user_data, {})
        query.message.reply_document.assert_awaited_once()
        document = query.message.reply_document.await_args.kwargs["document"]
        self.assertEqual(document.filename, "Шаблоны_регулярных_задач.txt")
        self.assertEqual(document.input_file_content, long_text.encode("utf-8"))
        self.assertIn("отправлен файлом", query.edit_message_text.await_args.args[0])

    def test_template_view_is_grouped_by_weekday(self):
        templates = [
            {
                "template_id": "tpl-mon",
                "series_id": "series-shipping",
                "weekday": 0,
                "Тип задачи": "warehouse",
                "Описание": "Отправки",
                "Тип исполнителей": "working_today",
                "Дедлайн": "18:00",
            },
            {
                "template_id": "tpl-wed",
                "series_id": "series-returns",
                "weekday": 2,
                "Тип задачи": "general",
                "Описание": "Разобрать возвраты",
                "Тип исполнителей": "none",
                "Дедлайн": "",
            },
            {
                "template_id": "tpl-fri",
                "series_id": "series-shipping",
                "weekday": 4,
                "Тип задачи": "warehouse",
                "Описание": "Отправки",
                "Тип исполнителей": "working_today",
                "Дедлайн": "18:00",
            },
        ]

        text = format_regular_tasks_view(templates)

        self.assertIn("📅 Понедельник\n1. Складская: Отправки", text)
        self.assertIn("📅 Среда\n1. Нескладская: Разобрать возвраты", text)
        self.assertIn("📅 Пятница\n1. Складская: Отправки", text)
        self.assertNotIn("дни:", text)
        self.assertLess(text.index("📅 Понедельник"), text.index("📅 Среда"))
        self.assertLess(text.index("📅 Среда"), text.index("📅 Пятница"))

    async def test_add_flow_toggles_several_weekdays_before_continuing(self):
        query = SimpleNamespace(
            data="regweekday:toggle:2",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"regular_weekdays": [0]})

        state = await regular_add_weekday_selected(
            SimpleNamespace(callback_query=query), context,
        )

        self.assertEqual(state, REG_ADD_WEEKDAY)
        self.assertEqual(context.user_data["regular_weekdays"], [0, 2])
        labels = [
            button.text
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("✅ Понедельник", labels)
        self.assertIn("✅ Среда", labels)

        query.data = "regweekday:done"
        state = await regular_add_weekday_selected(
            SimpleNamespace(callback_query=query), context,
        )
        self.assertEqual(state, REG_ADD_TYPE)

    async def test_edit_flow_saves_complete_weekday_set_once(self):
        query = SimpleNamespace(
            data="regeditweekday:done",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={
            "edit_template_id": "tpl-1",
            "edit_template_weekdays": [1, 3, 5],
        })

        with patch(
            "modules.tasks.handlers.replace_task_template_series_weekdays",
        ) as replace_weekdays:
            state = await regular_edit_weekday_selected(
                SimpleNamespace(callback_query=query), context,
            )

        replace_weekdays.assert_called_once_with("tpl-1", {1, 3, 5})
        self.assertNotEqual(state, REG_EDIT_WEEKDAY)
        self.assertEqual(context.user_data, {})
        self.assertIn("Вторник, Четверг, Суббота", query.edit_message_text.await_args.args[0])

    def test_multiselect_keyboard_has_one_save_action(self):
        keyboard = weekday_multiselect_keyboard("regweekday", [0, 6])
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(callbacks.count("regweekday:done"), 1)
        self.assertIn("regweekday:toggle:0", callbacks)
        self.assertIn("regweekday:toggle:6", callbacks)

    def test_series_list_contains_each_template_once_with_all_days(self):
        templates = [
            {"template_id": "tpl-mon", "series_id": "series-1", "weekday": 0, "Описание": "Отправки"},
            {"template_id": "tpl-thu", "series_id": "series-1", "weekday": 3, "Описание": "Отправки"},
            {"template_id": "tpl-fri", "series_id": "series-2", "weekday": 4, "Описание": "Инвентаризация"},
        ]

        with patch("modules.tasks.handlers.get_task_templates", return_value=templates):
            result = regular_template_series_list()

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["Описание"], "Отправки")
        self.assertEqual(result[0]["weekdays"], [0, 3])
        self.assertEqual(result[0]["Дни недели"], "Понедельник, Четверг")

    async def test_manage_starts_with_clickable_template_list_not_weekdays(self):
        query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
        context = SimpleNamespace(user_data={"old": "value"})
        templates = [{
            "template_id": "tpl-mon", "series_id": "series-1",
            "weekdays": [0, 3], "Описание": "Отправки",
        }]

        with patch(
            "modules.tasks.handlers.regular_template_series_list",
            return_value=templates,
        ):
            state = await regular_manage_start(
                SimpleNamespace(callback_query=query), context,
            )

        self.assertEqual(state, REG_EDIT_SELECT)
        callbacks = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        labels = [
            button.text
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("regmanage:tpl-mon", callbacks)
        self.assertFalse(any(callback.startswith("regeditday:") for callback in callbacks))
        self.assertIn("Отправки · Пн, Чт", labels)

    async def test_selected_template_offers_edit_and_delete_actions(self):
        template = {
            "template_id": "tpl-mon", "Описание": "Отправки",
            "Дни недели": "Понедельник, Четверг",
        }
        query = SimpleNamespace(
            data="regmanage:tpl-mon",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        with patch("modules.tasks.handlers.get_task_template_by_id", return_value=template):
            state = await regular_manage_selected(
                SimpleNamespace(callback_query=query), context,
            )

        self.assertEqual(state, REG_MANAGE_ACTION)
        self.assertEqual(context.user_data["edit_template_id"], "tpl-mon")
        callbacks = [
            button.callback_data
            for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("regmanageaction:edit", callbacks)
        self.assertIn("regmanageaction:delete", callbacks)

        query.data = "regmanageaction:edit"
        with patch("modules.tasks.handlers.get_task_template_by_id", return_value=template):
            state = await regular_manage_action_selected(
                SimpleNamespace(callback_query=query), context,
            )
        self.assertEqual(state, REG_EDIT_FIELD)


if __name__ == "__main__":
    unittest.main()
