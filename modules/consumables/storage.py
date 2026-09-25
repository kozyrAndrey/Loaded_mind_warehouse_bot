import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, delete, desc, distinct, select, text
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class ConsumableSupply(Base):
    __tablename__ = "consumable_supplies"
    __table_args__ = (
        Index("ix_consumable_supplies_status", "status"),
        Index("ix_consumable_supplies_created_at", "created_at"),
        Index("ix_consumable_supplies_organization", "organization"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    consumable_name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_by_user_id: Mapped[str | None] = mapped_column(String(100))
    created_by_name: Mapped[str | None] = mapped_column(String(255))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(100))
    accepted_by_name: Mapped[str | None] = mapped_column(String(255))
    layout_photo_file_id: Mapped[str | None] = mapped_column(Text)
    closing_document_file_id: Mapped[str | None] = mapped_column(Text)
    closing_document_kind: Mapped[str | None] = mapped_column(String(50))
    topic_message_ids: Mapped[str | None] = mapped_column(String(1000))
    document_workflow_message_ids: Mapped[str | None] = mapped_column(String(1000))
    supply_items_json: Mapped[str | None] = mapped_column(Text)
    invoice_document_file_id: Mapped[str | None] = mapped_column(Text)
    invoice_document_kind: Mapped[str | None] = mapped_column(String(50))


class ConsumableSupplier(Base):
    __tablename__ = "consumable_suppliers"
    __table_args__ = (
        Index("ix_consumable_suppliers_name", "name"),
        Index("ix_consumable_suppliers_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    closing_documents_delivery: Mapped[str] = mapped_column(String(20), nullable=False, default="paper")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConsumableItem(Base):
    __tablename__ = "consumable_items"
    __table_args__ = (
        Index("ix_consumable_items_name", "name"),
        Index("ix_consumable_items_is_active", "is_active"),
    )

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    unit: Mapped[str] = mapped_column(String(50), nullable=False, default="шт")
    current_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProductConsumableRule(Base):
    __tablename__ = "product_consumable_rules"
    __table_args__ = (
        UniqueConstraint("product_id", "item_id", name="uq_product_consumable_rules_product_item"),
        Index("ix_product_consumable_rules_product_id", "product_id"),
        Index("ix_product_consumable_rules_item_id", "item_id"),
    )

    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(255))
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_per_unit: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConsumableMovement(Base):
    __tablename__ = "consumable_movements"
    __table_args__ = (
        Index("ix_consumable_movements_created_at", "created_at"),
        Index("ix_consumable_movements_item_id", "item_id"),
        Index("ix_consumable_movements_source", "source"),
    )

    movement_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_delta: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str | None] = mapped_column(String(100))
    created_by_name: Mapped[str | None] = mapped_column(String(255))


class ConsumableInventoryCount(Base):
    __tablename__ = "consumable_inventory_counts"
    __table_args__ = (
        Index("ix_consumable_inventory_counts_created_at", "created_at"),
        Index("ix_consumable_inventory_counts_item_id", "item_id"),
    )

    count_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    system_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    counted_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    difference: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    counted_by_user_id: Mapped[str | None] = mapped_column(String(100))
    counted_by_name: Mapped[str | None] = mapped_column(String(255))
    batch_id: Mapped[str | None] = mapped_column(String(100))


class ConsumableReceipt(Base):
    __tablename__ = "consumable_receipts"
    __table_args__ = (
        Index("ix_consumable_receipts_created_at", "created_at"),
        Index("ix_consumable_receipts_item_id", "item_id"),
    )

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(100))
    accepted_by_name: Mapped[str | None] = mapped_column(String(255))
    layout_photo_file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    closing_document_kind: Mapped[str] = mapped_column(String(50), nullable=False, default="no_paper")
    closing_document_file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class ConsumableInventorySession(Base):
    __tablename__ = "consumable_inventory_sessions"
    __table_args__ = (
        Index("ix_consumable_inventory_sessions_status", "status"),
        Index("ix_consumable_inventory_sessions_created_at", "created_at"),
    )

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    created_by_user_id: Mapped[str | None] = mapped_column(String(100))
    created_by_name: Mapped[str | None] = mapped_column(String(255))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_by_user_id: Mapped[str | None] = mapped_column(String(100))
    completed_by_name: Mapped[str | None] = mapped_column(String(255))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
    applied_by_user_id: Mapped[str | None] = mapped_column(String(100))
    applied_by_name: Mapped[str | None] = mapped_column(String(255))


class ConsumableInventorySessionItem(Base):
    __tablename__ = "consumable_inventory_session_items"
    __table_args__ = (
        UniqueConstraint("session_id", "item_id", name="uq_consumable_inventory_session_item"),
        Index("ix_consumable_inventory_session_items_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    system_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    counted_quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    counted_by_user_id: Mapped[str | None] = mapped_column(String(100))
    counted_by_name: Mapped[str | None] = mapped_column(String(255))
    counted_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class ConsumableInventorySessionParticipant(Base):
    __tablename__ = "consumable_inventory_session_participants"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id", name="uq_consumable_inventory_session_participant"),
        Index("ix_consumable_inventory_session_participants_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(255))
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class ConsumableInventorySessionChange(Base):
    __tablename__ = "consumable_inventory_session_changes"
    __table_args__ = (
        Index("ix_consumable_inventory_session_changes_session", "session_id"),
        Index("ix_consumable_inventory_session_changes_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    new_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    changed_by_user_id: Mapped[str | None] = mapped_column(String(100))
    changed_by_name: Mapped[str | None] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


LABEL_58_40 = "Этикетки для принтера 58х40"
SUPPLIER_DOCUMENTS_EDO = "edo"
SUPPLIER_DOCUMENTS_PAPER = "paper"
SUPPLIER_DOCUMENT_DELIVERY_VALUES = {SUPPLIER_DOCUMENTS_EDO, SUPPLIER_DOCUMENTS_PAPER}


DEFAULT_CONSUMABLE_ITEMS = [
    ("Коробки 305*210*70 мм (Футболки)", "шт"),
    ("Коробки 300*200*100 мм (Сумки малые)", "шт"),
    ("Коробки 400*350*100 мм (Корсеты)", "шт"),
    ("Коробки 580х350х160 (Пуховики)", "шт"),
    ("Пыльники (Сумки большие)", "шт"),
    ("Курьерский пакет 300*400+40 мм (Футболки)", "шт"),
    ("Курьерский пакет 430*500+40 мм (Худи)", "шт"),
    ("Курьерский пакет 660*500+40 мм (Пуховики)", "шт"),
    ("Скотч hmmls", "шт"),
    ("Дой-паки", "шт"),
    ("Мешки большие (рулон)", "рулон"),
    ("Мешки малые (рулон)", "рулон"),
    ("Биркодержатели", "шт"),
    ("Навесные бирки с логотипом hmmls белые", "шт"),
    ("Навесные бирки с текстом hmmls белые", "шт"),
    ("Навесные бирки с логотипом hmmls черные", "шт"),
    ("Навесные бирки с текстом hmmls черные", "шт"),
    ('Открытка А5 базовая "Шар"', "шт"),
    ("Открытка A5 Diamond худи/штаны", "шт"),
    ("Открытка А5 базовые пуховики", "шт"),
    ("Открытка А5 двухсторонние", "шт"),
    ("Открытка A5 Diamond пуховики", "шт"),
    ("Открытка A5 Кожанки", "шт"),
    ("Сертификат А5 подарочный", "шт"),
    ("Zip Lock пакет черный 35*45 мм (Diamond)", "шт"),
    ("Zip Lock пакет белый 35*45 мм (Базовые вещи)", "шт"),
    ("Zip Lock пакет белый 25*35 мм (футболки / монограм шарфы)", "шт"),
    ("Zip Lock пакет hmmls 60*70 мм (Пуховики)", "шт"),
    ("Zip Lock пакет белый 30*40мм (сумки средние)", "шт"),
    ("Пакеты белые с ручкой с брендированием 60*70", "шт"),
    ("Пакеты прозрачные с ручкой с брендированием 30*40", "шт"),
    ("Бутылки (мешки)", "шт"),
    ("Наклейки для бутылок", "шт"),
    ("Картриджи для принтера", "шт"),
    ("Бумага (упаковки)", "упак"),
    (LABEL_58_40, "шт"),
    ("Этикетки для принтера 120х75 (рулон)", "рулон"),
    ("Стаканчики", "шт"),
    ("Вилки", "шт"),
    ("Ложки", "шт"),
    ("Пломбы не маркированные", "шт"),
    ("Пломбы", "шт"),
    ("Открытка А5 корсет", "шт"),
    ("Номерной знак 1", "шт"),
    ("Номерной знак 2", "шт"),
    ("Номерной знак 3", "шт"),
    ("Номерной знак 4", "шт"),
    ("Номерной знак 5", "шт"),
    ("Номерной знак 6", "шт"),
    ("Номерной знак 7", "шт"),
    ("Номерной знак 8", "шт"),
    ("Открытка V2", "шт"),
    ("Открытка Рубашки", "шт"),
    ("Открытки ремни", "шт"),
]


DEFAULT_CONSUMABLE_ITEM_UNITS = dict(DEFAULT_CONSUMABLE_ITEMS)
CONSUMABLE_ITEM_RENAMES = {
    "Этикетки для принтера 40х58 (рулон)": LABEL_58_40,
}


TAG_HOLDER = "Биркодержатели"
WHITE_LOGO_TAG = "Навесные бирки с логотипом hmmls белые"
WHITE_TEXT_TAG = "Навесные бирки с текстом hmmls белые"
BLACK_LOGO_TAG = "Навесные бирки с логотипом hmmls черные"
BLACK_TEXT_TAG = "Навесные бирки с текстом hmmls черные"
BASIC_CARD = 'Открытка А5 базовая "Шар"'
DIAMOND_HOODIE_PANTS_CARD = "Открытка A5 Diamond худи/штаны"
V2_CARD = "Открытка V2"
SHIRT_CARD = "Открытка Рубашки"
CORSET_CARD = "Открытка А5 корсет"
REVERSIBLE_CARD = "Открытка А5 двухсторонние"
BELT_CARD = "Открытки ремни"
BASE_ZIP_LOCK = "Zip Lock пакет белый 35*45 мм (Базовые вещи)"
DIAMOND_ZIP_LOCK = "Zip Lock пакет черный 35*45 мм (Diamond)"
TSHIRT_ZIP_LOCK = "Zip Lock пакет белый 25*35 мм (футболки / монограм шарфы)"
PUFFER_ZIP_LOCK = "Zip Lock пакет hmmls 60*70 мм (Пуховики)"
BAG_ZIP_LOCK = "Zip Lock пакет белый 30*40мм (сумки средние)"
SMALL_BAG_BOX = "Коробки 300*200*100 мм (Сумки малые)"
CORSET_BOX = "Коробки 400*350*100 мм (Корсеты)"
SEALS = "Пломбы"


def init_consumables_storage():
    Base.metadata.create_all(
        get_engine(),
        tables=[
            ConsumableSupply.__table__,
            ConsumableSupplier.__table__,
            ConsumableItem.__table__,
            ProductConsumableRule.__table__,
            ConsumableMovement.__table__,
            ConsumableInventoryCount.__table__,
            ConsumableReceipt.__table__,
            ConsumableInventorySession.__table__,
            ConsumableInventorySessionItem.__table__,
            ConsumableInventorySessionParticipant.__table__,
            ConsumableInventorySessionChange.__table__,
        ],
    )
    ensure_consumables_columns()
    seed_suppliers_from_existing_supplies()
    # Номенклатура Loaded Mind добавляется через бот, нормы на товары не используются.


def seed_default_consumable_items():
    with session_scope() as session:
        for old_name, new_name in CONSUMABLE_ITEM_RENAMES.items():
            old_item = (
                session.execute(select(ConsumableItem).where(ConsumableItem.name == old_name))
                .scalars()
                .first()
            )
            if not old_item:
                continue

            new_item = (
                session.execute(select(ConsumableItem).where(ConsumableItem.name == new_name))
                .scalars()
                .first()
            )
            if new_item:
                old_item.is_active = False
            else:
                old_item.name = new_name
                old_item.unit = DEFAULT_CONSUMABLE_ITEM_UNITS.get(new_name, old_item.unit)
                old_item.is_active = True

        session.flush()

        for name, unit in DEFAULT_CONSUMABLE_ITEMS:
            item = (
                session.execute(select(ConsumableItem).where(ConsumableItem.name == name))
                .scalars()
                .first()
            )
            if item:
                item.unit = unit
            else:
                session.add(ConsumableItem(name=name, unit=unit, current_quantity=0, is_active=True))


def seed_default_product_consumable_rules():
    from modules.receiving.products import CATEGORIES

    rule_specs = build_default_product_consumable_rule_specs(CATEGORIES)
    if not rule_specs:
        return

    with session_scope() as session:
        item_names = sorted({item_name for rules in rule_specs.values() for item_name in rules["items"]})
        items = (
            session.execute(select(ConsumableItem).where(ConsumableItem.name.in_(item_names)))
            .scalars()
            .all()
        )
        items_by_name = {item.name: item for item in items}
        missing_items = sorted(set(item_names) - set(items_by_name))
        if missing_items:
            raise RuntimeError("Не найдены расходники для норм: " + ", ".join(missing_items))

        product_ids = list(rule_specs)
        existing_rules = (
            session.execute(select(ProductConsumableRule).where(ProductConsumableRule.product_id.in_(product_ids)))
            .scalars()
            .all()
        )
        rules_by_key = {(rule.product_id, rule.item_id): rule for rule in existing_rules}
        rules_by_product = {}
        for rule in existing_rules:
            rules_by_product.setdefault(rule.product_id, []).append(rule)

        for product_id, spec in rule_specs.items():
            desired_item_ids = set()
            for item_name, quantity in spec["items"].items():
                item = items_by_name[item_name]
                desired_item_ids.add(item.item_id)
                key = (product_id, item.item_id)
                rule = rules_by_key.get(key)
                if rule:
                    rule.product_name = spec["product_name"]
                    rule.quantity_per_unit = quantity
                    rule.is_active = bool(item.is_active)
                else:
                    session.add(
                        ProductConsumableRule(
                            product_id=product_id,
                            product_name=spec["product_name"],
                            item_id=item.item_id,
                            quantity_per_unit=quantity,
                            is_active=bool(item.is_active),
                        )
                    )

            for rule in rules_by_product.get(product_id, []):
                if rule.item_id not in desired_item_ids:
                    rule.is_active = False


def build_default_product_consumable_rule_specs(categories):
    specs = {}

    def add_product_rules(category_id, model_id, product_id, product_name, items):
        merged_items = {}
        for item_name, quantity in list(items) + [(LABEL_58_40, 2)]:
            merged_items[item_name] = merged_items.get(item_name, 0) + float(quantity)
        specs[str(product_id)] = {
            "category_id": category_id,
            "model_id": model_id,
            "product_name": product_name,
            "items": merged_items,
        }

    def white_tags():
        return [(TAG_HOLDER, 1), (WHITE_LOGO_TAG, 1), (WHITE_TEXT_TAG, 1)]

    def black_tags():
        return [(TAG_HOLDER, 1), (BLACK_LOGO_TAG, 1), (BLACK_TEXT_TAG, 1)]

    def base_clothing():
        return white_tags() + [(BASIC_CARD, 1), (BASE_ZIP_LOCK, 1)]

    def diamond_clothing(card=DIAMOND_HOODIE_PANTS_CARD):
        return black_tags() + [(card, 1), (DIAMOND_ZIP_LOCK, 1)]

    for category_id, category_data in categories.items():
        for model_id, model_data in category_data["models"].items():
            for variant_data in model_data["variants"].values():
                product_id = variant_data["id"]
                product_name = variant_data["name"]
                upper_name = product_name.upper()

                if category_id == "hoodies":
                    if "V2" in upper_name:
                        items = diamond_clothing(card=V2_CARD)
                    elif "DIAMOND" in upper_name:
                        items = diamond_clothing()
                    else:
                        items = base_clothing()
                    if "ZIP HOODIE" in upper_name:
                        items = items + [(SEALS, 1)]
                    add_product_rules(category_id, model_id, product_id, product_name, items)

                elif category_id == "tshirts":
                    add_product_rules(
                        category_id,
                        model_id,
                        product_id,
                        product_name,
                        white_tags() + [(BASIC_CARD, 1), (TSHIRT_ZIP_LOCK, 1)],
                    )

                elif category_id == "shirts":
                    add_product_rules(
                        category_id,
                        model_id,
                        product_id,
                        product_name,
                        black_tags() + [(SHIRT_CARD, 1), (DIAMOND_ZIP_LOCK, 1)],
                    )

                elif category_id == "pants":
                    items = diamond_clothing() if "DIAMOND" in upper_name else base_clothing()
                    add_product_rules(category_id, model_id, product_id, product_name, items)

                elif category_id == "shorts":
                    add_product_rules(category_id, model_id, product_id, product_name, base_clothing())

                elif category_id == "bombers":
                    if "DIAMOND" in upper_name:
                        items = black_tags() + [(BASIC_CARD, 1), (PUFFER_ZIP_LOCK, 1)]
                    elif "CORSET" in upper_name:
                        items = white_tags() + [(CORSET_CARD, 1), (PUFFER_ZIP_LOCK, 1), (CORSET_BOX, 1)]
                    else:
                        items = white_tags() + [(BASIC_CARD, 1), (PUFFER_ZIP_LOCK, 1)]
                    add_product_rules(category_id, model_id, product_id, product_name, items)

                elif category_id == "belts":
                    tags = black_tags() if "DIAMOND" in upper_name else white_tags()
                    add_product_rules(category_id, model_id, product_id, product_name, tags + [(BELT_CARD, 1)])

                elif category_id == "vests":
                    if "DIAMOND" in upper_name:
                        items = black_tags() + [(BASIC_CARD, 1), (PUFFER_ZIP_LOCK, 1)]
                    elif "REVERSIBLE" in upper_name:
                        items = white_tags() + [(REVERSIBLE_CARD, 1), (PUFFER_ZIP_LOCK, 1)]
                    else:
                        items = white_tags() + [(BASIC_CARD, 1), (PUFFER_ZIP_LOCK, 1)]
                    add_product_rules(category_id, model_id, product_id, product_name, items)

                elif category_id == "leather":
                    add_product_rules(
                        category_id,
                        model_id,
                        product_id,
                        product_name,
                        white_tags() + [(BASIC_CARD, 1), (PUFFER_ZIP_LOCK, 1)],
                    )

                elif category_id == "bags":
                    if "MILLION DOLLAR" in upper_name:
                        items = white_tags() + [(BASIC_CARD, 1)]
                    else:
                        items = [(BASIC_CARD, 1), (BAG_ZIP_LOCK, 1), (SMALL_BAG_BOX, 1)]
                    add_product_rules(category_id, model_id, product_id, product_name, items)

    return specs


def ensure_consumables_columns():
    statements = [
        "alter table consumable_supplies add column if not exists status varchar(50) not null default 'pending'",
        "alter table consumable_supplies add column if not exists created_by_user_id varchar(100)",
        "alter table consumable_supplies add column if not exists created_by_name varchar(255)",
        "alter table consumable_supplies add column if not exists accepted_at timestamp",
        "alter table consumable_supplies add column if not exists accepted_by_user_id varchar(100)",
        "alter table consumable_supplies add column if not exists accepted_by_name varchar(255)",
        "alter table consumable_supplies add column if not exists layout_photo_file_id text",
        "alter table consumable_supplies add column if not exists closing_document_file_id text",
        "alter table consumable_supplies add column if not exists closing_document_kind varchar(50)",
        "alter table consumable_supplies add column if not exists topic_message_ids varchar(1000)",
        "alter table consumable_supplies add column if not exists document_workflow_message_ids varchar(1000)",
        "alter table consumable_supplies add column if not exists supply_items_json text",
        "alter table consumable_supplies add column if not exists invoice_document_file_id text",
        "alter table consumable_supplies add column if not exists invoice_document_kind varchar(50)",
        "create index if not exists ix_consumable_supplies_status on consumable_supplies (status)",
        "create index if not exists ix_consumable_supplies_created_at on consumable_supplies (created_at)",
        "create index if not exists ix_consumable_supplies_organization on consumable_supplies (organization)",
        "alter table consumable_suppliers add column if not exists is_active boolean not null default true",
        "alter table consumable_suppliers add column if not exists closing_documents_delivery varchar(20) not null default 'paper'",
        "create unique index if not exists uq_consumable_suppliers_name on consumable_suppliers (name)",
        "create index if not exists ix_consumable_suppliers_is_active on consumable_suppliers (is_active)",
        "create unique index if not exists uq_consumable_items_name on consumable_items (name)",
        "create index if not exists ix_consumable_items_is_active on consumable_items (is_active)",
        "create unique index if not exists uq_product_consumable_rules_product_item on product_consumable_rules (product_id, item_id)",
        "create index if not exists ix_product_consumable_rules_product_id on product_consumable_rules (product_id)",
        "create index if not exists ix_product_consumable_rules_item_id on product_consumable_rules (item_id)",
        "create index if not exists ix_consumable_movements_created_at on consumable_movements (created_at)",
        "create index if not exists ix_consumable_movements_item_id on consumable_movements (item_id)",
        "create index if not exists ix_consumable_movements_source on consumable_movements (source)",
        "create index if not exists ix_consumable_inventory_counts_created_at on consumable_inventory_counts (created_at)",
        "create index if not exists ix_consumable_inventory_counts_item_id on consumable_inventory_counts (item_id)",
        "alter table consumable_inventory_counts add column if not exists batch_id varchar(100)",
        "create index if not exists ix_consumable_inventory_counts_batch_id on consumable_inventory_counts (batch_id)",
        "create index if not exists ix_consumable_receipts_created_at on consumable_receipts (created_at)",
        "create index if not exists ix_consumable_receipts_item_id on consumable_receipts (item_id)",
        "create index if not exists ix_consumable_inventory_sessions_status on consumable_inventory_sessions (status)",
        "create index if not exists ix_consumable_inventory_sessions_created_at on consumable_inventory_sessions (created_at)",
        "alter table consumable_inventory_sessions add column if not exists applied_at timestamp",
        "alter table consumable_inventory_sessions add column if not exists applied_by_user_id varchar(100)",
        "alter table consumable_inventory_sessions add column if not exists applied_by_name varchar(255)",
        "create unique index if not exists uq_consumable_inventory_session_item on consumable_inventory_session_items (session_id, item_id)",
        "create index if not exists ix_consumable_inventory_session_items_session on consumable_inventory_session_items (session_id)",
        "create unique index if not exists uq_consumable_inventory_session_participant on consumable_inventory_session_participants (session_id, user_id)",
        "create index if not exists ix_consumable_inventory_session_participants_session on consumable_inventory_session_participants (session_id)",
        "create index if not exists ix_consumable_inventory_session_changes_session on consumable_inventory_session_changes (session_id)",
        "create index if not exists ix_consumable_inventory_session_changes_item on consumable_inventory_session_changes (item_id)",
    ]

    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def supply_to_dict(supply):
    supply_items = []
    try:
        supply_items = json.loads(supply.supply_items_json or "[]")
    except (TypeError, ValueError):
        supply_items = []

    return {
        "id": supply.id,
        "created_at": supply.created_at,
        "consumable_name": supply.consumable_name,
        "organization": supply.organization,
        "amount": float(supply.amount or 0),
        "status": supply.status,
        "created_by_user_id": supply.created_by_user_id or "",
        "created_by_name": supply.created_by_name or "",
        "accepted_at": supply.accepted_at,
        "accepted_by_user_id": supply.accepted_by_user_id or "",
        "accepted_by_name": supply.accepted_by_name or "",
        "layout_photo_file_id": supply.layout_photo_file_id or "",
        "closing_document_file_id": supply.closing_document_file_id or "",
        "closing_document_kind": supply.closing_document_kind or "",
        "topic_message_ids": supply.topic_message_ids or "",
        "document_workflow_message_ids": supply.document_workflow_message_ids or "",
        "supply_items": supply_items,
        "invoice_document_file_id": supply.invoice_document_file_id or "",
        "invoice_document_kind": supply.invoice_document_kind or "",
    }


def create_supply(
    consumable_name,
    organization,
    amount,
    created_by_user_id,
    created_by_name,
    supply_items=None,
    invoice_document_file_id="",
    invoice_document_kind="none",
):
    normalized_items = normalize_supply_items(supply_items or [])
    with session_scope() as session:
        upsert_supplier_in_session(session, organization)
        supply = ConsumableSupply(
            consumable_name=consumable_name,
            organization=organization,
            amount=amount,
            created_by_user_id=str(created_by_user_id or ""),
            created_by_name=created_by_name,
            supply_items_json=json.dumps(normalized_items, ensure_ascii=False),
            invoice_document_file_id=invoice_document_file_id or "",
            invoice_document_kind=invoice_document_kind or "none",
        )
        session.add(supply)
        session.flush()
        return supply_to_dict(supply)


def create_accepted_supply(
    consumable_name,
    organization,
    amount,
    supply_items,
    accepted_by_user_id,
    accepted_by_name,
    closing_document_file_id="",
    closing_document_kind="none",
    topic_message_ids=None,
    document_workflow_message_ids=None,
):
    normalized_items = normalize_supply_items(supply_items)
    if not normalized_items:
        raise RuntimeError("Добавьте хотя бы один расходник в поставку.")

    with session_scope() as session:
        upsert_supplier_in_session(session, organization)
        supply = ConsumableSupply(
            consumable_name=consumable_name,
            organization=organization,
            amount=amount,
            status="accepted",
            created_by_user_id=str(accepted_by_user_id or ""),
            created_by_name=accepted_by_name,
            accepted_at=datetime.now(),
            accepted_by_user_id=str(accepted_by_user_id or ""),
            accepted_by_name=accepted_by_name,
            closing_document_file_id=closing_document_file_id or "",
            closing_document_kind=closing_document_kind or "none",
            topic_message_ids=",".join(str(message_id) for message_id in (topic_message_ids or [])),
            document_workflow_message_ids=",".join(
                str(message_id) for message_id in (document_workflow_message_ids or [])
            ),
            supply_items_json=json.dumps(normalized_items, ensure_ascii=False),
        )
        session.add(supply)
        session.flush()
        movements = apply_supply_items_to_stock_in_session(
            session,
            supply=supply,
            supply_items=normalized_items,
            created_by_user_id=accepted_by_user_id,
            created_by_name=accepted_by_name,
        )
        session.flush()
        result = supply_to_dict(supply)
        result["stock_movements"] = movements
        return result


def normalize_supply_items(supply_items):
    result = []
    for item in supply_items or []:
        try:
            item_id = int(item.get("item_id"))
            quantity = float(item.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or quantity <= 0:
            continue
        result.append(
            {
                "item_id": item_id,
                "item_name": str(item.get("item_name") or "").strip(),
                "unit": str(item.get("unit") or "шт").strip() or "шт",
                "quantity": quantity,
            }
        )
    return result


def apply_supply_items_to_stock_in_session(session, supply, supply_items, created_by_user_id="", created_by_name=""):
    movements = []
    for supply_item in supply_items:
        item = session.get(ConsumableItem, int(supply_item["item_id"]))
        if not item or not item.is_active:
            continue
        quantity = float(supply_item["quantity"] or 0)
        if quantity <= 0:
            continue
        item.current_quantity = float(item.current_quantity or 0) + quantity
        movement = ConsumableMovement(
            item_id=item.item_id,
            quantity_delta=quantity,
            source="supply_acceptance",
            source_id=str(supply.id),
            comment=f"Приемка расходников: {supply.consumable_name}",
            created_by_user_id=str(created_by_user_id or "").strip(),
            created_by_name=str(created_by_name or "").strip(),
        )
        session.add(movement)
        session.flush()
        movements.append(movement_to_dict(movement, item.name))
    return movements


def upsert_supplier_in_session(session, name):
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None

    supplier = (
        session.execute(
            select(ConsumableSupplier).where(ConsumableSupplier.name == normalized_name)
        )
        .scalars()
        .first()
    )

    if supplier:
        supplier.is_active = True
        return supplier

    supplier = ConsumableSupplier(
        name=normalized_name,
        closing_documents_delivery=SUPPLIER_DOCUMENTS_PAPER,
        is_active=True,
    )
    session.add(supplier)
    return supplier


def normalize_supplier_documents_delivery(value):
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPLIER_DOCUMENT_DELIVERY_VALUES:
        raise RuntimeError("Выберите способ доставки закрывающих документов.")
    return normalized


def supplier_to_dict(supplier):
    return {
        "id": supplier.id,
        "created_at": supplier.created_at,
        "name": supplier.name,
        "closing_documents_delivery": supplier.closing_documents_delivery or SUPPLIER_DOCUMENTS_PAPER,
        "is_active": bool(supplier.is_active),
    }


def seed_suppliers_from_existing_supplies():
    with session_scope() as session:
        names = (
            session.execute(select(distinct(ConsumableSupply.organization)))
            .scalars()
            .all()
        )
        for name in names:
            upsert_supplier_in_session(session, name)


def create_supplier(name, closing_documents_delivery):
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise RuntimeError("Название поставщика не должно быть пустым.")
    delivery = normalize_supplier_documents_delivery(closing_documents_delivery)

    with session_scope() as session:
        supplier = (
            session.execute(select(ConsumableSupplier).where(ConsumableSupplier.name == normalized_name))
            .scalars()
            .first()
        )
        if supplier:
            supplier.closing_documents_delivery = delivery
            supplier.is_active = True
        else:
            supplier = ConsumableSupplier(
                name=normalized_name,
                closing_documents_delivery=delivery,
                is_active=True,
            )
            session.add(supplier)
        session.flush()
        return supplier_to_dict(supplier)


def get_supplier(name):
    normalized_name = str(name or "").strip()
    if not normalized_name:
        return None
    with session_scope() as session:
        supplier = (
            session.execute(select(ConsumableSupplier).where(ConsumableSupplier.name == normalized_name))
            .scalars()
            .first()
        )
        return supplier_to_dict(supplier) if supplier else None


def get_supplier_records(active_only=True, limit=50):
    with session_scope() as session:
        statement = select(ConsumableSupplier).order_by(ConsumableSupplier.name).limit(limit)
        if active_only:
            statement = statement.where(ConsumableSupplier.is_active.is_(True))
        suppliers = session.execute(statement).scalars().all()
        return [supplier_to_dict(supplier) for supplier in suppliers]


def update_supplier(supplier_id, name=None, closing_documents_delivery=None):
    with session_scope() as session:
        supplier = session.get(ConsumableSupplier, int(supplier_id))
        if not supplier:
            raise RuntimeError("Поставщик не найден.")

        old_name = supplier.name
        if name is not None:
            normalized_name = str(name or "").strip()
            if not normalized_name:
                raise RuntimeError("Название поставщика не должно быть пустым.")
            duplicate = (
                session.execute(
                    select(ConsumableSupplier).where(
                        ConsumableSupplier.name == normalized_name,
                        ConsumableSupplier.id != supplier.id,
                    )
                )
                .scalars()
                .first()
            )
            if duplicate:
                raise RuntimeError("Поставщик с таким названием уже есть.")
            supplier.name = normalized_name
            if normalized_name != old_name:
                supplies = (
                    session.execute(select(ConsumableSupply).where(ConsumableSupply.organization == old_name))
                    .scalars()
                    .all()
                )
                for supply in supplies:
                    supply.organization = normalized_name

        if closing_documents_delivery is not None:
            supplier.closing_documents_delivery = normalize_supplier_documents_delivery(
                closing_documents_delivery
            )
        supplier.is_active = True
        session.flush()
        return supplier_to_dict(supplier)


def get_recent_organizations(limit=12):
    with session_scope() as session:
        inactive_names = set(
            session.execute(
                select(ConsumableSupplier.name).where(ConsumableSupplier.is_active.is_(False))
            )
            .scalars()
            .all()
        )
        active_supplier_names = (
            session.execute(
                select(ConsumableSupplier.name)
                .where(ConsumableSupplier.is_active.is_(True))
                .order_by(ConsumableSupplier.name)
            )
            .scalars()
            .all()
        )
        supply_names = (
            session.execute(select(distinct(ConsumableSupply.organization)).order_by(ConsumableSupply.organization))
            .scalars()
            .all()
        )

    names = []
    for name in list(active_supplier_names) + list(supply_names):
        if not name or name in inactive_names or name in names:
            continue
        names.append(name)
        if len(names) >= limit:
            break

    return names


def get_pending_supplies(limit=30):
    return get_supplies(status="pending", limit=limit)


def get_accepted_supplies(limit=30):
    return get_supplies(status="accepted", limit=limit)


def get_supplies(status=None, limit=30):
    with session_scope() as session:
        statement = select(ConsumableSupply).order_by(desc(ConsumableSupply.id)).limit(limit)
        if status:
            statement = statement.where(ConsumableSupply.status == status)
        supplies = session.execute(statement).scalars().all()

    return [supply_to_dict(supply) for supply in supplies]


def get_supply(supply_id):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        return supply_to_dict(supply) if supply else None


def mark_supply_accepted(
    supply_id,
    accepted_by_user_id,
    accepted_by_name,
    layout_photo_file_id,
    closing_document_file_id="",
    closing_document_kind="none",
    topic_message_ids=None,
    document_workflow_message_ids=None,
):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        if not supply:
            raise RuntimeError("Поставка не найдена.")
        if supply.status != "pending":
            raise RuntimeError("Эта поставка уже принята.")

        supply.status = "accepted"
        supply.accepted_at = datetime.now()
        supply.accepted_by_user_id = str(accepted_by_user_id or "")
        supply.accepted_by_name = accepted_by_name
        supply.layout_photo_file_id = layout_photo_file_id
        supply.closing_document_file_id = closing_document_file_id or ""
        supply.closing_document_kind = closing_document_kind or "none"
        supply.topic_message_ids = ",".join(str(message_id) for message_id in (topic_message_ids or []))
        supply.document_workflow_message_ids = ",".join(
            str(message_id) for message_id in (document_workflow_message_ids or [])
        )
        supply_items = normalize_supply_items(json.loads(supply.supply_items_json or "[]"))
        if supply_items:
            apply_supply_items_to_stock_in_session(
                session,
                supply=supply,
                supply_items=supply_items,
                created_by_user_id=accepted_by_user_id,
                created_by_name=accepted_by_name,
            )

        session.flush()
        return supply_to_dict(supply)


def update_supply(supply_id, consumable_name=None, organization=None, amount=None):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        if not supply:
            raise RuntimeError("Поставка не найдена.")

        if consumable_name is not None:
            supply.consumable_name = consumable_name
        if organization is not None:
            supply.organization = organization
            upsert_supplier_in_session(session, organization)
        if amount is not None:
            supply.amount = amount

        session.flush()
        return supply_to_dict(supply)


def delete_supply(supply_id):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        if not supply:
            raise RuntimeError("Поставка не найдена.")

        result = supply_to_dict(supply)
        if supply.status == "accepted":
            reverse_supply_acceptance_movements_in_session(session, supply)
        session.delete(supply)
        return result


def update_acceptance(
    supply_id,
    accepted_by_user_id,
    accepted_by_name,
    layout_photo_file_id,
    closing_document_file_id="",
    closing_document_kind="none",
    topic_message_ids=None,
    document_workflow_message_ids=None,
):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        if not supply:
            raise RuntimeError("Поставка не найдена.")
        if supply.status != "accepted":
            raise RuntimeError("Эта поставка еще не принята.")

        supply.accepted_at = datetime.now()
        supply.accepted_by_user_id = str(accepted_by_user_id or "")
        supply.accepted_by_name = accepted_by_name
        supply.layout_photo_file_id = layout_photo_file_id
        supply.closing_document_file_id = closing_document_file_id or ""
        supply.closing_document_kind = closing_document_kind or "none"
        supply.topic_message_ids = ",".join(str(message_id) for message_id in (topic_message_ids or []))
        supply.document_workflow_message_ids = ",".join(
            str(message_id) for message_id in (document_workflow_message_ids or [])
        )

        session.flush()
        return supply_to_dict(supply)


def clear_acceptance(supply_id):
    with session_scope() as session:
        supply = session.get(ConsumableSupply, int(supply_id))
        if not supply:
            raise RuntimeError("Поставка не найдена.")
        if supply.status != "accepted":
            raise RuntimeError("Эта поставка еще не принята.")

        result = supply_to_dict(supply)
        reverse_supply_acceptance_movements_in_session(session, supply)
        supply.status = "pending"
        supply.accepted_at = None
        supply.accepted_by_user_id = None
        supply.accepted_by_name = None
        supply.layout_photo_file_id = None
        supply.closing_document_file_id = None
        supply.closing_document_kind = None
        supply.topic_message_ids = None
        supply.document_workflow_message_ids = None
        session.flush()
        return result


def reverse_supply_acceptance_movements_in_session(session, supply):
    movements = (
        session.execute(
            select(ConsumableMovement).where(
                ConsumableMovement.source == "supply_acceptance",
                ConsumableMovement.source_id == str(supply.id),
            )
        )
        .scalars()
        .all()
    )
    for movement in movements:
        item = session.get(ConsumableItem, int(movement.item_id))
        if not item:
            continue
        reverse_delta = -1 * float(movement.quantity_delta or 0)
        if reverse_delta == 0:
            continue
        item.current_quantity = float(item.current_quantity or 0) + reverse_delta
        session.add(
            ConsumableMovement(
                item_id=item.item_id,
                quantity_delta=reverse_delta,
                source="supply_acceptance_reversal",
                source_id=str(supply.id),
                comment=f"Удаление приемки расходников: {supply.consumable_name}",
                created_by_user_id=supply.accepted_by_user_id or "",
                created_by_name=supply.accepted_by_name or "",
            )
        )


def split_message_ids(value):
    result = []
    for part in str(value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            continue
    return result


def get_active_suppliers(limit=50):
    return get_recent_organizations(limit=limit)


def deactivate_supplier(name):
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise RuntimeError("Поставщик не найден.")

    with session_scope() as session:
        supplier = (
            session.execute(
                select(ConsumableSupplier).where(ConsumableSupplier.name == normalized_name)
            )
            .scalars()
            .first()
        )

        if supplier:
            supplier.is_active = False
        else:
            session.add(ConsumableSupplier(name=normalized_name, is_active=False))

    return normalized_name


def consumable_item_to_dict(item):
    return {
        "item_id": item.item_id,
        "created_at": item.created_at,
        "name": item.name,
        "unit": item.unit,
        "current_quantity": float(item.current_quantity or 0),
        "is_active": bool(item.is_active),
    }


def movement_to_dict(movement, item_name=""):
    return {
        "movement_id": movement.movement_id,
        "created_at": movement.created_at,
        "item_id": movement.item_id,
        "item_name": item_name,
        "quantity_delta": float(movement.quantity_delta or 0),
        "source": movement.source,
        "source_id": movement.source_id or "",
        "comment": movement.comment or "",
        "created_by_name": movement.created_by_name or "",
    }


def inventory_count_to_dict(record, item_name="", unit="шт"):
    return {
        "count_id": record.count_id,
        "batch_id": record.batch_id or "",
        "created_at": record.created_at,
        "item_id": record.item_id,
        "item_name": item_name,
        "unit": unit,
        "system_quantity": float(record.system_quantity or 0),
        "counted_quantity": float(record.counted_quantity or 0),
        "difference": float(record.difference or 0),
        "counted_by_name": record.counted_by_name or "",
    }


def rule_to_dict(rule, item=None):
    return {
        "rule_id": rule.rule_id,
        "product_id": rule.product_id,
        "product_name": rule.product_name or "",
        "item_id": rule.item_id,
        "item_name": item.name if item else "",
        "unit": item.unit if item else "шт",
        "quantity_per_unit": float(rule.quantity_per_unit or 0),
        "is_active": bool(rule.is_active),
    }


def get_consumable_items(active_only=True):
    with session_scope() as session:
        statement = select(ConsumableItem).order_by(ConsumableItem.name)
        if active_only:
            statement = statement.where(ConsumableItem.is_active.is_(True))
        items = session.execute(statement).scalars().all()
    return [consumable_item_to_dict(item) for item in items]


def get_consumable_item(item_id):
    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        return consumable_item_to_dict(item) if item else None


def upsert_consumable_item(name, unit="шт"):
    normalized_name = str(name or "").strip()
    normalized_unit = str(unit or "шт").strip() or "шт"
    if not normalized_name:
        raise RuntimeError("Название расходника не должно быть пустым.")

    with session_scope() as session:
        item = (
            session.execute(select(ConsumableItem).where(ConsumableItem.name == normalized_name))
            .scalars()
            .first()
        )
        if item:
            item.unit = normalized_unit
            item.is_active = True
        else:
            item = ConsumableItem(name=normalized_name, unit=normalized_unit, current_quantity=0, is_active=True)
            session.add(item)
        session.flush()
        return consumable_item_to_dict(item)


def deactivate_consumable_item(item_id):
    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден или уже удален из учета.")

        item.is_active = False
        rules = (
            session.execute(
                select(ProductConsumableRule).where(
                    ProductConsumableRule.item_id == item.item_id,
                    ProductConsumableRule.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        for rule in rules:
            rule.is_active = False

        session.flush()
        return consumable_item_to_dict(item)


def add_consumable_movement(
    item_id,
    quantity_delta,
    source,
    source_id="",
    comment="",
    created_by_user_id="",
    created_by_name="",
):
    delta = float(quantity_delta or 0)
    if delta == 0:
        raise RuntimeError("Количество движения не должно быть 0.")

    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден.")

        item.current_quantity = float(item.current_quantity or 0) + delta
        movement = ConsumableMovement(
            item_id=item.item_id,
            quantity_delta=delta,
            source=str(source or "").strip(),
            source_id=str(source_id or "").strip(),
            comment=str(comment or "").strip(),
            created_by_user_id=str(created_by_user_id or "").strip(),
            created_by_name=str(created_by_name or "").strip(),
        )
        session.add(movement)
        session.flush()
        return movement_to_dict(movement, item.name)


def get_recent_consumable_movements(limit=20):
    with session_scope() as session:
        movements = (
            session.execute(select(ConsumableMovement).order_by(desc(ConsumableMovement.movement_id)).limit(limit))
            .scalars()
            .all()
        )
        item_ids = {movement.item_id for movement in movements}
        items = {}
        if item_ids:
            item_rows = session.execute(select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))).scalars().all()
            items = {item.item_id: item.name for item in item_rows}
    return [movement_to_dict(movement, items.get(movement.item_id, "")) for movement in movements]


def create_inventory_count(item_id, counted_quantity, counted_by_user_id="", counted_by_name=""):
    counted = float(counted_quantity or 0)
    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден.")
        system_quantity = float(item.current_quantity or 0)
        record = ConsumableInventoryCount(
            item_id=item.item_id,
            system_quantity=system_quantity,
            counted_quantity=counted,
            difference=counted - system_quantity,
            counted_by_user_id=str(counted_by_user_id or "").strip(),
            counted_by_name=str(counted_by_name or "").strip(),
            batch_id=str(uuid4()),
        )
        session.add(record)
        session.flush()
        return inventory_count_to_dict(record, item.name, item.unit)


def create_inventory_count_batch(counts, counted_by_user_id="", counted_by_name=""):
    normalized_counts = {}
    for item_id, quantity in (counts or {}).items():
        try:
            item_id = int(item_id)
            quantity = float(quantity)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or quantity < 0:
            continue
        normalized_counts[item_id] = quantity

    if not normalized_counts:
        raise RuntimeError("Нет заполненных значений пересчета.")

    batch_id = str(uuid4())
    with session_scope() as session:
        items = (
            session.execute(select(ConsumableItem).where(ConsumableItem.item_id.in_(normalized_counts.keys())))
            .scalars()
            .all()
        )
        items_by_id = {item.item_id: item for item in items if item.is_active}
        records = []
        for item_id, counted in normalized_counts.items():
            item = items_by_id.get(item_id)
            if not item:
                continue
            system_quantity = float(item.current_quantity or 0)
            record = ConsumableInventoryCount(
                item_id=item.item_id,
                system_quantity=system_quantity,
                counted_quantity=counted,
                difference=counted - system_quantity,
                counted_by_user_id=str(counted_by_user_id or "").strip(),
                counted_by_name=str(counted_by_name or "").strip(),
                batch_id=batch_id,
            )
            session.add(record)
            session.flush()
            records.append(inventory_count_to_dict(record, item.name, item.unit))
        if not records:
            raise RuntimeError("Расходники для пересчета не найдены.")
        return {"batch_id": batch_id, "records": records}


def get_recent_inventory_counts(limit=20):
    with session_scope() as session:
        records = (
            session.execute(select(ConsumableInventoryCount).order_by(desc(ConsumableInventoryCount.count_id)).limit(limit))
            .scalars()
            .all()
        )
        item_ids = {record.item_id for record in records}
        items = {}
        if item_ids:
            item_rows = session.execute(select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))).scalars().all()
            items = {item.item_id: item for item in item_rows}
    return [
        inventory_count_to_dict(record, items.get(record.item_id).name, items.get(record.item_id).unit)
        if items.get(record.item_id)
        else inventory_count_to_dict(record)
        for record in records
    ]


def get_recent_inventory_batches(limit=10):
    with session_scope() as session:
        records = (
            session.execute(
                select(ConsumableInventoryCount)
                .where(ConsumableInventoryCount.batch_id.is_not(None))
                .order_by(desc(ConsumableInventoryCount.count_id))
                .limit(500)
            )
            .scalars()
            .all()
        )
    batches = []
    seen = set()
    for record in records:
        if not record.batch_id or record.batch_id in seen:
            continue
        seen.add(record.batch_id)
        batches.append(
            {
                "batch_id": record.batch_id,
                "created_at": record.created_at,
                "counted_by_name": record.counted_by_name or "",
            }
        )
        if len(batches) >= limit:
            break
    return batches


def get_inventory_batch_comparison(batch_id):
    with session_scope() as session:
        records = (
            session.execute(
                select(ConsumableInventoryCount)
                .where(ConsumableInventoryCount.batch_id == str(batch_id))
                .order_by(ConsumableInventoryCount.count_id)
            )
            .scalars()
            .all()
        )
        item_ids = {record.item_id for record in records}
        items = {}
        if item_ids:
            item_rows = session.execute(select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))).scalars().all()
            items = {item.item_id: item for item in item_rows}

    result = []
    for record in records:
        item = items.get(record.item_id)
        current_quantity = float(item.current_quantity or 0) if item else float(record.system_quantity or 0)
        counted_quantity = float(record.counted_quantity or 0)
        result.append(
            {
                **inventory_count_to_dict(record, item.name if item else "", item.unit if item else "шт"),
                "current_quantity": current_quantity,
                "current_difference": counted_quantity - current_quantity,
            }
        )
    return result


def apply_inventory_batch_counts(batch_id, item_ids, created_by_user_id="", created_by_name=""):
    selected_ids = {int(item_id) for item_id in (item_ids or [])}
    if not selected_ids:
        return []

    with session_scope() as session:
        records = (
            session.execute(
                select(ConsumableInventoryCount).where(
                    ConsumableInventoryCount.batch_id == str(batch_id),
                    ConsumableInventoryCount.item_id.in_(selected_ids),
                )
            )
            .scalars()
            .all()
        )
        movements = []
        for record in records:
            item = session.get(ConsumableItem, int(record.item_id))
            if not item or not item.is_active:
                continue
            counted = float(record.counted_quantity or 0)
            current = float(item.current_quantity or 0)
            delta = counted - current
            if delta == 0:
                continue
            item.current_quantity = counted
            movement = ConsumableMovement(
                item_id=item.item_id,
                quantity_delta=delta,
                source="inventory_adjustment",
                source_id=str(batch_id),
                comment=f"Корректировка по пересчету: {record.counted_by_name or '-'}",
                created_by_user_id=str(created_by_user_id or "").strip(),
                created_by_name=str(created_by_name or "").strip(),
            )
            session.add(movement)
            session.flush()
            movements.append(movement_to_dict(movement, item.name))
        return movements


def _normalize_file_entries(values, default_kind):
    result = []
    for value in values or []:
        if isinstance(value, dict):
            file_id = str(value.get("file_id") or "").strip()
            kind = str(value.get("kind") or default_kind).strip()
        else:
            file_id = str(value or "").strip()
            kind = default_kind
        if file_id:
            result.append({"file_id": file_id, "kind": kind})
    return result


def _file_entries_from_json(value, default_kind):
    try:
        return _normalize_file_entries(json.loads(value or "[]"), default_kind)
    except (TypeError, ValueError):
        return []


def receipt_to_dict(receipt, item=None):
    return {
        "receipt_id": receipt.receipt_id,
        "created_at": receipt.created_at,
        "item_id": receipt.item_id,
        "item_name": item.name if item else "",
        "unit": item.unit if item else "шт",
        "quantity": float(receipt.quantity or 0),
        "accepted_by_user_id": receipt.accepted_by_user_id or "",
        "accepted_by_name": receipt.accepted_by_name or "",
        "layout_photo_file_ids": _file_entries_from_json(receipt.layout_photo_file_ids, "photo"),
        "closing_document_kind": receipt.closing_document_kind or "no_paper",
        "closing_document_file_ids": _file_entries_from_json(receipt.closing_document_file_ids, "document"),
    }


def create_consumable_receipt(
    item_id,
    quantity,
    accepted_by_user_id,
    accepted_by_name,
    layout_photo_file_ids,
    closing_document_kind="no_paper",
    closing_document_file_ids=None,
):
    quantity = float(quantity or 0)
    layout_photo_file_ids = _normalize_file_entries(layout_photo_file_ids, "photo")
    closing_document_file_ids = _normalize_file_entries(closing_document_file_ids, "document")
    if quantity <= 0:
        raise RuntimeError("Количество должно быть больше нуля.")
    if not layout_photo_file_ids:
        raise RuntimeError("Добавьте хотя бы одно фото принятого расходника.")
    if closing_document_kind not in {"no_paper", "scan", "photo"}:
        raise RuntimeError("Выберите тип закрывающего документа.")
    if closing_document_kind != "no_paper" and not closing_document_file_ids:
        raise RuntimeError("Добавьте закрывающий документ.")

    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден.")
        receipt = ConsumableReceipt(
            receipt_id=f"receipt_{uuid4().hex[:12]}",
            item_id=item.item_id,
            quantity=quantity,
            accepted_by_user_id=str(accepted_by_user_id or ""),
            accepted_by_name=str(accepted_by_name or ""),
            layout_photo_file_ids=json.dumps(layout_photo_file_ids),
            closing_document_kind=closing_document_kind,
            closing_document_file_ids=json.dumps(closing_document_file_ids),
        )
        session.add(receipt)
        item.current_quantity = float(item.current_quantity or 0) + quantity
        movement = ConsumableMovement(
            item_id=item.item_id,
            quantity_delta=quantity,
            source="consumable_receipt",
            source_id=receipt.receipt_id,
            comment="Приемка расходника на складе",
            created_by_user_id=str(accepted_by_user_id or ""),
            created_by_name=str(accepted_by_name or ""),
        )
        session.add(movement)
        session.flush()
        return receipt_to_dict(receipt, item)


def get_consumable_receipt(receipt_id):
    with session_scope() as session:
        receipt = session.get(ConsumableReceipt, str(receipt_id))
        item = session.get(ConsumableItem, receipt.item_id) if receipt else None
        return receipt_to_dict(receipt, item) if receipt else None


def list_consumable_receipts(limit=30):
    with session_scope() as session:
        receipts = (
            session.execute(
                select(ConsumableReceipt)
                .order_by(desc(ConsumableReceipt.created_at))
                .limit(limit)
            )
            .scalars()
            .all()
        )
        item_ids = {receipt.item_id for receipt in receipts}
        items = {}
        if item_ids:
            items = {
                item.item_id: item
                for item in session.execute(
                    select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))
                ).scalars().all()
            }
    return [receipt_to_dict(receipt, items.get(receipt.item_id)) for receipt in receipts]


def update_consumable_receipt_quantity(receipt_id, quantity, changed_by_user_id="", changed_by_name=""):
    quantity = float(quantity or 0)
    if quantity <= 0:
        raise RuntimeError("Количество должно быть больше нуля.")
    with session_scope() as session:
        receipt = session.get(ConsumableReceipt, str(receipt_id))
        if not receipt:
            return None
        item = session.get(ConsumableItem, receipt.item_id)
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден.")
        previous_quantity = float(receipt.quantity or 0)
        delta = quantity - previous_quantity
        receipt.quantity = quantity
        if delta:
            item.current_quantity = float(item.current_quantity or 0) + delta
            session.add(
                ConsumableMovement(
                    item_id=item.item_id,
                    quantity_delta=delta,
                    source="consumable_receipt_adjustment",
                    source_id=receipt.receipt_id,
                    comment="Изменение количества приемки расходников",
                    created_by_user_id=str(changed_by_user_id or ""),
                    created_by_name=str(changed_by_name or ""),
                )
            )
        session.flush()
        return receipt_to_dict(receipt, item)


def delete_consumable_receipt(receipt_id, deleted_by_user_id="", deleted_by_name=""):
    with session_scope() as session:
        receipt = session.get(ConsumableReceipt, str(receipt_id))
        if not receipt:
            return None
        item = session.get(ConsumableItem, receipt.item_id)
        result = receipt_to_dict(receipt, item)
        if item:
            quantity = float(receipt.quantity or 0)
            item.current_quantity = float(item.current_quantity or 0) - quantity
            session.add(
                ConsumableMovement(
                    item_id=item.item_id,
                    quantity_delta=-quantity,
                    source="consumable_receipt_reversal",
                    source_id=receipt.receipt_id,
                    comment="Удаление приемки расходников",
                    created_by_user_id=str(deleted_by_user_id or ""),
                    created_by_name=str(deleted_by_name or ""),
                )
            )
        session.delete(receipt)
        return result


def _inventory_session_to_dict(session, participant_count=0):
    return {
        "session_id": session.session_id,
        "status": session.status,
        "created_at": session.created_at,
        "created_by_user_id": session.created_by_user_id or "",
        "created_by_name": session.created_by_name or "",
        "completed_at": session.completed_at,
        "completed_by_user_id": session.completed_by_user_id or "",
        "completed_by_name": session.completed_by_name or "",
        "applied_at": session.applied_at,
        "applied_by_user_id": session.applied_by_user_id or "",
        "applied_by_name": session.applied_by_name or "",
        "participant_count": participant_count,
    }


def _add_inventory_session_participant(session, session_id, user_id, user_name):
    user_id = str(user_id or "").strip()
    if not user_id:
        return
    participant = session.execute(
        select(ConsumableInventorySessionParticipant).where(
            ConsumableInventorySessionParticipant.session_id == session_id,
            ConsumableInventorySessionParticipant.user_id == user_id,
        )
    ).scalars().first()
    if not participant:
        session.add(
            ConsumableInventorySessionParticipant(
                session_id=session_id,
                user_id=user_id,
                user_name=str(user_name or ""),
            )
        )


def get_or_create_active_inventory_session(user_id="", user_name=""):
    with session_scope() as session:
        pending_review = session.execute(
            select(ConsumableInventorySession)
            .where(ConsumableInventorySession.status == "pending_review")
            .order_by(ConsumableInventorySession.completed_at.desc())
            .limit(1)
        ).scalars().first()
        if pending_review:
            raise RuntimeError("Текущий пересчет ожидает проверки руководителем.")

        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(ConsumableInventorySession.status == "draft")
            .order_by(ConsumableInventorySession.created_at.desc())
            .limit(1)
        ).scalars().first()
        if not inventory_session:
            inventory_session = ConsumableInventorySession(
                session_id=f"inventory_{uuid4().hex[:12]}",
                status="draft",
                created_by_user_id=str(user_id or ""),
                created_by_name=str(user_name or ""),
            )
            session.add(inventory_session)
            session.flush()
            items = session.execute(
                select(ConsumableItem)
                .where(ConsumableItem.is_active.is_(True))
                .order_by(ConsumableItem.name)
            ).scalars().all()
            for item in items:
                session.add(
                    ConsumableInventorySessionItem(
                        session_id=inventory_session.session_id,
                        item_id=item.item_id,
                        system_quantity=float(item.current_quantity or 0),
                    )
                )
        _add_inventory_session_participant(
            session,
            inventory_session.session_id,
            user_id,
            user_name,
        )
        session.flush()
        participant_count = session.execute(
            select(ConsumableInventorySessionParticipant.id).where(
                ConsumableInventorySessionParticipant.session_id == inventory_session.session_id
            )
        ).all()
        return _inventory_session_to_dict(inventory_session, len(participant_count))


def get_inventory_session(session_id, user_id="", user_name=""):
    with session_scope() as session:
        inventory_session = session.get(ConsumableInventorySession, str(session_id))
        if not inventory_session:
            return None
        if inventory_session.status == "draft":
            _add_inventory_session_participant(session, inventory_session.session_id, user_id, user_name)
        session.flush()
        participant_count = session.execute(
            select(ConsumableInventorySessionParticipant.id).where(
                ConsumableInventorySessionParticipant.session_id == inventory_session.session_id
            )
        ).all()
        return _inventory_session_to_dict(inventory_session, len(participant_count))


def get_inventory_session_items(session_id):
    with session_scope() as session:
        records = session.execute(
            select(ConsumableInventorySessionItem)
            .where(ConsumableInventorySessionItem.session_id == str(session_id))
            .order_by(ConsumableInventorySessionItem.item_id)
        ).scalars().all()
        item_ids = {record.item_id for record in records}
        items = {}
        if item_ids:
            items = {
                item.item_id: item
                for item in session.execute(
                    select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))
                ).scalars().all()
            }
    result = []
    for record in records:
        item = items.get(record.item_id)
        counted = record.counted_quantity is not None
        system_quantity = float(record.system_quantity or 0)
        counted_quantity = float(record.counted_quantity or 0) if counted else None
        result.append(
            {
                "item_id": record.item_id,
                "item_name": item.name if item else "",
                "unit": item.unit if item else "шт",
                "system_quantity": system_quantity,
                "counted_quantity": counted_quantity,
                "difference": (counted_quantity - system_quantity) if counted else None,
                "counted": counted,
                "counted_by_user_id": record.counted_by_user_id or "",
                "counted_by_name": record.counted_by_name or "",
                "counted_at": record.counted_at,
            }
        )
    return result


def save_inventory_session_item(session_id, item_id, counted_quantity, user_id="", user_name=""):
    quantity = float(counted_quantity)
    if quantity < 0:
        raise RuntimeError("Количество не может быть отрицательным.")
    with session_scope() as session:
        inventory_session = session.get(ConsumableInventorySession, str(session_id))
        if not inventory_session or inventory_session.status != "draft":
            raise RuntimeError("Пересчет не найден или уже завершен.")
        record = session.execute(
            select(ConsumableInventorySessionItem).where(
                ConsumableInventorySessionItem.session_id == inventory_session.session_id,
                ConsumableInventorySessionItem.item_id == int(item_id),
            )
        ).scalars().first()
        if not record:
            raise RuntimeError("Расходник не найден в пересчете.")
        _add_inventory_session_participant(session, inventory_session.session_id, user_id, user_name)
        previous_quantity = record.counted_quantity
        now_value = datetime.now()
        record.counted_quantity = quantity
        record.counted_by_user_id = str(user_id or "")
        record.counted_by_name = str(user_name or "")
        record.counted_at = now_value
        record.updated_at = now_value
        session.add(
            ConsumableInventorySessionChange(
                session_id=inventory_session.session_id,
                item_id=int(item_id),
                previous_quantity=previous_quantity,
                new_quantity=quantity,
                changed_by_user_id=str(user_id or ""),
                changed_by_name=str(user_name or ""),
            )
        )
        item = session.get(ConsumableItem, int(item_id))
        session.flush()
        return {
            "item_id": int(item_id),
            "item_name": item.name if item else "",
            "quantity": quantity,
            "counted_by_name": str(user_name or ""),
        }


def complete_inventory_session(session_id, user_id="", user_name=""):
    with session_scope() as session:
        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(
                ConsumableInventorySession.session_id == str(session_id),
                ConsumableInventorySession.status == "draft",
            )
            .with_for_update()
        ).scalars().first()
        if not inventory_session:
            raise RuntimeError("Пересчет не найден или уже завершен.")
        records = session.execute(
            select(ConsumableInventorySessionItem).where(
                ConsumableInventorySessionItem.session_id == inventory_session.session_id,
                ConsumableInventorySessionItem.counted_quantity.is_not(None),
            )
        ).scalars().all()
        if not records:
            raise RuntimeError("Нельзя завершить пересчет без посчитанных позиций.")

        result_records = []
        for record in records:
            item = session.get(ConsumableItem, record.item_id)
            if not item or not item.is_active:
                continue
            counted_quantity = float(record.counted_quantity or 0)
            system_quantity = float(record.system_quantity or 0)
            result_records.append(
                {
                    "item_id": item.item_id,
                    "item_name": item.name,
                    "unit": item.unit,
                    "system_quantity": system_quantity,
                    "counted_quantity": counted_quantity,
                    "difference": counted_quantity - system_quantity,
                    "counted_by_user_id": record.counted_by_user_id or "",
                    "counted_by_name": record.counted_by_name or "",
                }
            )

        inventory_session.status = "pending_review"
        inventory_session.completed_at = datetime.now()
        inventory_session.completed_by_user_id = str(user_id or "")
        inventory_session.completed_by_name = str(user_name or "")
        _add_inventory_session_participant(session, inventory_session.session_id, user_id, user_name)
        session.flush()
        return {
            "session": _inventory_session_to_dict(inventory_session),
            "records": result_records,
            "movements": [],
        }


def list_completed_inventory_sessions(limit=20):
    with session_scope() as session:
        sessions = session.execute(
            select(ConsumableInventorySession)
            .where(ConsumableInventorySession.status.in_(["applied", "completed"]))
            .order_by(ConsumableInventorySession.completed_at.desc())
            .limit(limit)
        ).scalars().all()
    return [_inventory_session_to_dict(inventory_session) for inventory_session in sessions]


def get_completed_inventory_session_records(session_id):
    return get_inventory_batch_comparison(session_id)


def get_pending_inventory_session():
    with session_scope() as session:
        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(ConsumableInventorySession.status == "pending_review")
            .order_by(ConsumableInventorySession.completed_at.desc())
            .limit(1)
        ).scalars().first()
        if not inventory_session:
            return None
        participant_count = session.execute(
            select(ConsumableInventorySessionParticipant.id).where(
                ConsumableInventorySessionParticipant.session_id == inventory_session.session_id
            )
        ).all()
        return _inventory_session_to_dict(inventory_session, len(participant_count))


def update_inventory_review_item(session_id, item_id, counted_quantity, user_id="", user_name=""):
    quantity = float(counted_quantity)
    if quantity < 0:
        raise RuntimeError("Количество не может быть отрицательным.")
    with session_scope() as session:
        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(
                ConsumableInventorySession.session_id == str(session_id),
                ConsumableInventorySession.status == "pending_review",
            )
            .with_for_update()
        ).scalars().first()
        if not inventory_session:
            raise RuntimeError("Пересчет не найден или уже обработан.")
        record = session.execute(
            select(ConsumableInventorySessionItem).where(
                ConsumableInventorySessionItem.session_id == inventory_session.session_id,
                ConsumableInventorySessionItem.item_id == int(item_id),
            )
        ).scalars().first()
        if not record:
            raise RuntimeError("Расходник не найден в пересчете.")
        previous_quantity = record.counted_quantity
        record.counted_quantity = quantity
        record.counted_by_user_id = str(user_id or "")
        record.counted_by_name = str(user_name or "")
        record.counted_at = datetime.now()
        record.updated_at = datetime.now()
        session.add(
            ConsumableInventorySessionChange(
                session_id=inventory_session.session_id,
                item_id=int(item_id),
                previous_quantity=previous_quantity,
                new_quantity=quantity,
                changed_by_user_id=str(user_id or ""),
                changed_by_name=str(user_name or ""),
            )
        )
        return True


def return_inventory_session_to_draft(session_id):
    with session_scope() as session:
        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(
                ConsumableInventorySession.session_id == str(session_id),
                ConsumableInventorySession.status == "pending_review",
            )
            .with_for_update()
        ).scalars().first()
        if not inventory_session:
            return False
        inventory_session.status = "draft"
        inventory_session.completed_at = None
        inventory_session.completed_by_user_id = None
        inventory_session.completed_by_name = None
        return True


def apply_inventory_session(session_id, user_id="", user_name=""):
    with session_scope() as session:
        inventory_session = session.execute(
            select(ConsumableInventorySession)
            .where(
                ConsumableInventorySession.session_id == str(session_id),
                ConsumableInventorySession.status == "pending_review",
            )
            .with_for_update()
        ).scalars().first()
        if not inventory_session:
            raise RuntimeError("Пересчет не найден, уже применен или возвращен на исправление.")

        records = session.execute(
            select(ConsumableInventorySessionItem).where(
                ConsumableInventorySessionItem.session_id == inventory_session.session_id,
                ConsumableInventorySessionItem.counted_quantity.is_not(None),
            )
        ).scalars().all()
        if not records:
            raise RuntimeError("В пересчете нет заполненных позиций.")

        result_records = []
        movements = []
        for record in records:
            item = session.get(ConsumableItem, record.item_id)
            if not item or not item.is_active:
                continue
            counted_quantity = float(record.counted_quantity or 0)
            system_quantity = float(record.system_quantity or 0)
            final_record = ConsumableInventoryCount(
                item_id=item.item_id,
                system_quantity=system_quantity,
                counted_quantity=counted_quantity,
                difference=counted_quantity - system_quantity,
                counted_by_user_id=record.counted_by_user_id,
                counted_by_name=record.counted_by_name,
                batch_id=inventory_session.session_id,
            )
            session.add(final_record)

            current_quantity = float(item.current_quantity or 0)
            delta = counted_quantity - current_quantity
            if delta:
                item.current_quantity = counted_quantity
                movement = ConsumableMovement(
                    item_id=item.item_id,
                    quantity_delta=delta,
                    source="inventory_adjustment",
                    source_id=inventory_session.session_id,
                    comment="Корректировка после проверки пересчета руководителем",
                    created_by_user_id=str(user_id or ""),
                    created_by_name=str(user_name or ""),
                )
                session.add(movement)
                movements.append((movement, item.name))
            session.flush()
            result_records.append(inventory_count_to_dict(final_record, item.name, item.unit))

        inventory_session.status = "applied"
        inventory_session.applied_at = datetime.now()
        inventory_session.applied_by_user_id = str(user_id or "")
        inventory_session.applied_by_name = str(user_name or "")
        session.flush()
        return {
            "session": _inventory_session_to_dict(inventory_session),
            "records": result_records,
            "movements": [movement_to_dict(movement, item_name) for movement, item_name in movements],
        }


def discard_inventory_session(session_id):
    with session_scope() as session:
        inventory_session = session.get(ConsumableInventorySession, str(session_id))
        if not inventory_session or inventory_session.status != "draft":
            return False
        session.execute(
            delete(ConsumableInventorySessionChange).where(
                ConsumableInventorySessionChange.session_id == inventory_session.session_id
            )
        )
        session.execute(
            delete(ConsumableInventorySessionParticipant).where(
                ConsumableInventorySessionParticipant.session_id == inventory_session.session_id
            )
        )
        session.execute(
            delete(ConsumableInventorySessionItem).where(
                ConsumableInventorySessionItem.session_id == inventory_session.session_id
            )
        )
        session.delete(inventory_session)
        return True


def set_product_consumable_rule(product_id, product_name, item_id, quantity_per_unit):
    quantity = float(quantity_per_unit or 0)
    if quantity <= 0:
        raise RuntimeError("Норма должна быть больше 0.")

    with session_scope() as session:
        item = session.get(ConsumableItem, int(item_id))
        if not item or not item.is_active:
            raise RuntimeError("Расходник не найден.")

        rule = (
            session.execute(
                select(ProductConsumableRule).where(
                    ProductConsumableRule.product_id == str(product_id),
                    ProductConsumableRule.item_id == int(item_id),
                )
            )
            .scalars()
            .first()
        )
        if rule:
            rule.product_name = str(product_name or "").strip()
            rule.quantity_per_unit = quantity
            rule.is_active = True
        else:
            rule = ProductConsumableRule(
                product_id=str(product_id),
                product_name=str(product_name or "").strip(),
                item_id=int(item_id),
                quantity_per_unit=quantity,
                is_active=True,
            )
            session.add(rule)
        session.flush()
        return rule_to_dict(rule, item)


def get_product_consumable_rules(product_id, active_only=True):
    with session_scope() as session:
        statement = select(ProductConsumableRule).where(ProductConsumableRule.product_id == str(product_id))
        if active_only:
            statement = statement.where(ProductConsumableRule.is_active.is_(True))
        rules = session.execute(statement.order_by(ProductConsumableRule.rule_id)).scalars().all()
        item_ids = {rule.item_id for rule in rules}
        items = {}
        if item_ids:
            item_rows = session.execute(select(ConsumableItem).where(ConsumableItem.item_id.in_(item_ids))).scalars().all()
            items = {item.item_id: item for item in item_rows}
    return [rule_to_dict(rule, items.get(rule.item_id)) for rule in rules]


def update_product_consumable_rules_name(product_id, product_name):
    product_name = str(product_name or "").strip()
    if not product_name:
        raise ValueError("Название товара не должно быть пустым.")
    with session_scope() as session:
        rules = session.execute(
            select(ProductConsumableRule).where(
                ProductConsumableRule.product_id == str(product_id)
            )
        ).scalars().all()
        for rule in rules:
            rule.product_name = product_name
        return len(rules)


def apply_receiving_consumable_usage(
    product_id,
    product_name,
    packed_quantity,
    source_id="",
    created_by_user_id="",
    created_by_name="",
):
    quantity = int(packed_quantity or 0)
    if quantity <= 0:
        return []

    with session_scope() as session:
        rules = (
            session.execute(
                select(ProductConsumableRule).where(
                    ProductConsumableRule.product_id == str(product_id),
                    ProductConsumableRule.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        if not rules:
            return []

        movements = []
        for rule in rules:
            item = session.get(ConsumableItem, int(rule.item_id))
            if not item or not item.is_active:
                continue

            delta = -1 * float(rule.quantity_per_unit or 0) * quantity
            if delta == 0:
                continue

            item.current_quantity = float(item.current_quantity or 0) + delta
            movement = ConsumableMovement(
                item_id=item.item_id,
                quantity_delta=delta,
                source="receiving",
                source_id=str(source_id or "").strip(),
                comment=f"Оприходование: {product_name}, упаковано {quantity}",
                created_by_user_id=str(created_by_user_id or "").strip(),
                created_by_name=str(created_by_name or "").strip(),
            )
            session.add(movement)
            session.flush()
            movements.append(movement_to_dict(movement, item.name))

    return movements


def format_quantity(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")
