import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from modules.consumables.storage import (
    ConsumableInventoryCount,
    ConsumableInventorySession,
    ConsumableInventorySessionChange,
    ConsumableInventorySessionItem,
    ConsumableInventorySessionParticipant,
    ConsumableItem,
    ConsumableMovement,
    apply_inventory_session,
    complete_inventory_session,
    get_or_create_active_inventory_session,
    return_inventory_session_to_draft,
    update_inventory_review_item,
)
from modules.storage.postgres import Base


class ConsumablesInventoryApprovalStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(
            self.engine,
            tables=[
                ConsumableItem.__table__,
                ConsumableMovement.__table__,
                ConsumableInventoryCount.__table__,
                ConsumableInventorySession.__table__,
                ConsumableInventorySessionItem.__table__,
                ConsumableInventorySessionParticipant.__table__,
                ConsumableInventorySessionChange.__table__,
            ],
        )
        self.factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        self.storage_patch = patch(
            "modules.consumables.storage.session_scope",
            new=self.session_scope,
        )
        self.storage_patch.start()

        with self.session_scope() as session:
            session.add_all(
                [
                    ConsumableItem(
                        item_id=1,
                        name="Коробки",
                        unit="шт",
                        current_quantity=10,
                        is_active=True,
                    ),
                    ConsumableItem(
                        item_id=2,
                        name="Пакеты",
                        unit="шт",
                        current_quantity=20,
                        is_active=True,
                    ),
                    ConsumableInventorySession(
                        session_id="inventory-test",
                        status="draft",
                        created_by_user_id="7",
                        created_by_name="Сотрудник",
                    ),
                    ConsumableInventorySessionItem(
                        session_id="inventory-test",
                        item_id=1,
                        system_quantity=10,
                        counted_quantity=7,
                        counted_by_user_id="7",
                        counted_by_name="Сотрудник",
                    ),
                    ConsumableInventorySessionItem(
                        session_id="inventory-test",
                        item_id=2,
                        system_quantity=20,
                        counted_quantity=None,
                    ),
                ]
            )

    def tearDown(self):
        self.storage_patch.stop()
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

    def scalar(self, statement):
        with self.session_scope() as session:
            return session.execute(statement).scalar_one()

    def test_submit_does_not_change_stock_and_blocks_next_inventory(self):
        result = complete_inventory_session("inventory-test", "7", "Сотрудник")

        self.assertEqual(result["session"]["status"], "pending_review")
        self.assertEqual(self.scalar(select(ConsumableItem.current_quantity).where(ConsumableItem.item_id == 1)), 10)
        self.assertEqual(self.scalar(select(func.count()).select_from(ConsumableMovement)), 0)
        self.assertEqual(self.scalar(select(func.count()).select_from(ConsumableInventoryCount)), 0)

        with self.assertRaisesRegex(RuntimeError, "ожидает проверки"):
            get_or_create_active_inventory_session("8", "Другой сотрудник")

    def test_manager_can_edit_and_apply_only_counted_positions_once(self):
        complete_inventory_session("inventory-test", "7", "Сотрудник")
        update_inventory_review_item(
            "inventory-test",
            1,
            8,
            "42",
            "Руководитель",
        )

        result = apply_inventory_session(
            "inventory-test",
            "42",
            "Руководитель",
        )

        self.assertEqual(result["session"]["status"], "applied")
        self.assertEqual(self.scalar(select(ConsumableItem.current_quantity).where(ConsumableItem.item_id == 1)), 8)
        self.assertEqual(self.scalar(select(ConsumableItem.current_quantity).where(ConsumableItem.item_id == 2)), 20)
        self.assertEqual(self.scalar(select(func.count()).select_from(ConsumableMovement)), 1)
        self.assertEqual(self.scalar(select(func.count()).select_from(ConsumableInventoryCount)), 1)

        with self.assertRaisesRegex(RuntimeError, "уже применен"):
            apply_inventory_session("inventory-test", "42", "Руководитель")

    def test_rejected_inventory_returns_to_same_draft(self):
        complete_inventory_session("inventory-test", "7", "Сотрудник")

        self.assertTrue(return_inventory_session_to_draft("inventory-test"))

        inventory_session = get_or_create_active_inventory_session("7", "Сотрудник")
        self.assertEqual(inventory_session["session_id"], "inventory-test")
        self.assertEqual(inventory_session["status"], "draft")


if __name__ == "__main__":
    unittest.main()
