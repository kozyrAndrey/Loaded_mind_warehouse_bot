import unittest
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from modules.lamoda_fbs.constants import CodeState, PackState
from modules.lamoda_fbs.marking import parse_marking_code
from modules.lamoda_fbs.services import (
    assembly_label_documents,
    assembly_picking_document,
    create_lamoda_shipment,
    discover_orders,
    match_pallets,
    prepare_orders,
    resolve_return_item_barcode,
)
from modules.lamoda_fbs.client import LamodaDocumentError, LamodaTemporaryError, LamodaValidationError
from modules.lamoda_fbs.storage import (
    LAMODA_TABLES,
    AssemblySession,
    LamodaPack,
    MarkingCode,
    add_pack_to_cargo,
    assign_marking_code,
    attach_packs,
    cancel_marking_batch,
    cargo_manifest,
    code_fingerprint,
    confirm_marking_batch,
    complete_pack_without_marking,
    create_assembly_session,
    create_cargo_place,
    create_marking_batch,
    get_next_unscanned_pack,
    get_session_packs,
    mark_pack_barcode_scanned,
    marking_batch_rows,
    persist_order,
    record_return_receipt,
    save_shipment,
    set_cargo_status,
    set_order_preparation,
    update_pack_lamoda_status,
    validate_cargo_complete,
)
from modules.storage.postgres import Base


def one_page_pdf(text):
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(20, 800, text)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


class FakeLamodaClient:
    seller_id = "120528732"

    def __init__(self):
        self.assemblies = []
        self.pack_counts = []
        self.item_label_batches = []
        self.pack_label_batches = []
        self.pdf = one_page_pdf("label")
        self.order_statuses = {}

    async def create_assembly(self, order_id, packs):
        self.assemblies.append((order_id, packs))
        return {}

    async def create_packs(self, order_id, count):
        self.pack_counts.append(count)
        return {"packs": [{"packNumber": f"PACK-{index:03d}"} for index in range(count, 0, -1)]}

    async def existing_order_pack_numbers(self, order_id):
        return []

    async def get_order(self, order_id):
        return {
            "id": str(order_id), "orderId": str(order_id),
            "status": self.order_statuses.get(str(order_id), "AWAITING_SHIPMENT"),
        }

    async def list_return_items(self, **params):
        return []

    async def order_item_labels(self, values, label_format):
        self.item_label_batches.append((list(values), label_format))
        return {"fileUrl": f"https://files.test/item-{len(self.item_label_batches)}.pdf", "excludedItems": []}

    async def order_pack_labels(self, values, label_format):
        self.pack_label_batches.append((list(values), label_format))
        return {"fileUrl": f"https://files.test/pack-{len(self.pack_label_batches)}.pdf", "excludedPacks": []}

    async def download_pdf(self, url):
        return self.pdf


class FakeShipmentClient(FakeLamodaClient):
    def __init__(self):
        super().__init__()
        self.shipment_calls = []

    async def create_shipment(self, ship_at, pallets):
        self.shipment_calls.append((ship_at, pallets))
        return {
            "shipmentId": "SHIP-ONE",
            "pallets": [
                {"palletId": "PALLET-2", "packs": [pallets[1]["packs"][0]]},
                {"palletId": "PALLET-1", "packs": [pallets[0]["packs"][0]]},
            ],
        }


class FakeGeneratedPacksClient(FakeLamodaClient):
    async def create_packs(self, order_id, count):
        self.pack_counts.append(count)
        raise LamodaValidationError("It is not possible to generate more codes")

    async def existing_order_pack_numbers(self, order_id):
        return [f"EXISTING-{index:03d}" for index in range(1, 3)]


class FakeDiscoveryClient(FakeLamodaClient):
    def __init__(self, orders):
        super().__init__()
        self.orders = orders
        self.detail_requests = []

    async def list_orders(self, **params):
        return self.orders

    async def get_order(self, order_id):
        self.detail_requests.append(order_id)
        return next(row for row in self.orders if row["id"] == order_id)


class FakeUncertainShipmentClient(FakeLamodaClient):
    def __init__(self):
        super().__init__()
        self.create_count = 0

    async def create_shipment(self, ship_at, pallets):
        self.create_count += 1
        raise LamodaTemporaryError("timeout", uncertain=True)

    async def list_shipments(self, **params):
        return []


class LamodaWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine, tables=LAMODA_TABLES)
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)
        self.patches = [
            patch("modules.lamoda_fbs.storage.session_scope", new=self.session_scope),
            patch("modules.lamoda_fbs.services.session_scope", new=self.session_scope),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
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

    @staticmethod
    def order(order_id="ORDER-1", count=5):
        return {
            "orderId": order_id,
            "status": "NEW",
            "items": [
                {"itemId": f"ITEM-{index:03d}", "sku": f"SKU-{index}", "name": "Футболка", "size": "M"}
                for index in range(1, count + 1)
            ],
        }

    async def test_five_items_create_five_one_item_packs_with_explicit_mapping(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order()], "7", "Сотрудник", client)
        self.assertEqual(result["errors"], [])
        self.assertEqual(client.pack_counts, [5])
        self.assertEqual(
            client.assemblies[0][1],
            [{"itemIds": [f"ITEM-{index:03d}"]} for index in range(1, 6)],
        )
        packs = get_session_packs(result["session_id"])
        self.assertEqual(len(packs), 5)
        self.assertEqual(
            [(row["item_id"], row["pack_number"]) for row in packs],
            [(f"ITEM-{index:03d}", f"PACK-{index:03d}") for index in range(1, 6)],
        )

    async def test_moysklad_name_is_used_and_picking_pdf_is_created(self):
        client = FakeLamodaClient()
        order = self.order(count=1)
        order["items"][0].update({
            "externalSku": "HBM-BAG",
            "_moysklad_name": "HOMME BIRKIN MESSENGER (19x25x10, черная, HBM-BAG)",
        })

        result = await prepare_orders([order], "7", "Сотрудник", client)
        pack = get_session_packs(result["session_id"])[0]

        self.assertEqual(pack["lamoda_product_name"], "Футболка")
        self.assertEqual(pack["product_name"], order["items"][0]["_moysklad_name"])
        self.assertEqual(pack["external_sku"], "HBM-BAG")
        self.assertGreater(len(PdfReader(BytesIO(assembly_picking_document(result["session_id"]))).pages), 0)

    async def test_v2_resource_and_item_ids_are_used_for_mutations(self):
        client = FakeLamodaClient()
        order = {
            "id": "RESOURCE-ORDER-1",
            "orderId": "RU260810-123456",
            "status": "NEW",
            "items": [{
                "id": "RESOURCE-ITEM-1",
                "itemId": "LEGACY-ITEM-1",
                "sku": "SKU-1",
                "name": "Футболка",
            }],
        }
        result = await prepare_orders([order], "7", "Сотрудник", client)
        self.assertEqual(result["errors"], [])
        self.assertEqual(client.assemblies, [
            ("RESOURCE-ORDER-1", [{"itemIds": ["RESOURCE-ITEM-1"]}]),
        ])
        packs = get_session_packs(result["session_id"])
        self.assertEqual(packs[0]["order_id"], "RU260810-123456")
        self.assertEqual(packs[0]["item_id"], "RESOURCE-ITEM-1")

    async def test_existing_pack_numbers_are_recovered_after_generation_is_rejected(self):
        client = FakeGeneratedPacksClient()

        result = await prepare_orders([self.order(count=2)], "7", "Сотрудник", client)

        self.assertEqual(result["errors"], [])
        self.assertEqual(client.pack_counts, [2])
        self.assertEqual(
            [row["pack_number"] for row in get_session_packs(result["session_id"])],
            ["EXISTING-001", "EXISTING-002"],
        )

    async def test_locally_assembled_order_recovers_packs_without_regeneration(self):
        client = FakeGeneratedPacksClient()
        order = self.order(count=2)
        persist_order(order, order["items"])
        set_order_preparation(order["orderId"], "ASSEMBLED")

        result = await prepare_orders([order], "7", "Сотрудник", client)

        self.assertEqual(result["errors"], [])
        self.assertEqual(client.assemblies, [])
        self.assertEqual(client.pack_counts, [])

    async def test_discovery_resumes_locally_assembled_order_awaiting_shipment(self):
        recoverable = {
            "id": "RU260809-591247",
            "orderId": "RU260809-591247",
            "status": "AWAITING_SHIPMENT",
            "items": [{"id": "ITEM-1", "sku": "SKU-1"}],
        }
        unrelated = {
            "id": "RU260804-632500",
            "orderId": "RU260804-632500",
            "status": "AWAITING_SHIPMENT",
            "items": [{"id": "ITEM-2", "sku": "SKU-2"}],
        }
        persist_order(recoverable, recoverable["items"])
        set_order_preparation(recoverable["orderId"], "ASSEMBLED")
        client = FakeDiscoveryClient([recoverable, unrelated])

        result = await discover_orders(client)

        self.assertEqual([row["orderId"] for row in result], ["RU260809-591247"])
        self.assertEqual(client.detail_requests, ["RU260809-591247"])

    async def test_discovery_does_not_resume_terminal_local_order(self):
        terminal = {
            "id": "RU260815-219987", "orderId": "RU260815-219987",
            "status": "SENT_BACK_TO_PARTNER",
            "items": [{"id": "ITEM-TERMINAL", "sku": "SKU-1"}],
        }
        persist_order(terminal, terminal["items"])
        set_order_preparation(terminal["orderId"], "ASSEMBLED")
        client = FakeDiscoveryClient([terminal])

        result = await discover_orders(client)

        self.assertEqual(result, [])
        self.assertEqual(client.detail_requests, [])

    async def test_label_requests_are_split_at_100_and_merged(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=101)], "7", "Сотрудник", client)
        documents = await assembly_label_documents(result["session_id"], client)
        self.assertEqual([len(row[0]) for row in client.item_label_batches], [100, 1])
        self.assertEqual([len(row[0]) for row in client.pack_label_batches], [100, 1])
        self.assertEqual(len(PdfReader(BytesIO(documents["item_pdf"])).pages), 2)
        self.assertEqual(len(PdfReader(BytesIO(documents["pack_pdf"])).pages), 2)

    async def test_missing_label_file_url_is_an_error(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)

        async def missing_url(values, label_format):
            return {"fileUrl": "", "excludedItems": []}

        client.order_item_labels = missing_url
        with self.assertRaisesRegex(LamodaDocumentError, "fileUrl"):
            await assembly_label_documents(result["session_id"], client)

    async def test_excluded_labels_are_returned_to_operator(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)

        async def item_labels(values, label_format):
            return {"fileUrl": "https://files.test/items.pdf", "excludedItems": ["ITEM-001"]}

        async def pack_labels(values, label_format):
            return {"fileUrl": "https://files.test/packs.pdf", "excludedPacks": ["PACK-001"]}

        client.order_item_labels = item_labels
        client.order_pack_labels = pack_labels
        documents = await assembly_label_documents(result["session_id"], client)
        self.assertEqual(documents["excluded_items"], ["ITEM-001"])
        self.assertEqual(documents["excluded_packs"], ["PACK-001"])

    async def test_terminal_order_from_active_session_is_excluded_from_labels(self):
        client = FakeLamodaClient()
        stale = {
            "orderId": "RU260815-219987", "status": "NEW",
            "items": [{"itemId": "STALE-ITEM", "name": "Старый товар"}],
        }
        session = create_assembly_session("7", "Сотрудник")
        persist_order(stale, stale["items"])
        attach_packs(session.id, [{
            "order_id": stale["orderId"], "item_id": "STALE-ITEM", "pack_number": "STALE-PACK",
        }])
        client.order_statuses[stale["orderId"]] = "SENT_BACK_TO_PARTNER"

        result = await prepare_orders([self.order("CURRENT-ORDER", 1)], "7", "Сотрудник", client)
        documents = await assembly_label_documents(result["session_id"], client)

        self.assertEqual(client.item_label_batches[0][0], ["ITEM-001"])
        self.assertEqual(client.pack_label_batches[0][0], ["PACK-001"])
        self.assertEqual(result["excluded_orders"], [{
            "order_id": "RU260815-219987", "status": "SENT_BACK_TO_PARTNER",
        }])
        self.assertEqual(documents["excluded_orders"], [])
        self.assertEqual(
            [row["pack_number"] for row in get_session_packs(session.id, include_cancelled=True)],
            ["STALE-PACK", "PACK-001"],
        )
        self.assertEqual([row["pack_number"] for row in get_session_packs(session.id)], ["PACK-001"])

    async def test_return_item_label_resolves_local_pack_without_pack_scan(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)

        resolved = await resolve_return_item_barcode("ITEM-001", client)

        self.assertEqual(resolved["pack_number"], "PACK-001")
        self.assertEqual(resolved["item_id"], "ITEM-001")

    async def test_scan_resume_duplicate_kiz_and_cargo_rules(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=2)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        first = get_next_unscanned_pack(session_id)
        self.assertEqual(first["pack_number"], "PACK-001")
        with self.assertRaisesRegex(ValueError, "другого отправления"):
            mark_pack_barcode_scanned("PACK-002", first["pack_number"])
        parsed = parse_marking_code("010460123456789021SERIAL-1\x1d91AB\x1d92CD")
        mark_pack_barcode_scanned(first["pack_number"], first["pack_number"])
        assign_marking_code(first["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        assign_marking_code(first["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        resumed = get_next_unscanned_pack(session_id)
        self.assertEqual(resumed["pack_number"], "PACK-002")
        mark_pack_barcode_scanned(resumed["pack_number"], resumed["pack_number"])
        with self.assertRaisesRegex(RuntimeError, "уже зарезервирован"):
            assign_marking_code(resumed["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        parsed2 = parse_marking_code("010460123456789021SERIAL-2\x1d91AB\x1d92CD")
        assign_marking_code(resumed["pack_number"], parsed2.raw, parsed2.uit, parsed2.gtin, parsed2.serial, "7")

        cargo1 = create_cargo_place(session_id, "7")
        self.assertEqual(cargo1.local_number, 1)
        add_pack_to_cargo(cargo1.id, "PACK-001", "7")
        self.assertFalse(add_pack_to_cargo(cargo1.id, "PACK-001", "7"))
        set_cargo_status(cargo1.id, "CLOSED", "7")
        with self.assertRaisesRegex(RuntimeError, "ровно в одном"):
            validate_cargo_complete(session_id)
        cargo2 = create_cargo_place(session_id, "7")
        self.assertEqual(cargo2.local_number, 2)
        add_pack_to_cargo(cargo2.id, "PACK-002", "7")
        set_cargo_status(cargo2.id, "CLOSED", "7")
        self.assertEqual(len(validate_cargo_complete(session_id)), 2)

    async def test_unmarked_product_can_be_packed_without_kiz_and_skips_withdrawal(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=2)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        marked, unmarked = get_session_packs(session_id)

        parsed = parse_marking_code("010460123456789021SERIAL-1\x1d91AB\x1d92CD")
        mark_pack_barcode_scanned(marked["pack_number"], marked["pack_number"])
        assign_marking_code(
            marked["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7",
        )
        mark_pack_barcode_scanned(unmarked["pack_number"], unmarked["pack_number"])
        complete_pack_without_marking(unmarked["pack_number"], "7")

        rows = get_session_packs(session_id)
        self.assertTrue(rows[0]["requires_marking"])
        self.assertTrue(rows[0]["kiz_scanned"])
        self.assertFalse(rows[1]["requires_marking"])
        self.assertTrue(rows[1]["packed"])
        self.assertFalse(rows[1]["kiz_scanned"])

        cargo = create_cargo_place(session_id, "7")
        for pack in rows:
            add_pack_to_cargo(cargo.id, pack["pack_number"], "7")
        set_cargo_status(cargo.id, "CLOSED", "7")
        save_shipment(
            session_id, "SHIP-MIXED", __import__("datetime").datetime.now(), {},
            {cargo.id: "PALLET-MIXED"},
        )

        self.assertEqual(self._pack(marked["pack_number"]).marking_state, PackState.WAITING_WITHDRAWAL)
        self.assertEqual(self._pack(unmarked["pack_number"]).marking_state, PackState.PACKED)
        batch = create_marking_batch("WITHDRAWAL", "42")
        self.assertEqual(
            [row["pack_number"] for row in marking_batch_rows(batch)],
            [marked["pack_number"]],
        )

    def test_pallets_are_matched_by_pack_set_not_array_order(self):
        manifest = [
            {"id": 10, "local_number": 1, "packs": [{"pack_number": "A"}, {"pack_number": "B"}]},
            {"id": 11, "local_number": 2, "packs": [{"pack_number": "C"}]},
        ]
        response = [
            {"palletId": "PALLET-2", "packs": [{"packId": "C"}]},
            {"palletId": "PALLET-1", "packs": [{"packId": "B"}, {"packId": "A"}]},
        ]
        self.assertEqual(match_pallets(manifest, response), {10: "PALLET-1", 11: "PALLET-2"})

    async def test_all_cargo_places_are_sent_in_one_common_shipment(self):
        client = FakeShipmentClient()
        result = await prepare_orders([self.order(count=2)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        for index, pack in enumerate(get_session_packs(session_id), 1):
            parsed = parse_marking_code(f"010460123456789021SHIP-{index}\x1d91AB\x1d92CD")
            mark_pack_barcode_scanned(pack["pack_number"], pack["pack_number"])
            assign_marking_code(pack["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        for pack in get_session_packs(session_id):
            cargo = create_cargo_place(session_id, "7")
            add_pack_to_cargo(cargo.id, pack["pack_number"], "7")
            set_cargo_status(cargo.id, "CLOSED", "7")
        shipment_id = await create_lamoda_shipment(session_id, client)
        self.assertEqual(shipment_id, "SHIP-ONE")
        self.assertEqual(len(client.shipment_calls), 1)
        self.assertEqual(len(client.shipment_calls[0][1]), 2)
        self.assertEqual(client.shipment_calls[0][1], [
            {"packs": [{"packId": "PACK-001", "items": [{"unitload": "ITEM-001"}]}]},
            {"packs": [{"packId": "PACK-002", "items": [{"unitload": "ITEM-002"}]}]},
        ])
        self.assertEqual([row["pallet_id"] for row in cargo_manifest(session_id)], ["PALLET-1", "PALLET-2"])

    async def test_uncertain_shipment_post_is_not_sent_a_second_time(self):
        client = FakeUncertainShipmentClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        pack = get_session_packs(session_id)[0]
        parsed = parse_marking_code("010460123456789021UNCERTAIN\x1d91AB\x1d92CD")
        mark_pack_barcode_scanned(pack["pack_number"], pack["pack_number"])
        assign_marking_code(pack["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        cargo = create_cargo_place(session_id, "7")
        add_pack_to_cargo(cargo.id, pack["pack_number"], "7")
        set_cargo_status(cargo.id, "CLOSED", "7")
        with self.assertRaises(LamodaTemporaryError):
            await create_lamoda_shipment(session_id, client)
        with self.assertRaisesRegex(RuntimeError, "Повторная отправка заблокирована"):
            await create_lamoda_shipment(session_id, client)
        self.assertEqual(client.create_count, 1)

    async def test_full_withdrawal_return_reintroduction_and_code_reuse(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        pack = get_next_unscanned_pack(session_id)
        parsed = parse_marking_code("010460123456789021SERIAL-1\x1d91AB\x1d92CD")
        mark_pack_barcode_scanned(pack["pack_number"], pack["pack_number"])
        assign_marking_code(pack["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        cargo = create_cargo_place(session_id, "7")
        add_pack_to_cargo(cargo.id, pack["pack_number"], "7")
        set_cargo_status(cargo.id, "CLOSED", "7")
        save_shipment(session_id, "SHIP-1", __import__("datetime").datetime.now(), {}, {cargo.id: "PALLET-1"})

        batch = create_marking_batch("WITHDRAWAL", "42")
        confirm_marking_batch(batch, "42")
        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.WITHDRAWN)
        update_pack_lamoda_status(pack["pack_number"], "NOT_BOUGHT")
        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.RETURN_EXPECTED)
        record_return_receipt(
            pack_number=pack["pack_number"], order_id="", item_id="", return_item_id="RETURN-1",
            condition="DEFECT", defect_reason="Пятно", label_photo_file_id="photo-label",
            scanned_kiz_fingerprint=code_fingerprint(parsed.normalized), defect_photo_file_ids=["photo-defect"],
            user_id="7", user_name="Сотрудник",
        )
        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.WAITING_REINTRODUCTION)
        reintro_batch = create_marking_batch("REINTRODUCTION", "42")
        confirm_marking_batch(reintro_batch, "42")
        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.REINTRODUCED)
        with self.session_scope() as session:
            code = session.execute(select(MarkingCode)).scalar_one()
            self.assertEqual(code.state, CodeState.AVAILABLE)

        new_session = create_assembly_session("8", "Другой сотрудник")
        from modules.lamoda_fbs.storage import persist_order
        persist_order(self.order("ORDER-2", 1) | {"items": [{"itemId": "ITEM-NEW", "name": "Футболка", "size": "M"}]}, [{"itemId": "ITEM-NEW", "name": "Футболка", "size": "M"}])
        attach_packs(new_session.id, [{"order_id": "ORDER-2", "item_id": "ITEM-NEW", "pack_number": "PACK-NEW"}])
        mark_pack_barcode_scanned("PACK-NEW", "PACK-NEW")
        assign_marking_code("PACK-NEW", parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "8")
        self.assertEqual(self._pack("PACK-NEW").marking_state, PackState.PACKED)
        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.REINTRODUCED)

    async def test_partial_batch_marks_only_failed_pack_for_reconciliation(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=2)], "7", "Сотрудник", client)
        session_id = result["session_id"]
        for index, pack in enumerate(get_session_packs(session_id), 1):
            parsed = parse_marking_code(f"010460123456789021SERIAL-{index}\x1d91AB\x1d92CD")
            mark_pack_barcode_scanned(pack["pack_number"], pack["pack_number"])
            assign_marking_code(pack["pack_number"], parsed.raw, parsed.uit, parsed.gtin, parsed.serial, "7")
        cargo = create_cargo_place(session_id, "7")
        for pack in get_session_packs(session_id):
            add_pack_to_cargo(cargo.id, pack["pack_number"], "7")
        set_cargo_status(cargo.id, "CLOSED", "7")
        save_shipment(session_id, "SHIP-PARTIAL", __import__("datetime").datetime.now(), {}, {cargo.id: "PALLET-P"})
        batch = create_marking_batch("WITHDRAWAL", "42")
        status = confirm_marking_batch(batch, "42", ["PACK-002"])
        self.assertEqual(status, "PARTIAL")
        self.assertEqual(self._pack("PACK-001").marking_state, PackState.WITHDRAWN)
        self.assertEqual(self._pack("PACK-002").marking_state, PackState.NEEDS_RECONCILIATION)

    def test_defect_receipt_requires_reason_and_photo(self):
        with self.assertRaisesRegex(ValueError, "причин"):
            record_return_receipt(
                pack_number="UNKNOWN", order_id="ORDER", item_id="", return_item_id="",
                condition="DEFECT", defect_reason="", label_photo_file_id="label",
                scanned_kiz_fingerprint="", defect_photo_file_ids=[],
                user_id="7", user_name="Сотрудник",
            )

    def test_return_without_pack_is_deduplicated_by_mandatory_item_label(self):
        kwargs = {
            "pack_number": None, "order_id": "ORDER", "item_id": "ITEM-LABEL",
            "return_item_id": "RETURN-1", "condition": "NORMAL", "defect_reason": "",
            "label_photo_file_id": "label", "scanned_kiz_fingerprint": "",
            "defect_photo_file_ids": [], "user_id": "7", "user_name": "Сотрудник",
        }
        record_return_receipt(**kwargs)

        with self.assertRaisesRegex(RuntimeError, "Возврат уже принят"):
            record_return_receipt(**kwargs)

    async def test_unmarked_return_without_kiz_does_not_wait_for_reintroduction(self):
        client = FakeLamodaClient()
        result = await prepare_orders([self.order(count=1)], "7", "Сотрудник", client)
        pack = get_session_packs(result["session_id"])[0]
        mark_pack_barcode_scanned(pack["pack_number"], pack["pack_number"])
        complete_pack_without_marking(pack["pack_number"], "7")

        record_return_receipt(
            pack_number=pack["pack_number"], order_id=pack["order_id"], item_id=pack["item_id"],
            return_item_id="RETURN-UNMARKED", condition="NORMAL", defect_reason="",
            label_photo_file_id="label", scanned_kiz_fingerprint="", defect_photo_file_ids=[],
            user_id="7", user_name="Сотрудник",
        )

        self.assertEqual(self._pack(pack["pack_number"]).marking_state, PackState.REINTRODUCED)
        self.assertFalse(self._pack(pack["pack_number"]).requires_marking)

    def _pack(self, pack_number):
        with self.session_scope() as session:
            return session.execute(select(LamodaPack).where(LamodaPack.pack_number == pack_number)).scalar_one()


if __name__ == "__main__":
    unittest.main()
