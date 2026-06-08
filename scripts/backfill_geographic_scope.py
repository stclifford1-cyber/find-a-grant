from __future__ import annotations

from collections import Counter

from app.database import SessionLocal, engine
from app.geography import classify_geographic_scope
from app.models import Opportunity
from app.schema import ensure_database_schema


def run() -> dict[str, int]:
    ensure_database_schema(engine)

    db = SessionLocal()
    changed = 0
    scopes: Counter[str] = Counter()
    try:
        rows = db.query(Opportunity).order_by(Opportunity.source, Opportunity.title).all()
        for row in rows:
            scope = classify_geographic_scope(row)
            scopes[scope] += 1
            if row.geographic_scope != scope:
                row.geographic_scope = scope
                changed += 1

        db.commit()
        return {
            "rows_seen": len(rows),
            "rows_changed": changed,
            **dict(sorted(scopes.items())),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    summary = run()
    print(f"Rows seen: {summary.pop('rows_seen')}")
    print(f"Rows changed: {summary.pop('rows_changed')}")
    for scope, count in summary.items():
        print(f"{scope}: {count}")
