import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Numeric, String, select
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


VACATION_DAILY_HOURS = 8


class VacationValidationError(ValueError):
    pass


class VacationRecord(Base):
    __tablename__ = "payroll_vacations"
    __table_args__ = (
        Index("ix_payroll_vacations_employee_dates", "employee_id", "start_date", "end_date"),
        Index("ix_payroll_vacations_start_date", "start_date"),
    )

    vacation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    hourly_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


def init_vacation_storage():
    Base.metadata.create_all(get_engine(), tables=[VacationRecord.__table__])
    with get_engine().begin() as connection:
        connection.exec_driver_sql(
            "create index if not exists ix_payroll_vacations_employee_dates "
            "on payroll_vacations (employee_id, start_date, end_date)"
        )
        connection.exec_driver_sql(
            "create index if not exists ix_payroll_vacations_start_date "
            "on payroll_vacations (start_date)"
        )


def normalize_vacation_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value or "").strip(), "%d.%m.%Y").date()
    except ValueError as error:
        raise VacationValidationError("Дата должна быть в формате ДД.ММ.ГГГГ.") from error


def vacation_days(start_date, end_date):
    return (end_date - start_date).days + 1


def vacation_to_dict(record):
    days = vacation_days(record.start_date, record.end_date)
    hourly_rate = float(record.hourly_rate)
    return {
        "vacation_id": record.vacation_id,
        "employee_id": record.employee_id,
        "employee_name": record.employee_name,
        "start_date": record.start_date.strftime("%d.%m.%Y"),
        "end_date": record.end_date.strftime("%d.%m.%Y"),
        "hourly_rate": hourly_rate,
        "days": days,
        "hours": days * VACATION_DAILY_HOURS,
        "amount": days * VACATION_DAILY_HOURS * hourly_rate,
        "created_by": record.created_by or "",
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _validate_range(start_date, end_date):
    if end_date < start_date:
        raise VacationValidationError("Дата окончания отпуска не может быть раньше даты начала.")


def _find_overlap(session, employee_id, start_date, end_date, excluded_id=None):
    statement = select(VacationRecord).where(
        VacationRecord.employee_id == str(employee_id),
        VacationRecord.start_date <= end_date,
        VacationRecord.end_date >= start_date,
    )
    if excluded_id:
        statement = statement.where(VacationRecord.vacation_id != str(excluded_id))
    return session.execute(statement).scalars().first()


def create_vacation(employee, start_date, end_date, created_by=""):
    start_value = normalize_vacation_date(start_date)
    end_value = normalize_vacation_date(end_date)
    _validate_range(start_value, end_value)

    employee_id = str((employee or {}).get("employee_id") or "").strip()
    employee_name = str((employee or {}).get("full_name") or "").strip()
    hourly_rate = float((employee or {}).get("hourly_rate") or 0)
    if not employee_id or not employee_name:
        raise VacationValidationError("Сотрудник не найден.")
    if hourly_rate < 0:
        raise VacationValidationError("У сотрудника указана некорректная часовая ставка.")

    with session_scope() as session:
        if _find_overlap(session, employee_id, start_value, end_value):
            raise VacationValidationError("Этот период пересекается с уже созданным отпуском сотрудника.")
        record = VacationRecord(
            vacation_id=f"vac_{uuid.uuid4().hex[:12]}",
            employee_id=employee_id,
            employee_name=employee_name,
            start_date=start_value,
            end_date=end_value,
            hourly_rate=hourly_rate,
            created_by=str(created_by or "").strip(),
        )
        session.add(record)
        session.flush()
        return vacation_to_dict(record)


def update_vacation_period(vacation_id, start_date, end_date):
    start_value = normalize_vacation_date(start_date)
    end_value = normalize_vacation_date(end_date)
    _validate_range(start_value, end_value)

    with session_scope() as session:
        record = session.get(VacationRecord, str(vacation_id))
        if not record:
            return None
        if _find_overlap(
            session,
            record.employee_id,
            start_value,
            end_value,
            excluded_id=record.vacation_id,
        ):
            raise VacationValidationError("Этот период пересекается с уже созданным отпуском сотрудника.")
        record.start_date = start_value
        record.end_date = end_value
        record.updated_at = datetime.now()
        session.flush()
        return vacation_to_dict(record)


def delete_vacation(vacation_id):
    with session_scope() as session:
        record = session.get(VacationRecord, str(vacation_id))
        if not record:
            return None
        result = vacation_to_dict(record)
        session.delete(record)
        return result


def get_vacation(vacation_id):
    with session_scope() as session:
        record = session.get(VacationRecord, str(vacation_id))
        return vacation_to_dict(record) if record else None


def list_vacations(employee_id=None, start_date=None, end_date=None):
    statement = select(VacationRecord)
    if employee_id:
        statement = statement.where(VacationRecord.employee_id == str(employee_id))
    if start_date is not None and end_date is not None:
        start_value = normalize_vacation_date(start_date)
        end_value = normalize_vacation_date(end_date)
        statement = statement.where(
            VacationRecord.start_date <= end_value,
            VacationRecord.end_date >= start_value,
        )

    with session_scope() as session:
        records = session.execute(
            statement.order_by(VacationRecord.start_date, VacationRecord.employee_name)
        ).scalars().all()
    return [vacation_to_dict(record) for record in records]


def get_vacations_in_period(start_date, end_date):
    return list_vacations(start_date=start_date, end_date=end_date)


def vacation_amount_for_period(vacation, start_date, end_date):
    period_start = normalize_vacation_date(start_date)
    period_end = normalize_vacation_date(end_date)
    vacation_start = normalize_vacation_date(vacation["start_date"])
    vacation_end = normalize_vacation_date(vacation["end_date"])
    overlap_start = max(period_start, vacation_start)
    overlap_end = min(period_end, vacation_end)
    if overlap_end < overlap_start:
        return 0, 0.0
    days = vacation_days(overlap_start, overlap_end)
    return days, days * VACATION_DAILY_HOURS * float(vacation["hourly_rate"])
