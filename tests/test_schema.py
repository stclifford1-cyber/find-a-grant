from sqlalchemy import create_engine, inspect

from app.models import AppMetadata, Opportunity
from app.schema import ensure_database_schema


def test_ensure_database_schema_creates_required_tables_on_fresh_database() -> None:
    engine = create_engine("sqlite:///:memory:")

    ensure_database_schema(engine)

    tables = set(inspect(engine).get_table_names())
    assert Opportunity.__tablename__ in tables
    assert AppMetadata.__tablename__ in tables
