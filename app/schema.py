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
}


def ensure_database_schema(bind) -> None:
    Base.metadata.create_all(bind=bind)

    inspector = inspect(bind)
    if Opportunity.__tablename__ not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns(Opportunity.__tablename__)}
    missing = [name for name in EXTRA_COLUMNS if name not in existing]
    if not missing:
        return

    with bind.begin() as connection:
        for name in missing:
            column_type = EXTRA_COLUMNS[name]().compile(dialect=bind.dialect)
            connection.execute(text(f"ALTER TABLE {Opportunity.__tablename__} ADD COLUMN {name} {column_type}"))
