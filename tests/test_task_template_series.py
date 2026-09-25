import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from modules.storage.postgres import Base
from modules.tasks.storage import (
    TaskTemplate,
    create_task_template_series,
    delete_task_template,
    ensure_task_template_series_ids,
    get_task_template_by_id,
    get_task_templates,
    replace_task_template_series_weekdays,
    update_task_template_fields,
)


class TaskTemplateSeriesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine, tables=[TaskTemplate.__table__])
        self.factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True,
        )
        self.patch = patch(
            "modules.tasks.storage.session_scope", new=self.session_scope,
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()

    @contextmanager
    def session_scope(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def test_create_edit_days_and_delete_operate_on_whole_series(self):
        created = create_task_template_series(
            [0, 2, 4], "general", "Проверить остатки", deadline="18:00",
        )
        anchor_id = created["template_ids"][0]

        templates = get_task_templates()
        self.assertEqual([row["weekday"] for row in templates], [0, 2, 4])
        self.assertEqual({row["series_id"] for row in templates}, {created["series_id"]})

        update_task_template_fields(anchor_id, **{"Описание": "Новый текст"})
        self.assertEqual({row["Описание"] for row in get_task_templates()}, {"Новый текст"})

        replace_task_template_series_weekdays(anchor_id, [1, 4, 6])
        templates = get_task_templates()
        self.assertEqual([row["weekday"] for row in templates], [1, 4, 6])
        self.assertEqual({row["Описание"] for row in templates}, {"Новый текст"})
        self.assertEqual(get_task_template_by_id(templates[0]["template_id"])["weekdays"], [1, 4, 6])

        delete_task_template(templates[1]["template_id"])
        self.assertEqual(get_task_templates(), [])

    def test_legacy_identical_rows_are_grouped_without_merging_duplicates_on_same_day(self):
        with self.session_scope() as session:
            for template_id, weekday in (("a-monday", 0), ("b-wednesday", 2), ("z-monday", 0)):
                session.add(TaskTemplate(
                    template_id=template_id,
                    series_id="",
                    weekday_name=("Понедельник" if weekday == 0 else "Среда"),
                    weekday=weekday,
                    task_type="general",
                    description="Контроль производства",
                    assignee_mode="none",
                    deadline="",
                ))

        self.assertEqual(ensure_task_template_series_ids(), 3)
        templates = {row["template_id"]: row for row in get_task_templates()}
        self.assertEqual(templates["a-monday"]["series_id"], templates["b-wednesday"]["series_id"])
        self.assertNotEqual(templates["a-monday"]["series_id"], templates["z-monday"]["series_id"])


if __name__ == "__main__":
    unittest.main()
