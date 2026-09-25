from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, desc, select, text
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        Index("ix_job_applications_created_at", "created_at"),
        Index("ix_job_applications_user_id", "telegram_user_id"),
        Index("ix_job_applications_position", "position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    telegram_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    telegram_full_name: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    settlement: Mapped[str] = mapped_column(String(255), nullable=False)
    experience: Mapped[str] = mapped_column(Text, nullable=False)
    shifts_per_week: Mapped[int] = mapped_column(Integer, nullable=False)
    hours_per_shift: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="new")


def init_recruitment_storage():
    Base.metadata.create_all(get_engine(), tables=[JobApplication.__table__])
    ensure_recruitment_columns()


def ensure_recruitment_columns():
    statements = [
        "alter table job_applications add column if not exists telegram_username varchar(255)",
        "alter table job_applications add column if not exists telegram_full_name varchar(255)",
        "alter table job_applications add column if not exists status varchar(50) not null default 'new'",
        "create index if not exists ix_job_applications_created_at on job_applications (created_at)",
        "create index if not exists ix_job_applications_user_id on job_applications (telegram_user_id)",
        "create index if not exists ix_job_applications_position on job_applications (position)",
    ]

    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def application_to_dict(application):
    return {
        "id": application.id,
        "created_at": application.created_at,
        "telegram_user_id": application.telegram_user_id,
        "telegram_username": application.telegram_username or "",
        "telegram_full_name": application.telegram_full_name or "",
        "position": application.position,
        "full_name": application.full_name,
        "age": application.age,
        "settlement": application.settlement,
        "experience": application.experience,
        "shifts_per_week": application.shifts_per_week,
        "hours_per_shift": application.hours_per_shift,
        "status": application.status,
    }


def create_job_application(
    user,
    position,
    full_name,
    age,
    settlement,
    experience,
    shifts_per_week,
    hours_per_shift,
):
    with session_scope() as session:
        application = JobApplication(
            telegram_user_id=str(user.id),
            telegram_username=user.username or "",
            telegram_full_name=user.full_name or "",
            position=position,
            full_name=full_name,
            age=age,
            settlement=settlement,
            experience=experience,
            shifts_per_week=shifts_per_week,
            hours_per_shift=hours_per_shift,
        )
        session.add(application)
        session.flush()
        return application_to_dict(application)


def get_recent_applications(limit=20):
    with session_scope() as session:
        statement = select(JobApplication).order_by(desc(JobApplication.id)).limit(limit)
        applications = session.execute(statement).scalars().all()

    return [application_to_dict(application) for application in applications]


def get_all_applications():
    with session_scope() as session:
        applications = (
            session.execute(select(JobApplication).order_by(desc(JobApplication.id)))
            .scalars()
            .all()
        )

    return [application_to_dict(application) for application in applications]
