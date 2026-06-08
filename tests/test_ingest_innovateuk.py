from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_innovateuk
from app.database import Base
from app.models import Opportunity


def test_parse_detail_page_extracts_description_eligibility_and_funding() -> None:
    html = """
    <main id="main-content">
      <h1>Competition overview</h1>
      <p>Navigation text outside useful sections.</p>
      <h2>Description</h2>
      <div class="wysiwyg-styles">
        <p>Develop bold industrial research projects.</p>
      </div>
      <h2>Eligibility</h2>
      <p>Your project must be led by a UK registered business.</p>
      <h2>Funding</h2>
      <p>Your project costs can be between £100,000 and £2 million.</p>
    </main>
    """

    detail = ingest_innovateuk.parse_detail_page(
        html,
        source_url="https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview",
    )

    assert "Source page: https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview" in detail["description"]
    assert "Develop bold industrial research projects." in detail["description"]
    assert "Your project must be led by a UK registered business." in detail["description"]
    assert detail["funding_min"] == 100_000
    assert detail["funding_max"] == 2_000_000


def test_upsert_preserves_enriched_description(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_innovateuk, "SessionLocal", local_session)

    db = local_session()
    db.add(
        Opportunity(
            id="ifs:1234",
            source="innovate_uk",
            title="Original title",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview",
            opened_date=date(2026, 5, 1),
            closes_date=date(2026, 7, 1),
            summary="One-line summary",
            description="Full enriched description",
            status="open",
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.close()

    changed = ingest_innovateuk.upsert(
        [
            {
                "id": "ifs:1234",
                "source": "innovate_uk",
                "title": "Updated title",
                "url": "https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview",
                "summary": "Updated one-line summary",
                "description": "Updated one-line summary",
                "opened_date": date(2026, 5, 1),
                "closes_date": date(2026, 7, 1),
            }
        ]
    )

    db = local_session()
    try:
        row = db.query(Opportunity).filter(Opportunity.id == "ifs:1234").one()
        assert changed == 1
        assert row.title == "Updated title"
        assert row.summary == "Updated one-line summary"
        assert row.description == "Full enriched description"
    finally:
        db.close()
