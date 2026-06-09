from __future__ import annotations

from sqlalchemy import Date, Float, String, inspect, text

from .database import Base
from .models import Opportunity


EXTRA_COLUMNS = {
    "funding_currency": String,
    "funding_min_native": Float,
    "funding_max_native": Float,
    "exchange_rate": Float,
    "exchange_rate_date": Date,
    "geographic_scope": String,
    "eligible_applicants": String,
}


def _opportunities_columns(bind) -> set[str]:
    inspector = inspect(bind)
    if Opportunity.__tablename__ not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(Opportunity.__tablename__)}


def ensure_database_schema(bind) -> None:
    Base.metadata.create_all(bind=bind)

    existing = _opportunities_columns(bind)
    if not existing:
        return
    missing = [name for name in EXTRA_COLUMNS if name not in existing]
    if not missing:
        return

    with bind.begin() as connection:
        for name in missing:
            column_type = EXTRA_COLUMNS[name]().compile(dialect=bind.dialect)
            connection.execute(text(f"ALTER TABLE {Opportunity.__tablename__} ADD COLUMN {name} {column_type}"))


def migrate_geographic_scope(bind) -> dict[str, int | bool]:
    Base.metadata.create_all(bind=bind)

    if bind.dialect.name == "postgresql":
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS geographic_scope TEXT"))
            connection.execute(text("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS eligible_applicants TEXT"))
    elif "geographic_scope" not in _opportunities_columns(bind):
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE opportunities ADD COLUMN geographic_scope TEXT"))
    if bind.dialect.name != "postgresql" and "eligible_applicants" not in _opportunities_columns(bind):
        with bind.begin() as connection:
            connection.execute(text("ALTER TABLE opportunities ADD COLUMN eligible_applicants TEXT"))

    geographic_scope_present = "geographic_scope" in _opportunities_columns(bind)
    eligible_applicants_present = "eligible_applicants" in _opportunities_columns(bind)
    with bind.connect() as connection:
        total_rows = connection.execute(text("SELECT COUNT(*) FROM opportunities")).scalar_one()
        populated_rows = connection.execute(
            text("SELECT COUNT(*) FROM opportunities WHERE geographic_scope IS NOT NULL")
        ).scalar_one()
        eligible_applicants_populated_rows = connection.execute(
            text("SELECT COUNT(*) FROM opportunities WHERE eligible_applicants IS NOT NULL")
        ).scalar_one()

    return {
        "geographic_scope_present": geographic_scope_present,
        "eligible_applicants_present": eligible_applicants_present,
        "total_rows": int(total_rows),
        "geographic_scope_populated_rows": int(populated_rows),
        "eligible_applicants_populated_rows": int(eligible_applicants_populated_rows),
    }
