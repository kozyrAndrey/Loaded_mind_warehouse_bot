import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, select, text
from sqlalchemy.orm import Mapped, mapped_column

from config import BASE_DIR
from modules.storage.postgres import Base, get_engine, session_scope


CATALOG_SEED_PATH = BASE_DIR / "resources" / "honest_sign_products.csv"
GTIN_LENGTHS = {8, 12, 13, 14}


class HonestSignProduct(Base):
    __tablename__ = "honest_sign_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gtin: Mapped[str | None] = mapped_column(String(14), unique=True)
    honest_sign_name: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


def normalize_gtin(value):
    gtin = str(value or "").strip()
    if not gtin:
        raise ValueError("GTIN не указан.")
    if not gtin.isdigit():
        raise ValueError("GTIN должен содержать только цифры.")
    if len(gtin) not in GTIN_LENGTHS:
        raise ValueError("GTIN должен содержать 8, 12, 13 или 14 цифр.")
    return gtin.zfill(14)


def init_marking_storage():
    Base.metadata.create_all(get_engine(), tables=[HonestSignProduct.__table__])
    ensure_marking_storage_schema()
    seed_honest_sign_products_if_empty()


def ensure_marking_storage_schema():
    statements = [
        "alter table honest_sign_products add column if not exists id integer",
        "create sequence if not exists honest_sign_products_id_seq",
        (
            "alter sequence honest_sign_products_id_seq "
            "owned by honest_sign_products.id"
        ),
        (
            "alter table honest_sign_products alter column id "
            "set default nextval('honest_sign_products_id_seq')"
        ),
        (
            "update honest_sign_products "
            "set id = nextval('honest_sign_products_id_seq') where id is null"
        ),
        (
            "select setval("
            "'honest_sign_products_id_seq', "
            "greatest(coalesce(max(id), 0), 1), "
            "coalesce(max(id), 0) > 0"
            ") from honest_sign_products"
        ),
        "alter table honest_sign_products alter column id set not null",
        "alter table honest_sign_products add column if not exists size varchar(100)",
        """
        do $$
        declare primary_key_name text;
        begin
            select constraint_name
            into primary_key_name
            from information_schema.table_constraints
            where table_schema = current_schema()
              and table_name = 'honest_sign_products'
              and constraint_type = 'PRIMARY KEY'
              and constraint_name in (
                  select constraint_name
                  from information_schema.key_column_usage
                  where table_schema = current_schema()
                    and table_name = 'honest_sign_products'
                    and column_name = 'gtin'
              )
            limit 1;

            if primary_key_name is not null then
                execute format(
                    'alter table honest_sign_products drop constraint %I',
                    primary_key_name
                );
            end if;
        end
        $$;
        """,
        """
        do $$
        begin
            if not exists (
                select 1
                from information_schema.table_constraints
                where table_schema = current_schema()
                  and table_name = 'honest_sign_products'
                  and constraint_type = 'PRIMARY KEY'
            ) then
                alter table honest_sign_products
                    add constraint honest_sign_products_pkey primary key (id);
            end if;
        end
        $$;
        """,
        "alter table honest_sign_products alter column gtin drop not null",
        (
            "create unique index if not exists uq_honest_sign_products_gtin "
            "on honest_sign_products (gtin) where gtin is not null"
        ),
        (
            "create unique index if not exists uq_honest_sign_products_unmarked "
            "on honest_sign_products (lower(honest_sign_name), coalesce(size, '')) "
            "where gtin is null"
        ),
    ]
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def seed_honest_sign_products_if_empty(seed_path=CATALOG_SEED_PATH):
    seed_path = Path(seed_path)
    if not seed_path.exists():
        return 0

    with seed_path.open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    marked_rows = [row for row in rows if str(row.get("gtin") or "").strip()]
    unmarked_rows = [row for row in rows if not str(row.get("gtin") or "").strip()]
    with session_scope() as session:
        has_marked_products = session.execute(
            select(HonestSignProduct.id).where(HonestSignProduct.gtin.is_not(None)).limit(1)
        ).first()

    imported = 0
    if not has_marked_products:
        imported += upsert_honest_sign_products(marked_rows)
    imported += seed_unmarked_products(unmarked_rows)
    return imported


def upsert_honest_sign_products(rows):
    normalized_rows = []
    seen_gtins = set()
    seen_unmarked = set()
    for row in rows:
        raw_gtin = str(row.get("gtin") or "").strip()
        gtin = normalize_gtin(raw_gtin) if raw_gtin else None
        name = str(row.get("honest_sign_name") or "").strip()
        size = str(row.get("size") or "").strip()
        if not name:
            label = f"GTIN {gtin}" if gtin else "немаркируемого товара"
            raise ValueError(f"Для {label} не указано название.")
        if gtin and gtin in seen_gtins:
            raise ValueError(f"GTIN {gtin} повторяется в импортируемом справочнике.")
        unmarked_key = (name.casefold(), size.casefold())
        if not gtin and unmarked_key in seen_unmarked:
            raise ValueError(f"Немаркируемый товар «{name}» размера «{size}» повторяется.")
        if gtin:
            seen_gtins.add(gtin)
        else:
            seen_unmarked.add(unmarked_key)
        normalized_rows.append((gtin, name, size))

    now = datetime.now()
    with session_scope() as session:
        marked_gtins = [gtin for gtin, _, _ in normalized_rows if gtin]
        existing_marked = {
            product.gtin: product
            for product in session.execute(
                select(HonestSignProduct).where(HonestSignProduct.gtin.in_(marked_gtins))
            ).scalars()
        } if marked_gtins else {}
        existing_unmarked = {
            (product.honest_sign_name.casefold(), (product.size or "").casefold()): product
            for product in session.execute(
                select(HonestSignProduct).where(HonestSignProduct.gtin.is_(None))
            ).scalars()
        }
        for gtin, name, size in normalized_rows:
            product = (
                existing_marked.get(gtin)
                if gtin
                else existing_unmarked.get((name.casefold(), size.casefold()))
            )
            if product:
                product.honest_sign_name = name
                product.size = size or None
                product.updated_at = now
            else:
                session.add(
                    HonestSignProduct(
                        gtin=gtin,
                        honest_sign_name=name,
                        size=size or None,
                        created_at=now,
                        updated_at=now,
                    )
                )
    return len(normalized_rows)


def upsert_honest_sign_product(gtin, honest_sign_name):
    normalized_gtin = normalize_gtin(gtin)
    name = str(honest_sign_name or "").strip()
    if not name:
        raise ValueError("Название Честного ЗНАКа не должно быть пустым.")

    now = datetime.now()
    with session_scope() as session:
        product = session.execute(
            select(HonestSignProduct).where(HonestSignProduct.gtin == normalized_gtin)
        ).scalar_one_or_none()
        created = product is None
        if product:
            product.honest_sign_name = name
            product.updated_at = now
        else:
            product = HonestSignProduct(
                gtin=normalized_gtin,
                honest_sign_name=name,
                created_at=now,
                updated_at=now,
            )
            session.add(product)
        session.flush()
        return honest_sign_product_to_dict(product), created


def get_honest_sign_product(gtin):
    normalized_gtin = normalize_gtin(gtin)
    with session_scope() as session:
        product = session.execute(
            select(HonestSignProduct).where(HonestSignProduct.gtin == normalized_gtin)
        ).scalar_one_or_none()
        return honest_sign_product_to_dict(product) if product else None


def get_honest_sign_names(gtins):
    normalized = []
    for gtin in gtins:
        try:
            normalized.append(normalize_gtin(gtin))
        except ValueError:
            continue
    if not normalized:
        return {}

    with session_scope() as session:
        products = session.execute(
            select(HonestSignProduct).where(HonestSignProduct.gtin.in_(set(normalized)))
        ).scalars().all()
    return {product.gtin: product.honest_sign_name for product in products}


def list_honest_sign_products():
    with session_scope() as session:
        products = session.execute(
            select(HonestSignProduct).order_by(
                HonestSignProduct.gtin.is_(None),
                HonestSignProduct.gtin,
                HonestSignProduct.honest_sign_name,
            )
        ).scalars().all()
        return [honest_sign_product_to_dict(product) for product in products]


def list_unmarked_products():
    with session_scope() as session:
        products = session.execute(
            select(HonestSignProduct)
            .where(HonestSignProduct.gtin.is_(None))
            .order_by(HonestSignProduct.honest_sign_name)
        ).scalars().all()
        return [honest_sign_product_to_dict(product) for product in products]


def get_unmarked_product(product_id):
    with session_scope() as session:
        product = session.get(HonestSignProduct, int(product_id))
        if not product or product.gtin is not None:
            return None
        return honest_sign_product_to_dict(product)


def seed_unmarked_products(rows):
    if not rows:
        return 0
    with session_scope() as session:
        existing = {
            (product.honest_sign_name.casefold(), (product.size or "").casefold())
            for product in session.execute(
                select(HonestSignProduct).where(HonestSignProduct.gtin.is_(None))
            ).scalars()
        }
    missing = [
        row
        for row in rows
        if (
            str(row.get("honest_sign_name") or "").strip().casefold(),
            str(row.get("size") or "").strip().casefold(),
        )
        not in existing
    ]
    if not missing:
        return 0
    return upsert_honest_sign_products(missing)


def delete_honest_sign_product(gtin):
    normalized_gtin = normalize_gtin(gtin)
    with session_scope() as session:
        product = session.execute(
            select(HonestSignProduct).where(HonestSignProduct.gtin == normalized_gtin)
        ).scalar_one_or_none()
        if not product:
            return False
        session.delete(product)
    return True


def honest_sign_product_to_dict(product):
    return {
        "id": product.id,
        "gtin": product.gtin,
        "honest_sign_name": product.honest_sign_name,
        "size": product.size or "",
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }
