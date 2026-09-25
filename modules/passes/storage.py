from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class PassTemplate(Base):
    __tablename__ = "visitor_pass_templates"
    __table_args__ = (
        Index("ix_visitor_pass_templates_name", "name"),
        Index("ix_visitor_pass_templates_updated_at", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    surname: Mapped[str] = mapped_column(String(120), nullable=False)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    patronymic: Mapped[str] = mapped_column(String(120), nullable=False, default="-")
    has_vehicle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vehicle_make: Mapped[str | None] = mapped_column(String(120))
    vehicle_type: Mapped[str | None] = mapped_column(String(30))
    capacity_code: Mapped[str | None] = mapped_column(String(30))
    license_plate: Mapped[str | None] = mapped_column(String(30))
    created_by_user_id: Mapped[str | None] = mapped_column(String(100))
    created_by_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


def init_passes_storage():
    Base.metadata.create_all(get_engine(), tables=[PassTemplate.__table__])
    return True


def template_to_dict(template):
    if template is None:
        return None
    return {
        "id": template.id,
        "name": template.name,
        "surname": template.surname,
        "first_name": template.first_name,
        "patronymic": template.patronymic or "-",
        "has_vehicle": bool(template.has_vehicle),
        "vehicle_make": template.vehicle_make or "",
        "vehicle_type": template.vehicle_type or "",
        "capacity_code": template.capacity_code or "",
        "license_plate": template.license_plate or "",
        "created_by_user_id": template.created_by_user_id or "",
        "created_by_name": template.created_by_name or "",
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def create_pass_template(data, user):
    with session_scope() as session:
        template = PassTemplate(
            name=data["name"],
            surname=data["surname"],
            first_name=data["first_name"],
            patronymic=data.get("patronymic") or "-",
            has_vehicle=bool(data.get("has_vehicle")),
            vehicle_make=data.get("vehicle_make") or None,
            vehicle_type=data.get("vehicle_type") or None,
            capacity_code=data.get("capacity_code") or None,
            license_plate=data.get("license_plate") or None,
            created_by_user_id=str(user.id),
            created_by_name=user.full_name or "",
        )
        session.add(template)
        session.flush()
        return template_to_dict(template)


def get_pass_template(template_id):
    with session_scope() as session:
        return template_to_dict(session.get(PassTemplate, int(template_id)))


def list_pass_templates(limit=10, offset=0):
    with session_scope() as session:
        templates = session.execute(
            select(PassTemplate)
            .order_by(PassTemplate.name, PassTemplate.id)
            .limit(limit)
            .offset(offset)
        ).scalars().all()
    return [template_to_dict(template) for template in templates]


def count_pass_templates():
    with session_scope() as session:
        return int(session.execute(select(func.count(PassTemplate.id))).scalar_one())


def update_pass_template(template_id, **fields):
    allowed = {
        "name",
        "surname",
        "first_name",
        "patronymic",
        "has_vehicle",
        "vehicle_make",
        "vehicle_type",
        "capacity_code",
        "license_plate",
    }
    with session_scope() as session:
        template = session.get(PassTemplate, int(template_id))
        if template is None:
            return None
        for key, value in fields.items():
            if key in allowed:
                setattr(template, key, value)
        template.updated_at = datetime.now()
        session.flush()
        return template_to_dict(template)


def delete_pass_template(template_id):
    with session_scope() as session:
        template = session.get(PassTemplate, int(template_id))
        if template is None:
            return False
        session.delete(template)
        return True
