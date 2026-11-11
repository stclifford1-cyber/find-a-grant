from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_eit
from app.database import Base
from app.models import Opportunity


def _sample(**overrides) -> dict:
    call = {
        "title": "SME Market Expansion Open Call 2027",
        "kic": "EIT Urban Mobility",
        "url": "https://www.eit.europa.eu/our-activities/opportunities/sme-market-expansion-open-call-2027",
        "opened": "2026-10-09",
        "closes": "2026-12-01",
        "funding_min_eur": 60000,
        "funding_max_eur": 60000,
        "eligible_applicants": "business",
        "not_funding": False,
        "summary": "A mono-beneficiary call from EIT Urban Mobility for SMEs.",
    }
    call.update(overrides)
    return call


def test_normalise_maps_core_fields_and_uk_scope() -> None:
    item = ingest_eit.normalise(_sample(), "2026-11-11")
    assert item["id"] == "eit:sme-market-expansion-open-call-2027"
    assert item["source"] == "eit"
    assert item["closes_date"] == date(2026, 12, 1)
    assert item["geographic_scope"] == "uk_wide"
    assert item["niche_tags"] == "EIT Urban Mobility"
    assert item["eligible_applicants"] == "business"
    assert "More details: https://www.eit.europa.eu" in item["description"]
    assert "captured on 2026-11-11" in item["description"]


def test_lump_sum_funding_is_stored_in_eur() -> None:
    item = ingest_eit.normalise(_sample(funding_min_eur=60000, funding_max_eur=60000), "2026-11-11")
    assert item["funding_currency"] == "EUR"
    assert item["funding_min_native"] == 60000
    assert item["funding_max_native"] == 60000
    assert "lump sum of EUR 60,000" in item["description"]


def test_ceiling_funding_stores_max_only() -> None:
    item = ingest_eit.normalise(_sample(funding_min_eur=None, funding_max_eur=1250000), "2026-11-11")
    assert item["funding_currency"] == "EUR"
    assert item["funding_min_native"] is None
    assert item["funding_max_native"] == 1250000
    assert "up to EUR 1,250,000" in item["description"]


def test_blank_funding_leaves_value_empty() -> None:
    item = ingest_eit.normalise(_sample(funding_min_eur=None, funding_max_eur=None), "2026-11-11")
    assert item["funding_currency"] is None
    assert item["funding_min_native"] is None
    assert item["funding_max_native"] is None


def test_missing_eligible_applicants_defaults_to_any() -> None:
    sample = _sample()
    del sample["eligible_applicants"]
    item = ingest_eit.normalise(sample, "2026-11-11")
    assert item["eligible_applicants"] == "any"


def test_not_funding_calls_route_to_their_own_source() -> None:
    item = ingest_eit.normalise(_sample(not_funding=True), "2026-11-11")
    assert item["source"] == "eit_not_funding"


def _memory_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_eit, "engine", engine)
    monkeypatch.setattr(ingest_eit, "SessionLocal", local_session)
    return local_session


def test_upsert_skips_expired_and_sweeps_removed_rows(monkeypatch) -> None:
    local_session = _memory_session(monkeypatch)
    db = local_session()
    db.add(
        Opportunity(
            id="eit:gone-call",
            source="eit",
            title="Withdrawn EIT call",
            url="https://www.eit.europa.eu/our-activities/opportunities/gone-call/",
            closes_date=date(2027, 1, 1),
            description="Old",
            status="open",
            last_seen=ingest_eit.datetime.now(ingest_eit.timezone.utc),
        )
    )
    db.commit()
    db.close()

    live = ingest_eit.normalise(_sample(), "2026-11-11")
    expired = ingest_eit.normalise(_sample(closes="2020-01-01", url="https://x/old/"), "2026-11-11")
    ingest_eit.upsert([live, expired], mark_stale=True)

    db = local_session()
    try:
        assert db.query(Opportunity).filter(Opportunity.id == live["id"]).one().status == "open"
        assert db.query(Opportunity).filter(Opportunity.id == expired["id"]).one_or_none() is None
        assert db.query(Opportunity).filter(Opportunity.id == "eit:gone-call").one().status == "inactive"
    finally:
        db.close()


def test_committed_dataset_is_uk_eligible_and_valid() -> None:
    data = ingest_eit.load_dataset()
    calls = data["calls"]
    assert calls, "dataset should not be empty"
    assert data.get("captured_at")
    for call in calls:
        assert {"title", "kic", "url", "closes", "eligible_applicants", "summary"} <= call.keys()
        assert call["url"].startswith("https://www.eit.europa.eu/")
        # HORIZON-EIT-* calls belong to the Horizon Europe source, not here.
        assert "HORIZON-EIT" not in call["url"].upper()
