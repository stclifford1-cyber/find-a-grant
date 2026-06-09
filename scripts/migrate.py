from __future__ import annotations

from app.database import engine
from app.schema import migrate_geographic_scope


def run() -> dict[str, int | bool]:
    return migrate_geographic_scope(engine)


if __name__ == "__main__":
    summary = run()
    print(f"geographic_scope present: {summary['geographic_scope_present']}")
    print(f"eligible_applicants present: {summary['eligible_applicants_present']}")
    print(f"total rows: {summary['total_rows']}")
    print(f"rows where geographic_scope IS NOT NULL: {summary['geographic_scope_populated_rows']}")
    print(f"rows where eligible_applicants IS NOT NULL: {summary['eligible_applicants_populated_rows']}")
