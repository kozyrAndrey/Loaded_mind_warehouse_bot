from datetime import datetime

import re

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from modules.storage.postgres import Base, get_engine, session_scope


class GoogleSheetArchiveRow(Base):
    __tablename__ = "google_sheet_archive_rows"
    __table_args__ = (
        UniqueConstraint("source", "sheet_name", "row_number", name="uq_google_archive_source_sheet_row"),
        Index("ix_google_archive_source_sheet", "source", "sheet_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    spreadsheet_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


def init_google_archive_storage():
    Base.metadata.create_all(get_engine(), tables=[GoogleSheetArchiveRow.__table__])


def replace_sheet_archive(source, spreadsheet_id, sheet_name, rows):
    init_google_archive_storage()

    with session_scope() as session:
        session.execute(
            delete(GoogleSheetArchiveRow).where(
                GoogleSheetArchiveRow.source == source,
                GoogleSheetArchiveRow.sheet_name == sheet_name,
            )
        )

        for row_number, data in rows:
            session.add(
                GoogleSheetArchiveRow(
                    source=source,
                    spreadsheet_id=spreadsheet_id,
                    sheet_name=sheet_name,
                    row_number=row_number,
                    data=data,
                )
            )

    sync_structured_sheet_if_configured(source, sheet_name, rows)


def sync_structured_sheet_if_configured(source, sheet_name, rows):
    from modules.storage.structured_sheets import sync_structured_sheet

    sync_structured_sheet(source, sheet_name, rows)


def count_archived_rows(source=None):
    with session_scope() as session:
        statement = select(GoogleSheetArchiveRow.source, GoogleSheetArchiveRow.sheet_name)
        if source:
            statement = statement.where(GoogleSheetArchiveRow.source == source)

        rows = session.execute(statement).all()

    result = {}
    for row in rows:
        key = f"{row.source}:{row.sheet_name}"
        result[key] = result.get(key, 0) + 1

    return result


def column_to_index(column):
    result = 0
    for char in column.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def parse_a1_range(value):
    match = re.match(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", value)
    if not match:
        raise ValueError(f"Неподдерживаемый диапазон: {value}")

    start_col, start_row, end_col, end_row = match.groups()
    return {
        "start_col": column_to_index(start_col),
        "start_row": int(start_row),
        "end_col": column_to_index(end_col or start_col),
        "end_row": int(end_row or start_row),
    }


class DatabaseWorksheet:
    def __init__(self, source, spreadsheet_id, title, headers):
        self.source = source
        self.spreadsheet_id = spreadsheet_id or source
        self.title = title
        self.headers = list(headers)
        init_google_archive_storage()

    def _rows(self):
        with session_scope() as session:
            rows = (
                session.execute(
                    select(GoogleSheetArchiveRow)
                    .where(
                        GoogleSheetArchiveRow.source == self.source,
                        GoogleSheetArchiveRow.sheet_name == self.title,
                    )
                    .order_by(GoogleSheetArchiveRow.row_number)
                )
                .scalars()
                .all()
            )
            return [(row.row_number, dict(row.data or {})) for row in rows]

    def _sync_structured(self):
        sync_structured_sheet_if_configured(self.source, self.title, self._rows())

    def _row_values_from_data(self, data):
        return [data.get(header, "") for header in self.headers]

    def row_values(self, row_number):
        if int(row_number) == 1:
            return self.headers

        for current_row_number, data in self._rows():
            if current_row_number == int(row_number):
                return self._row_values_from_data(data)

        return []

    def get_all_values(self):
        values = [self.headers]
        for _, data in self._rows():
            values.append(self._row_values_from_data(data))
        return values

    def get_all_records(self, numericise_ignore=None):
        return [dict(data) for _, data in self._rows()]

    def _next_row_number(self, session):
        max_row_number = session.execute(
            select(func.max(GoogleSheetArchiveRow.row_number)).where(
                GoogleSheetArchiveRow.source == self.source,
                GoogleSheetArchiveRow.sheet_name == self.title,
            )
        ).scalar_one()
        return max(2, int(max_row_number or 1) + 1)

    def append_row(self, row):
        with session_scope() as session:
            session.add(
                GoogleSheetArchiveRow(
                    source=self.source,
                    spreadsheet_id=self.spreadsheet_id,
                    sheet_name=self.title,
                    row_number=self._next_row_number(session),
                    data=self._data_from_row(row),
                )
            )
        self._sync_structured()

    def append_rows(self, rows):
        with session_scope() as session:
            next_row_number = self._next_row_number(session)
            for offset, row in enumerate(rows):
                session.add(
                    GoogleSheetArchiveRow(
                        source=self.source,
                        spreadsheet_id=self.spreadsheet_id,
                        sheet_name=self.title,
                        row_number=next_row_number + offset,
                        data=self._data_from_row(row),
                    )
                )
        self._sync_structured()

    def _data_from_row(self, row):
        row = list(row)
        return {
            header: row[index] if index < len(row) else ""
            for index, header in enumerate(self.headers)
        }

    def update(self, a1_range, values):
        parsed = parse_a1_range(a1_range)

        if parsed["start_row"] == 1:
            return

        with session_scope() as session:
            for row_offset, values_row in enumerate(values):
                row_number = parsed["start_row"] + row_offset
                existing = session.execute(
                    select(GoogleSheetArchiveRow).where(
                        GoogleSheetArchiveRow.source == self.source,
                        GoogleSheetArchiveRow.sheet_name == self.title,
                        GoogleSheetArchiveRow.row_number == row_number,
                    )
                ).scalar_one_or_none()

                data = dict(existing.data or {}) if existing else {}

                for col_offset, value in enumerate(values_row):
                    col_index = parsed["start_col"] + col_offset
                    if col_index >= len(self.headers):
                        continue
                    data[self.headers[col_index]] = value

                if existing:
                    existing.data = data
                else:
                    session.add(
                        GoogleSheetArchiveRow(
                            source=self.source,
                            spreadsheet_id=self.spreadsheet_id,
                            sheet_name=self.title,
                            row_number=row_number,
                            data=data,
                        )
                    )
        self._sync_structured()

    def clear(self):
        with session_scope() as session:
            session.execute(
                delete(GoogleSheetArchiveRow).where(
                    GoogleSheetArchiveRow.source == self.source,
                    GoogleSheetArchiveRow.sheet_name == self.title,
                )
            )
        self._sync_structured()

    def delete_rows(self, row_number):
        rows = [
            (current_row_number, data)
            for current_row_number, data in self._rows()
            if current_row_number != int(row_number)
        ]
        resequenced = [(index, data) for index, (_, data) in enumerate(rows, start=2)]
        replace_sheet_archive(self.source, self.spreadsheet_id, self.title, resequenced)

    def resize(self, rows=None, cols=None):
        return None

    def format(self, *args, **kwargs):
        return None
