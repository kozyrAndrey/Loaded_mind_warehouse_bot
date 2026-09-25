import json
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, desc, select, text
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class ReturnRecord(Base):
    __tablename__ = "return_records"
    __table_args__ = (
        Index("ix_return_records_created_at", "created_at"),
        Index("ix_return_records_return_type", "return_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    return_type: Mapped[str] = mapped_column(String(50), nullable=False)
    employee_name: Mapped[str | None] = mapped_column(String(255))
    employee_user_id: Mapped[str | None] = mapped_column(String(100))
    counterparty: Mapped[str | None] = mapped_column(String(255))
    track_number: Mapped[str | None] = mapped_column(String(100))
    label_status: Mapped[str | None] = mapped_column(String(255))
    items_json: Mapped[str | None] = mapped_column(Text)
    photo_ids_json: Mapped[str | None] = mapped_column(Text)
    chat_id: Mapped[str | None] = mapped_column(String(100))
    thread_id: Mapped[str | None] = mapped_column(String(100))
    message_ids: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")


def init_returns_storage():
    Base.metadata.create_all(get_engine(), tables=[ReturnRecord.__table__])
    ensure_returns_columns()


def ensure_returns_columns():
    statements = [
        "alter table return_records add column if not exists status varchar(50) not null default 'active'",
        "create index if not exists ix_return_records_created_at on return_records (created_at)",
        "create index if not exists ix_return_records_return_type on return_records (return_type)",
    ]
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def dumps(value):
    return json.dumps(value or [], ensure_ascii=False)


def loads(value):
    if not value:
        return []
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


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


def record_to_dict(record):
    return {
        "id": record.id,
        "created_at": record.created_at.strftime("%d.%m.%Y %H:%M:%S") if record.created_at else "",
        "updated_at": record.updated_at.strftime("%d.%m.%Y %H:%M:%S") if record.updated_at else "",
        "return_type": record.return_type,
        "employee_name": record.employee_name or "",
        "employee_user_id": record.employee_user_id or "",
        "counterparty": record.counterparty or "",
        "track_number": record.track_number or "",
        "label_status": record.label_status or "",
        "items": loads(record.items_json),
        "photo_ids": loads(record.photo_ids_json),
        "chat_id": record.chat_id or "",
        "thread_id": record.thread_id or "",
        "message_ids": split_message_ids(record.message_ids),
        "status": record.status or "active",
    }


def create_return_record(data):
    now_value = datetime.now()
    message_ids = ",".join(str(message_id) for message_id in data.get("message_ids", []))
    with session_scope() as session:
        record = ReturnRecord(
            created_at=now_value,
            updated_at=now_value,
            return_type=data["return_type"],
            employee_name=data.get("employee_name", ""),
            employee_user_id=str(data.get("employee_user_id", "")),
            counterparty=data.get("counterparty", ""),
            track_number=data.get("track_number", ""),
            label_status=data.get("label_status", ""),
            items_json=dumps(data.get("items", [])),
            photo_ids_json=dumps(data.get("photo_ids", [])),
            chat_id=str(data.get("chat_id", "")),
            thread_id=str(data.get("thread_id", "")),
            message_ids=message_ids,
            status="active",
        )
        session.add(record)
        session.flush()
        return record.id


def get_recent_return_records(limit=10, include_deleted=False):
    with session_scope() as session:
        statement = select(ReturnRecord).order_by(desc(ReturnRecord.id))
        if not include_deleted:
            statement = statement.where(ReturnRecord.status != "deleted")
        statement = statement.limit(limit)
        records = session.execute(statement).scalars().all()
    return [record_to_dict(record) for record in records]


def get_return_record(record_id):
    with session_scope() as session:
        record = session.get(ReturnRecord, int(record_id))
        return record_to_dict(record) if record else None


def update_return_record(record_id, **fields):
    allowed = {
        "return_type",
        "counterparty",
        "track_number",
        "label_status",
        "items",
        "photo_ids",
        "chat_id",
        "thread_id",
        "message_ids",
        "employee_name",
    }
    with session_scope() as session:
        record = session.get(ReturnRecord, int(record_id))
        if not record or record.status == "deleted":
            raise RuntimeError("Запись возврата не найдена.")

        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "items":
                record.items_json = dumps(value)
            elif key == "photo_ids":
                record.photo_ids_json = dumps(value)
            elif key == "message_ids":
                record.message_ids = ",".join(str(message_id) for message_id in value or [])
            else:
                setattr(record, key, str(value or "").strip())
        record.updated_at = datetime.now()
        return record_to_dict(record)


def mark_return_record_deleted(record_id):
    with session_scope() as session:
        record = session.get(ReturnRecord, int(record_id))
        if not record or record.status == "deleted":
            raise RuntimeError("Запись возврата не найдена.")
        record.status = "deleted"
        record.updated_at = datetime.now()
        return record_to_dict(record)
