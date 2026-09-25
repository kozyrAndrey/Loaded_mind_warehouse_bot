import unittest
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules.tasks.storage import (
    assign_working_employees_to_unassigned_template_tasks,
    resolve_template_assignees,
)


class TaskTemplateAssignmentTests(unittest.TestCase):
    def test_templates_are_created_without_default_assignees(self):
        self.assertEqual(
            resolve_template_assignees({"Тип исполнителей": "working_today"}, date(2026, 7, 6)),
            [],
        )

    def test_auto_assignment_preserves_manual_assignees(self):
        manual_task = SimpleNamespace(
            template_id="template-1",
            assignee_ids="employee-manual",
            assignee_names="Руководитель назначил",
            updated_at=None,
        )
        empty_task = SimpleNamespace(
            template_id="template-2",
            assignee_ids="",
            assignee_names="",
            updated_at=None,
        )
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [manual_task, empty_task]

        @contextmanager
        def fake_session_scope():
            yield session

        with (
            patch("modules.tasks.storage.session_scope", fake_session_scope),
            patch(
                "modules.tasks.storage.get_working_employees_for_date",
                return_value=[{"employee_id": "employee-1", "full_name": "Иван"}],
            ),
        ):
            result = assign_working_employees_to_unassigned_template_tasks("06.07.2026")

        self.assertEqual(manual_task.assignee_ids, "employee-manual")
        self.assertEqual(empty_task.assignee_ids, "employee-1")
        self.assertEqual(empty_task.assignee_names, "Иван")
        self.assertEqual(result["updated"], 1)

    def test_auto_assignment_leaves_task_unassigned_when_nobody_works(self):
        task = SimpleNamespace(template_id="template-1", assignee_ids="", assignee_names="", updated_at=None)
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [task]

        @contextmanager
        def fake_session_scope():
            yield session

        with (
            patch("modules.tasks.storage.session_scope", fake_session_scope),
            patch("modules.tasks.storage.get_working_employees_for_date", return_value=[]),
        ):
            result = assign_working_employees_to_unassigned_template_tasks("06.07.2026")

        self.assertEqual(task.assignee_ids, "")
        self.assertEqual(task.assignee_names, "")
        self.assertEqual(result["working_count"], 0)


if __name__ == "__main__":
    unittest.main()
