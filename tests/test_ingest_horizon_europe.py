from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_horizon_europe
from app.database import Base
from app.models import Opportunity


def _sample_result() -> dict:
    return {
        "summary": "Low power Edge AI Chips",
        "metadata": {
            "identifier": ["HORIZON-JU-CHIPS-2025-IA-LEAI-two-stage"],
            "title": ["Low power Edge AI Chips"],
            "type": ["1"],
            "status": ["31094502"],
            "startDate": ["2026-03-04T00:00:00.000+0000"],
            "deadlineDate": ["2026-04-29T00:00:00.000+0000", "2026-09-17T00:00:00.000+0000"],
            "typesOfAction": ["HORIZON JU Innovation Actions"],
            "callTitle": ["Chips Joint Undertaking Call"],
            "keywords": ["Semiconductors", "Artificial intelligence"],
            "descriptionByte": [
                '<span class="topicdescriptionkind">Expected Outcome:</span><p>Develop AI chip prototypes.</p>'
            ],
            "budgetOverview": [
                '{"budgetYearsColumns":["2026"],"budgetTopicActionMap":{"109381":[{"action":"Topic action","expectedGrants":2,"minContribution":1000000,"maxContribution":20000000,"budgetYearMap":{"2026":"20000000"},"plannedOpeningDate":"2026-03-04","deadlineModel":"two-stage","deadlineDates":["2026-04-29","2026-09-17"]}]}}'
            ],
        },
    }


def test_normalise_result_keeps_eur_and_converts_to_gbp() -> None:
    item = ingest_horizon_europe.normalise_result(_sample_result(), 0.86, date(2026, 6, 1))

    assert item is not None
    assert item["id"] == "horizon:horizon-ju-chips-2025-ia-leai-two-stage"
    assert item["source"] == "horizon_europe"
    assert item["funding_currency"] == "EUR"
    assert item["funding_min_native"] == 1_000_000
    assert item["funding_max_native"] == 20_000_000
    assert item["funding_min"] == 860_000
    assert item["funding_max"] == 17_200_000
    assert item["opened_date"] == date(2026, 3, 4)
    assert item["closes_date"] == date(2026, 9, 17)
    assert item["url"].endswith("/screen/opportunities/topic-details/HORIZON-JU-CHIPS-2025-IA-LEAI-two-stage")
    assert "Topic budget: EUR 20,000,000" in item["description"]
    assert "Develop AI chip prototypes." in item["description"]


def test_competitive_calls_link_to_portal_search() -> None:
    result = _sample_result()
    result["metadata"]["type"] = ["8"]
    result["metadata"]["url"] = [
        "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/competitive-calls-cs/45652634"
    ]

    item = ingest_horizon_europe.normalise_result(result, 0.86, date(2026, 6, 1))

    assert item is not None
    assert item["url"].endswith(
        "frameworkProgramme=43108390&type=8"
    )
    assert "/screen/opportunities/calls-for-proposals?" in item["url"]
    assert "keywords=HORIZON-JU-CHIPS-2025-IA-LEAI-two-stage" in item["url"]
    assert "isExactMatch=true" in item["url"]


def test_upsert_marks_missing_horizon_rows_inactive(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_horizon_europe, "engine", engine)
    monkeypatch.setattr(ingest_horizon_europe, "SessionLocal", local_session)

    db = local_session()
    db.add(
        Opportunity(
            id="horizon:stale",
            source="horizon_europe",
            title="Stale Horizon Topic",
            url="https://ec.europa.eu/stale",
            opened_date=date(2026, 1, 1),
            closes_date=date(2026, 12, 1),
            description="Old topic",
            status="open",
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.close()

    item = ingest_horizon_europe.normalise_result(_sample_result(), 0.86, date(2026, 6, 1))
    assert item is not None

    assert ingest_horizon_europe.upsert([item], mark_stale=True) == 2

    db = local_session()
    try:
        current = db.query(Opportunity).filter(Opportunity.id == item["id"]).one()
        stale = db.query(Opportunity).filter(Opportunity.id == "horizon:stale").one()
        assert current.status == "open"
        assert current.funding_currency == "EUR"
        assert current.funding_max == 17_200_000
        assert stale.status == "inactive"
    finally:
        db.close()
