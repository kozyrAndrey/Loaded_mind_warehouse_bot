import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from modules.shipping.storage import (
    ShippingCode,
    ShippingExportBatch,
    ShippingExportItem,
    confirm_export_retired,
    create_export_snapshot,
    fail_export_snapshot,
    save_shipping,
)


class ShippingStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        ShippingExportBatch.metadata.create_all(
            self.engine,
            tables=[ShippingCode.__table__, ShippingExportBatch.__table__, ShippingExportItem.__table__],
        )

        @contextmanager
        def test_session_scope():
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        self.session_patch = patch("modules.shipping.storage.session_scope", test_session_scope)
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()
        self.engine.dispose()

    @staticmethod
    def code(price="1000.00"):
        return {
            "short_code": "010460123456789321ABCDEFGHIJKLM",
            "gtin": "04601234567893",
            "price": price,
            "position_id": "position-1",
            "assortment_id": "product-1",
            "product_name": "Куртка",
        }

    def test_repeat_code_updates_record_instead_of_creating_duplicate(self):
        save_shipping({"id": "o1", "name": "mind-1"}, [self.code()], 1, "Первый")
        save_shipping({"id": "o2", "name": "mind-2"}, [self.code("1200.00")], 2, "Второй")

        with Session(self.engine) as session:
            rows = session.scalars(select(ShippingCode)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].order_name, "mind-2")
            self.assertEqual(rows[0].revision, 2)

    def test_old_export_does_not_delete_code_reused_after_snapshot(self):
        save_shipping({"id": "o1", "name": "mind-1"}, [self.code()], 1, "Первый")
        snapshot = create_export_snapshot(1, "Первый")
        save_shipping({"id": "o2", "name": "mind-2"}, [self.code("1200.00")], 2, "Второй")

        result = confirm_export_retired(snapshot["batch_id"], 1, "Первый")

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["preserved"], 1)
        with Session(self.engine) as session:
            self.assertEqual(len(session.scalars(select(ShippingCode)).all()), 1)

    def test_confirmation_deletes_snapshot_and_is_idempotent(self):
        save_shipping({"id": "o1", "name": "mind-1"}, [self.code()], 1, "Первый")
        snapshot = create_export_snapshot(1, "Первый")

        first = confirm_export_retired(snapshot["batch_id"], 1, "Первый")
        second = confirm_export_retired(snapshot["batch_id"], 1, "Первый")

        self.assertEqual(first["deleted"], 1)
        self.assertTrue(second["already_confirmed"])
        with Session(self.engine) as session:
            self.assertEqual(session.scalars(select(ShippingCode)).all(), [])

    def test_failed_file_delivery_removes_batch_from_pending_exports(self):
        save_shipping({"id": "o1", "name": "mind-1"}, [self.code()], 1, "Первый")
        snapshot = create_export_snapshot(1, "Первый")

        self.assertTrue(fail_export_snapshot(snapshot["batch_id"]))
        with self.assertRaisesRegex(ValueError, "недоступна"):
            confirm_export_retired(snapshot["batch_id"], 1, "Первый")


if __name__ == "__main__":
    unittest.main()
