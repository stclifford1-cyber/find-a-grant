from __future__ import annotations

from collections import Counter

from app.database import SessionLocal, engine
from app.eligibility import classify_eligible_applicants
from app.models import Opportunity
from app.schema import ensure_database_schema


def run() -> dict[str, int]:
    ensure_database_schema(engine)

    db = SessionLocal()
    changed = 0
    applicant_sets: Counter[str] = Counter()
    try:
        rows = db.query(Opportunity).order_by(Opportunity.source, Opportunity.title).all()
        for row in rows:
            eligible_applicants = classify_eligible_applicants(row)
            applicant_sets[eligible_applicants] += 1
            if row.eligible_applicants != eligible_applicants:
                row.eligible_applicants = eligible_applicants
                changed += 1

        db.commit()
        return {
            "rows_seen": len(rows),
            "rows_changed": changed,
            **dict(sorted(applicant_sets.items())),
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
    for eligible_applicants, count in summary.items():
        print(f"{eligible_applicants}: {count}")
