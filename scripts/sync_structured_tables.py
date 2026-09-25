import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select

from modules.storage.google_archive import GoogleSheetArchiveRow
from modules.storage.postgres import session_scope
from modules.storage.structured_sheets import sync_all_structured_sheets


def main():
    with session_scope() as session:
        rows = (
            session.execute(
                select(GoogleSheetArchiveRow).order_by(
                    GoogleSheetArchiveRow.source,
                    GoogleSheetArchiveRow.sheet_name,
                    GoogleSheetArchiveRow.row_number,
                )
            )
            .scalars()
            .all()
        )

    sync_all_structured_sheets(rows)
    print(f"Synced structured tables from archive rows: {len(rows)}")


if __name__ == "__main__":
    main()
