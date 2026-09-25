from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, desc, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column

from modules.moysklad.search import compact_product_name
from modules.receiving.products import CATEGORIES
from modules.storage.postgres import Base, check_connection, get_engine, session_scope


class IncomingGood(Base):
    __tablename__ = "incoming_goods"
    __table_args__ = (
        Index("ix_incoming_goods_record_date", "record_date"),
        Index("ix_incoming_goods_product_id", "product_id"),
        Index("ix_incoming_goods_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(255))
    category_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    packed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defective: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rework: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime)
    exported_by: Mapped[str | None] = mapped_column(String(255))
    export_id: Mapped[str | None] = mapped_column(String(100))
    export_chat_id: Mapped[str | None] = mapped_column(String(100))
    export_thread_id: Mapped[str | None] = mapped_column(String(100))
    export_message_ids: Mapped[str | None] = mapped_column(String(1000))


class SpecialReceivingReport(Base):
    __tablename__ = "special_receiving_reports"
    __table_args__ = (
        Index("ix_special_receiving_reports_type", "report_type"),
        Index("ix_special_receiving_reports_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    store_from: Mapped[str | None] = mapped_column(String(255))
    removal_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str | None] = mapped_column(String(255))
    chat_id: Mapped[str | None] = mapped_column(String(100))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    message_ids: Mapped[str | None] = mapped_column(String(1000))


class SpecialReceivingReportItem(Base):
    __tablename__ = "special_receiving_report_items"
    __table_args__ = (
        Index("ix_special_receiving_report_items_report_id", "report_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("special_receiving_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id: Mapped[str] = mapped_column(String(100), nullable=False)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defective: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rework: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def init_receiving_storage():
    Base.metadata.create_all(
        get_engine(),
        tables=[
            IncomingGood.__table__,
            SpecialReceivingReport.__table__,
            SpecialReceivingReportItem.__table__,
        ],
    )
    ensure_receiving_columns()


def ensure_receiving_columns():
    statements = [
        "alter table incoming_goods add column if not exists exported boolean not null default false",
        "alter table incoming_goods add column if not exists exported_at timestamp",
        "alter table incoming_goods add column if not exists exported_by varchar(255)",
        "alter table incoming_goods add column if not exists export_id varchar(100)",
        "alter table incoming_goods add column if not exists export_chat_id varchar(100)",
        "alter table incoming_goods add column if not exists export_thread_id varchar(100)",
        "alter table incoming_goods add column if not exists export_message_ids varchar(1000)",
        "create index if not exists ix_incoming_goods_exported on incoming_goods (exported)",
        "create index if not exists ix_incoming_goods_export_id on incoming_goods (export_id)",
    ]

    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

        special_item_statements = [
            "alter table special_receiving_report_items add column if not exists packed integer not null default 0",
            "alter table special_receiving_report_items add column if not exists defective integer not null default 0",
            "alter table special_receiving_report_items add column if not exists rework integer not null default 0",
            "update special_receiving_report_items set packed = quantity where packed = 0 and defective = 0 and rework = 0 and quantity > 0",
        ]
        for statement in special_item_statements:
            connection.execute(text(statement))


def get_table_columns(table_name):
    inspector = inspect(get_engine())
    return {column["name"] for column in inspector.get_columns(table_name)}


def get_receiving_db_status():
    version = check_connection()
    columns = sorted(get_table_columns(IncomingGood.__tablename__))
    return version, columns


def get_last_records_text(limit=10):
    with session_scope() as session:
        records = (
            session.execute(
                select(IncomingGood)
                .order_by(desc(IncomingGood.id))
                .limit(limit)
            )
            .scalars()
            .all()
        )

    if not records:
        return "Пока нет записей в PostgreSQL."

    lines = [f"📋 Последние {min(limit, len(records))} записей из PostgreSQL:"]

    for record in records:
        lines.append(
            f"\n{record.record_date.strftime('%d.%m.%Y')}\n"
            f"Пользователь: {record.username or '-'}\n"
            f"Группа: {record.category_name}\n"
            f"Товар: {compact_product_name(record.product_name, record.size)}\n"
            f"Упаковано: {record.packed}\n"
            f"Брак: {record.defective}\n"
            f"Доработка: {record.rework}"
        )

    return "\n".join(lines)


def split_message_ids(value):
    if not value:
        return []

    result = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            continue

    return result


def parse_report_date(value):
    return datetime.strptime(str(value).strip(), "%d.%m.%Y").date()


def record_to_dict(record):
    return {
        "row_number": record.id,
        "date": record.record_date.strftime("%d.%m.%Y"),
        "user_id": str(record.user_id or ""),
        "username": record.username or "",
        "category_name": record.category_name,
        "product_name": record.product_name,
        "size": record.size,
        "packed": int(record.packed or 0),
        "defective": int(record.defective or 0),
        "rework": int(record.rework or 0),
        "exported": bool(record.exported),
        "exported_at": record.exported_at.strftime("%d.%m.%Y %H:%M:%S") if record.exported_at else "",
        "exported_by": record.exported_by or "",
        "export_id": record.export_id or "",
        "export_chat_id": record.export_chat_id or "",
        "export_thread_id": record.export_thread_id or "",
        "export_message_ids": record.export_message_ids or "",
    }


def get_unexported_receiving_records(limit=15):
    with session_scope() as session:
        records = (
            session.execute(
                select(IncomingGood)
                .where(IncomingGood.exported.is_(False))
                .order_by(desc(IncomingGood.id))
                .limit(limit)
            )
            .scalars()
            .all()
        )

    return [record_to_dict(record) for record in records]


def get_receiving_record_by_row(row_number):
    with session_scope() as session:
        record = session.get(IncomingGood, int(row_number))
        return record_to_dict(record) if record else None


def delete_unexported_receiving_record(row_number):
    with session_scope() as session:
        record = session.get(IncomingGood, int(row_number))

        if not record:
            raise RuntimeError("Запись не найдена.")

        if record.exported:
            raise RuntimeError("Эта запись уже выгружена в отчет, ее нельзя удалить.")

        result = record_to_dict(record)
        session.delete(record)

    return result


def has_unexported_receiving_records_for_date(report_date):
    parsed_date = parse_report_date(report_date)

    with session_scope() as session:
        record = session.execute(
            select(IncomingGood.id)
            .where(
                IncomingGood.record_date == parsed_date,
                IncomingGood.exported.is_(False),
            )
            .limit(1)
        ).first()

    return record is not None


def unexported_receiving_ids_for_date(report_date):
    parsed_date = parse_report_date(report_date)
    with session_scope() as session:
        return list(session.scalars(
            select(IncomingGood.id).where(
                IncomingGood.record_date == parsed_date,
                IncomingGood.exported.is_(False),
            ).order_by(IncomingGood.id)
        ).all())


def mark_receiving_rows_exported(
    report_date,
    exported_by,
    export_id,
    chat_id,
    thread_id,
    message_ids,
    record_ids=None,
):
    parsed_date = parse_report_date(report_date)
    now_value = datetime.now()
    message_ids_text = ",".join(str(message_id) for message_id in message_ids)

    with session_scope() as session:
        statement = select(IncomingGood).where(
            IncomingGood.record_date == parsed_date,
            IncomingGood.exported.is_(False),
        )
        if record_ids is not None:
            statement = statement.where(IncomingGood.id.in_(record_ids))
        records = (
            session.execute(statement)
            .scalars()
            .all()
        )

        for record in records:
            record.exported = True
            record.exported_at = now_value
            record.exported_by = exported_by
            record.export_id = export_id
            record.export_chat_id = str(chat_id)
            record.export_thread_id = str(thread_id)
            record.export_message_ids = message_ids_text

        return len(records)


def build_receiving_report_text(report_date, exported_by=None, only_unexported=True,
                                group_by_type=False, record_ids=None):
    parsed_date = parse_report_date(report_date)
    header_lines = [f"Дата: {report_date}"]

    if exported_by:
        header_lines.append(f"Выгрузил: {exported_by}")

    with session_scope() as session:
        statement = select(IncomingGood).where(IncomingGood.record_date == parsed_date)

        if only_unexported:
            statement = statement.where(IncomingGood.exported.is_(False))
        if record_ids is not None:
            statement = statement.where(IncomingGood.id.in_(record_ids))

        records = session.execute(statement).scalars().all()

    grouped = defaultdict(lambda: {
        "packed": 0,
        "defective": 0,
        "rework": 0,
    })

    total_packed = 0
    total_defective = 0
    total_rework = 0

    for record in records:
        name_with_size = compact_product_name(record.product_name, record.size)
        product_key = (record.category_name if group_by_type else "", name_with_size)
        grouped[product_key]["packed"] += record.packed
        grouped[product_key]["defective"] += record.defective
        grouped[product_key]["rework"] += record.rework

        total_packed += record.packed
        total_defective += record.defective
        total_rework += record.rework

    if not grouped:
        if only_unexported:
            return "\n".join(header_lines) + "\n\nНет невыгруженных записей за эту дату."

        return "\n".join(header_lines) + "\n\nНет записей за эту дату."

    lines = header_lines + [""]

    previous_category = None
    for category_name, product_name in sorted(grouped.keys()):
        if category_name and category_name != previous_category:
            lines.append(f"📦 {category_name}")
            previous_category = category_name
        lines.append(product_name)
        totals = grouped[(category_name, product_name)]
        packed = totals["packed"]
        defective = totals["defective"]
        rework = totals["rework"]
        total = packed + defective + rework
        lines.append(
            f"упаковано - {packed}, брак - {defective}, "
            f"доработка - {rework}, общее - {total}"
        )
        lines.append("")

    grand_total = total_packed + total_defective + total_rework

    lines.extend(
        [
            f"Общее упаковано: {total_packed}",
            f"Общее брак: {total_defective}",
            f"Общее доработка: {total_rework}",
            f"Общее: {grand_total}",
        ]
    )

    return "\n".join(lines)


def make_export_group(export_id, records):
    first = records[0]

    total_packed = sum(record.packed for record in records)
    total_defective = sum(record.defective for record in records)
    total_rework = sum(record.rework for record in records)

    return {
        "export_id": export_id,
        "date": first.record_date.strftime("%d.%m.%Y"),
        "exported_at": first.exported_at.strftime("%d.%m.%Y %H:%M:%S") if first.exported_at else "",
        "exported_by": first.exported_by or "",
        "chat_id": first.export_chat_id or "",
        "thread_id": first.export_thread_id or "",
        "message_ids": split_message_ids(first.export_message_ids),
        "row_count": len(records),
        "row_numbers": [record.id for record in records],
        "total_packed": total_packed,
        "total_defective": total_defective,
        "total_rework": total_rework,
        "total": total_packed + total_defective + total_rework,
        "last_row_number": max(record.id for record in records),
    }


def get_exported_receiving_report_groups(limit=10):
    with session_scope() as session:
        records = (
            session.execute(
                select(IncomingGood)
                .where(
                    IncomingGood.exported.is_(True),
                    IncomingGood.export_id.is_not(None),
                    IncomingGood.export_message_ids.is_not(None),
                )
                .order_by(desc(IncomingGood.id))
            )
            .scalars()
            .all()
        )

    groups = defaultdict(list)

    for record in records:
        if record.export_id and record.export_message_ids:
            groups[record.export_id].append(record)

    result = [make_export_group(export_id, rows) for export_id, rows in groups.items()]
    result.sort(key=lambda item: item["last_row_number"], reverse=True)

    return result[:limit]


def get_exported_receiving_report_by_export_id(export_id):
    groups = get_exported_receiving_report_groups(limit=1000)

    for group in groups:
        if group["export_id"] == export_id:
            return group

    return None


def unmark_receiving_rows_by_export_id(export_id):
    with session_scope() as session:
        records = (
            session.execute(
                select(IncomingGood).where(IncomingGood.export_id == str(export_id).strip())
            )
            .scalars()
            .all()
        )

        for record in records:
            record.exported = False
            record.exported_at = None
            record.exported_by = None
            record.export_id = None
            record.export_chat_id = None
            record.export_thread_id = None
            record.export_message_ids = None

        return len(records)


def save_incoming_good(
    user_id,
    username,
    category_id,
    product_id,
    size,
    packed,
    defective,
    rework,
    record_date,
):
    category_name = CATEGORIES[category_id]["name"]
    product_name = CATEGORIES[category_id]["products"][product_id]
    parsed_record_date = datetime.strptime(record_date, "%d.%m.%Y").date()

    with session_scope() as session:
        record = IncomingGood(
            record_date=parsed_record_date,
            user_id=user_id,
            username=username,
            category_id=category_id,
            category_name=category_name,
            product_id=product_id,
            product_name=product_name,
            size=size,
            packed=packed,
            defective=defective,
            rework=rework,
        )
        session.add(record)
        session.flush()
        return record.id


def save_moysklad_incoming_good(*, user_id, username, report_type, product_id,
                                product_name, size, packed, defective, rework,
                                record_date):
    labels = {
        "new_supply": "Новая поставка",
        "illiquid": "Неликвид",
        "rejected": "Отбракованный товар",
    }
    with session_scope() as session:
        record = IncomingGood(
            record_date=datetime.strptime(record_date, "%d.%m.%Y").date(),
            user_id=user_id,
            username=username,
            category_id=report_type,
            category_name=labels[report_type],
            product_id=str(product_id)[:100],
            product_name=str(product_name)[:255],
            size=str(size or "—")[:50],
            packed=packed,
            defective=defective,
            rework=rework,
        )
        session.add(record)
        session.flush()
        return record.id


def report_type_title(report_type):
    titles = {
        "illiquid": "Неликвид",
        "rejected": "Отбракованный товар",
    }
    return titles.get(report_type, report_type)


def special_report_to_dict(report, items=None):
    return {
        "id": report.id,
        "created_at": report.created_at.strftime("%d.%m.%Y %H:%M:%S") if report.created_at else "",
        "updated_at": report.updated_at.strftime("%d.%m.%Y %H:%M:%S") if report.updated_at else "",
        "report_type": report.report_type,
        "report_title": report_type_title(report.report_type),
        "store_from": report.store_from or "",
        "removal_date": report.removal_date.strftime("%d.%m.%Y") if report.removal_date else "",
        "created_by": report.created_by or "",
        "chat_id": report.chat_id or "",
        "thread_id": report.thread_id or "",
        "message_ids": split_message_ids(report.message_ids),
        "items": items or [],
    }


def special_item_to_dict(item, report_type=None):
    result = {
        "id": item.id,
        "category_id": item.category_id,
        "category_name": item.category_name,
        "product_id": item.product_id,
        "product_name": item.product_name,
        "size": item.size,
        "quantity": int(item.quantity or 0),
    }

    if report_type == "illiquid":
        result["packed"] = int(item.packed or 0)
        result["defective"] = int(item.defective or 0)
        result["rework"] = int(item.rework or 0)

    return result


def special_item_counts(item):
    packed = int(item.get("packed", item.get("quantity", 0)) or 0)
    defective = int(item.get("defective", 0) or 0)
    rework = int(item.get("rework", 0) or 0)
    quantity = int(item.get("quantity", packed + defective + rework) or 0)

    if quantity <= 0:
        quantity = packed + defective + rework

    return packed, defective, rework, quantity


def create_special_receiving_report(report_type, store_from, removal_date, created_by, items, telegram_data):
    parsed_removal_date = parse_report_date(removal_date) if removal_date else None
    now_value = datetime.now()

    with session_scope() as session:
        report = SpecialReceivingReport(
            created_at=now_value,
            updated_at=now_value,
            report_type=report_type,
            store_from=store_from or None,
            removal_date=parsed_removal_date,
            created_by=created_by,
            chat_id=str(telegram_data.get("chat_id", "")),
            thread_id=str(telegram_data.get("thread_id", "")),
            message_ids=",".join(str(message_id) for message_id in telegram_data.get("message_ids", [])),
        )
        session.add(report)
        session.flush()

        for item in items:
            category_id = item["category_id"]
            product_id = item["product_id"]
            packed, defective, rework, quantity = special_item_counts(item)
            session.add(
                SpecialReceivingReportItem(
                    report_id=report.id,
                    category_id=category_id,
                    category_name=CATEGORIES[category_id]["name"],
                    product_id=product_id,
                    product_name=CATEGORIES[category_id]["products"][product_id],
                    size=item["size"],
                    quantity=quantity,
                    packed=packed,
                    defective=defective,
                    rework=rework,
                )
            )

        return report.id


def get_special_receiving_reports(report_type=None, limit=10):
    with session_scope() as session:
        statement = select(SpecialReceivingReport).order_by(desc(SpecialReceivingReport.id))

        if report_type:
            statement = statement.where(SpecialReceivingReport.report_type == report_type)

        reports = session.execute(statement.limit(limit)).scalars().all()

        result = []
        for report in reports:
            items = (
                session.execute(
                    select(SpecialReceivingReportItem)
                    .where(SpecialReceivingReportItem.report_id == report.id)
                    .order_by(SpecialReceivingReportItem.id)
                )
                .scalars()
                .all()
            )
            result.append(
                special_report_to_dict(
                    report,
                    [special_item_to_dict(item, report.report_type) for item in items],
                )
            )

    return result


def get_special_receiving_report(report_id):
    with session_scope() as session:
        report = session.get(SpecialReceivingReport, int(report_id))

        if not report:
            return None

        items = (
            session.execute(
                select(SpecialReceivingReportItem)
                .where(SpecialReceivingReportItem.report_id == report.id)
                .order_by(SpecialReceivingReportItem.id)
            )
            .scalars()
            .all()
        )

        return special_report_to_dict(
            report,
            [special_item_to_dict(item, report.report_type) for item in items],
        )


def update_special_receiving_report(report_id, store_from=None, removal_date=None, items=None, telegram_data=None):
    parsed_removal_date = parse_report_date(removal_date) if removal_date else None

    with session_scope() as session:
        report = session.get(SpecialReceivingReport, int(report_id))

        if not report:
            raise RuntimeError("Отчет не найден.")

        report.store_from = store_from or None
        report.removal_date = parsed_removal_date
        report.updated_at = datetime.now()

        if telegram_data is not None:
            report.chat_id = str(telegram_data.get("chat_id", ""))
            report.thread_id = str(telegram_data.get("thread_id", ""))
            report.message_ids = ",".join(
                str(message_id) for message_id in telegram_data.get("message_ids", [])
            )

        if items is not None:
            old_items = (
                session.execute(
                    select(SpecialReceivingReportItem).where(
                        SpecialReceivingReportItem.report_id == report.id
                    )
                )
                .scalars()
                .all()
            )
            for item in old_items:
                session.delete(item)

            for item in items:
                category_id = item["category_id"]
                product_id = item["product_id"]
                packed, defective, rework, quantity = special_item_counts(item)
                session.add(
                    SpecialReceivingReportItem(
                        report_id=report.id,
                        category_id=category_id,
                        category_name=CATEGORIES[category_id]["name"],
                        product_id=product_id,
                        product_name=CATEGORIES[category_id]["products"][product_id],
                        size=item["size"],
                        quantity=quantity,
                        packed=packed,
                        defective=defective,
                        rework=rework,
                    )
                )


def delete_special_receiving_report(report_id):
    with session_scope() as session:
        report = session.get(SpecialReceivingReport, int(report_id))

        if not report:
            raise RuntimeError("Отчет не найден.")

        items = (
            session.execute(
                select(SpecialReceivingReportItem)
                .where(SpecialReceivingReportItem.report_id == report.id)
            )
            .scalars()
            .all()
        )

        result = special_report_to_dict(
            report,
            [special_item_to_dict(item, report.report_type) for item in items],
        )

        for item in items:
            session.delete(item)

        session.delete(report)

        return result
