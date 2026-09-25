import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from modules.consumables.storage import (
    LABEL_58_40,
    ConsumableItem,
    ProductConsumableRule,
    deactivate_consumable_item,
    seed_default_consumable_items,
    seed_default_product_consumable_rules,
)
from modules.storage.postgres import Base


class ConsumablesItemDeletionStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(
            self.engine,
            tables=[ConsumableItem.__table__, ProductConsumableRule.__table__],
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

    def test_deactivation_preserves_item_and_disables_rules_after_reseeding(self):
        seed_default_consumable_items()
        seed_default_product_consumable_rules()

        with self.session_scope() as session:
            item = session.execute(
                select(ConsumableItem).where(ConsumableItem.name == LABEL_58_40)
            ).scalar_one()
            item_id = item.item_id
            active_rules = session.execute(
                select(ProductConsumableRule).where(
                    ProductConsumableRule.item_id == item_id,
                    ProductConsumableRule.is_active.is_(True),
                )
            ).scalars().all()
            self.assertTrue(active_rules)

        deleted_item = deactivate_consumable_item(item_id)
        self.assertFalse(deleted_item["is_active"])

        seed_default_consumable_items()
        seed_default_product_consumable_rules()

        with self.session_scope() as session:
            item = session.get(ConsumableItem, item_id)
            active_rule_count = len(
                session.execute(
                    select(ProductConsumableRule).where(
                        ProductConsumableRule.item_id == item_id,
                        ProductConsumableRule.is_active.is_(True),
                    )
                ).scalars().all()
            )

        self.assertIsNotNone(item)
        self.assertFalse(item.is_active)
        self.assertEqual(active_rule_count, 0)


if __name__ == "__main__":
    unittest.main()
