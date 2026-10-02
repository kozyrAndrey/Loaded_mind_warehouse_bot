from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class ShippingCode(Base):
    __tablename__ = "shipping_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    short_code: Mapped[str] = mapped_column(String(31), unique=True, nullable=False, index=True)
    gtin: Mapped[str] = mapped_column(String(14), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    moysklad_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    assortment_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    product_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    employee_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    employee_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class ShippingExportBatch(Base):
    __tablename__ = "shipping_export_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="EXPORTED", index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(100))
    confirmed_by_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class ShippingExportItem(Base):
    __tablename__ = "shipping_export_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "code_id", name="uq_shipping_export_batch_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("shipping_export_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    code_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    short_code: Mapped[str] = mapped_column(String(31), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


def init_shipping_storage():
    Base.metadata.create_all(
        get_engine(),
        tables=[
            ShippingCode.__table__,
            ShippingExportBatch.__table__,
            ShippingExportItem.__table__,
        ],
    )


def save_shipping(order, codes, user_id, employee_name):
    codes = list(codes or [])
    short_codes = [str(row.get("short_code") or "") for row in codes]
    if any(len(code) != 31 for code in short_codes):
        raise ValueError("Короткий код маркировки должен содержать 31 символ.")
    if len(short_codes) != len(set(short_codes)):
        raise ValueError("В одной отгрузке обнаружены повторяющиеся коды маркировки.")

    now = datetime.now()
    with session_scope() as session:
        existing = {
            row.short_code: row
            for row in session.scalars(
                select(ShippingCode).where(ShippingCode.short_code.in_(short_codes))
            ).all()
        } if short_codes else {}
        for item in codes:
            short_code = str(item["short_code"])
            row = existing.get(short_code)
            if row is None:
                row = ShippingCode(short_code=short_code)
                session.add(row)
            else:
                row.revision += 1
            row.gtin = str(item["gtin"])
            row.sale_price = Decimal(str(item["price"]))
            row.moysklad_order_id = str(order["id"])
            row.order_name = str(order["name"])
            row.position_id = str(item.get("position_id") or "")
            row.assortment_id = str(item.get("assortment_id") or "")
            row.product_name = str(item.get("product_name") or "")
            row.employee_user_id = str(user_id or "")
            row.employee_name = str(employee_name or "")
            row.created_at = now
            row.updated_at = now
        session.flush()
    return len(codes)


def create_export_snapshot(user_id, employee_name):
    with session_scope() as session:
        codes = session.scalars(
            select(ShippingCode).order_by(ShippingCode.created_at, ShippingCode.id)
        ).all()
        if not codes:
            return None
        batch = ShippingExportBatch(
            status="EXPORTED",
            created_by_user_id=str(user_id or ""),
            created_by_name=str(employee_name or ""),
        )
        session.add(batch)
        session.flush()
        rows = []
        for code in codes:
            session.add(
                ShippingExportItem(
                    batch_id=batch.id,
                    code_id=code.id,
                    code_revision=code.revision,
                    short_code=code.short_code,
                    sale_price=code.sale_price,
                )
            )
            rows.append({"short_code": code.short_code, "sale_price": code.sale_price})
        return {"batch_id": batch.id, "rows": rows}


def latest_pending_export():
    with session_scope() as session:
        batch = session.scalars(
            select(ShippingExportBatch)
            .where(ShippingExportBatch.status == "EXPORTED")
            .order_by(ShippingExportBatch.id.desc())
            .limit(1)
        ).first()
        if not batch:
            return None
        count = len(session.scalars(
            select(ShippingExportItem.id).where(ShippingExportItem.batch_id == batch.id)
        ).all())
        return {"batch_id": batch.id, "count": count, "created_at": batch.created_at}


def fail_export_snapshot(batch_id):
    with session_scope() as session:
        batch = session.get(ShippingExportBatch, int(batch_id))
        if batch and batch.status == "EXPORTED":
            batch.status = "FAILED"
            return True
    return False


def confirm_export_retired(batch_id, user_id, employee_name):
    now = datetime.now()
    with session_scope() as session:
        batch = session.get(ShippingExportBatch, int(batch_id))
        if not batch:
            raise ValueError("Выгрузка не найдена.")
        if batch.status == "CONFIRMED":
            return {"deleted": 0, "preserved": 0, "already_confirmed": True}
        if batch.status != "EXPORTED":
            raise ValueError("Эта выгрузка недоступна для подтверждения.")

        items = session.scalars(
            select(ShippingExportItem).where(ShippingExportItem.batch_id == batch.id)
        ).all()
        deleted_count = 0
        for item in items:
            result = session.execute(
                delete(ShippingCode).where(
                    ShippingCode.id == item.code_id,
                    ShippingCode.revision == item.code_revision,
                )
            )
            deleted_count += int(result.rowcount or 0)
        batch.status = "CONFIRMED"
        batch.confirmed_by_user_id = str(user_id or "")
        batch.confirmed_by_name = str(employee_name or "")
        batch.confirmed_at = now
        return {
            "deleted": deleted_count,
            "preserved": len(items) - deleted_count,
            "already_confirmed": False,
        }


def active_codes_count():
    with session_scope() as session:
        return len(session.scalars(select(ShippingCode.id)).all())
