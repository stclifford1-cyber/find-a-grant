from __future__ import annotations

import time

from app.database import SessionLocal, engine
from app.ingest_innovateuk import apply_enrichment, detail_url, fetch_detail_page, parse_detail_page
from app.models import Opportunity
from app.schema import ensure_database_schema


REQUEST_DELAY_SECONDS = 1.0


def run() -> dict[str, int]:
    ensure_database_schema(engine)

    db = SessionLocal()
    enriched_count = 0
    failed_count = 0
    skipped_count = 0

    try:
        rows = (
            db.query(Opportunity)
            .filter(
                Opportunity.source.in_(["innovate_uk", "Innovate UK"]),
                Opportunity.description == Opportunity.summary,
            )
            .order_by(Opportunity.title)
            .all()
        )

        for row in rows:
            url = detail_url(row.id)
            if not url:
                skipped_count += 1
                print(f"Skipped: invalid Innovate UK id {row.id!r}")
                continue

            try:
                enriched = parse_detail_page(fetch_detail_page(row.id), source_url=url)
                if not apply_enrichment(row, enriched):
                    skipped_count += 1
                    print(f"Skipped: no detail text found for {row.title} ({row.id})")
                    db.rollback()
                else:
                    db.commit()
                    enriched_count += 1
                    print(f"Enriched: {row.title} ({row.id})")
            except Exception as exc:
                db.rollback()
                failed_count += 1
                print(f"Warning: failed to enrich {row.title} ({row.id}): {exc}")

            time.sleep(REQUEST_DELAY_SECONDS)

        return {
            "enriched": enriched_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }
    finally:
        db.close()


if __name__ == "__main__":
    summary = run()
    print(
        "Summary: "
        f"{summary['enriched']} enriched, "
        f"{summary['failed']} failed, "
        f"{summary['skipped']} skipped"
    )
