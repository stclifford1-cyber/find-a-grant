from sqlalchemy import create_engine, inspect, text

from app.models import AppMetadata, Opportunity
from app.schema import ensure_database_schema, migrate_geographic_scope


def test_ensure_database_schema_creates_required_tables_on_fresh_database() -> None:
    engine = create_engine("sqlite:///:memory:")

    ensure_database_schema(engine)

    tables = set(inspect(engine).get_table_names())
    assert Opportunity.__tablename__ in tables
    assert AppMetadata.__tablename__ in tables
    columns = {column["name"] for column in inspect(engine).get_columns(Opportunity.__tablename__)}
    assert "geographic_scope" in columns


def test_migrate_geographic_scope_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE opportunities (
                    id VARCHAR PRIMARY KEY,
                    source VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    url VARCHAR NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR NOT NULL,
                    last_seen DATETIME NOT NULL
                )
                """
            )
        )

    first = migrate_geographic_scope(engine)
    second = migrate_geographic_scope(engine)

    columns = {column["name"] for column in inspect(engine).get_columns(Opportunity.__tablename__)}
    assert "geographic_scope" in columns
    assert first == {
        "geographic_scope_present": True,
        "total_rows": 0,
        "geographic_scope_populated_rows": 0,
    }
    assert second == first
