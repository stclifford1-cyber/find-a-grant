from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_konfer
from app.database import Base
from app.models import Opportunity


KONFER_RECORD = {
    "mongoId": "abc123",
    "award": "Up to \u00a32m is available across projects.",
    "title": "Transport Research and Innovation Grants",
    "summary": "<p>Funding to develop feasibility studies and prototypes.</p>",
    "url": "/funding/detail/abc123",
    "registrationStartDate": "May 1st 2026",
    "registrationCloseDate": "Jun 6th 2026",
    "fundingUrl": "https://example.com/apply",
    "organisation": "Department for Transport",
    "sector": "Transport",
}


def test_normalise_konfer_record() -> None:
    item = ingest_konfer.normalise_record(KONFER_RECORD)

    assert item["id"] == "konfer:abc123"
    assert item["source"] == "konfer"
    assert item["title"] == "Transport Research and Innovation Grants"
    assert item["url"] == "https://example.com/apply"
    assert item["summary"] == "Funding to develop feasibility studies and prototypes."
    assert item["opened_date"] == date(2026, 5, 1)
    assert item["closes_date"] == date(2026, 6, 6)
    assert item["funding_max"] == 2_000_000
    assert item["sector_tags"] == "Transport"
    assert item["niche_tags"] == "Department for Transport, Grant Funding"
    assert "Konfer page: https://konfer.online/funding/detail/abc123" in item["description"]


def test_konfer_upsert_marks_seed_rows_inactive(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_konfer, "SessionLocal", local_session)

    db = local_session()
    db.add(
        Opportunity(
            id="KONFER-ROLL-DIGITAL-021",
            source="Konfer",
            title="Digital Adoption Grant Calls (Rolling)",
            url="https://www.konfer.online/funding/digital-adoption-grants-rolling",
            opened_date=date(2025, 10, 1),
            closes_date=None,
            description="Old seeded row",
            status="rolling",
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.close()

    item = ingest_konfer.normalise_record(KONFER_RECORD)
    changed = ingest_konfer.upsert([item])

    db = local_session()
    try:
        fresh = db.query(Opportunity).filter(Opportunity.id == "konfer:abc123").one()
        stale = db.query(Opportunity).filter(Opportunity.id == "KONFER-ROLL-DIGITAL-021").one()
        assert changed == 2
        assert fresh.status == "open"
        assert stale.status == "inactive"
    finally:
        db.close()


def test_konfer_crawl_skips_business_connect_funding_urls(monkeypatch) -> None:
    def fake_fetch_page(page: int = 1) -> dict:
        if page > 1:
            return {"total": 2, "results": []}
        return {
            "total": 2,
            "results": [
                {
                    **KONFER_RECORD,
                    "mongoId": "business-connect",
                    "fundingUrl": "https://iuk-business-connect.org.uk/opportunities/example/",
                },
                {
                    **KONFER_RECORD,
                    "mongoId": "native",
                    "fundingUrl": "https://example.com/native",
                },
            ],
        }

    monkeypatch.setattr(ingest_konfer, "fetch_page", fake_fetch_page)

    items = ingest_konfer.crawl()

    assert [item["id"] for item in items] == ["konfer:native"]


def test_fetch_page_uses_konfer_frontend_search_params(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"total": 0, "results": []}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ingest_konfer.requests, "get", fake_get)

    data = ingest_konfer.fetch_page(3)

    assert data == {"total": 0, "results": []}
    assert captured["url"] == ingest_konfer.API
    assert captured["params"] == {
        "q": "",
        "page": 3,
        "itemsRequired": ingest_konfer.PAGE_SIZE,
        "sortBy": "openDate",
    }
    assert captured["headers"]["Referer"] == "https://konfer.online/funding"
    assert captured["timeout"] == 25
