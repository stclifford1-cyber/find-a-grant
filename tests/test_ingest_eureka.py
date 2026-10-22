from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_eureka
from app.database import Base
from app.models import Opportunity


def _sample(**overrides) -> dict:
    call = {
        "title": "SMART Call 10 – 2026",
        "programme": "Eureka Clusters",
        "url": "https://www.eurekanetwork.org/programmes-and-calls/eureka-clusters/smart-call-10-2026/",
        "opened": "2026-07-01",
        "closes": "2027-01-26",
        "funding_eur": None,
        "not_funding": False,
        "summary": "SMART is the Eureka Cluster for advanced manufacturing.",
    }
    call.update(overrides)
    return call


def test_normalise_maps_core_fields_and_uk_scope() -> None:
    item = ingest_eureka.normalise(_sample(), "2026-10-22")
    assert item["id"] == "eureka:smart-call-10-2026"
    assert item["source"] == "eureka"
    assert item["closes_date"] == date(2027, 1, 26)
    assert item["geographic_scope"] == "uk_wide"
    assert "More details: https://www.eurekanetwork.org" in item["description"]
    assert "captured on 2026-10-22" in item["description"]


def test_published_funding_value_is_stored_in_eur() -> None:
    item = ingest_eureka.normalise(_sample(funding_eur=60000), "2026-10-22")
    assert item["funding_currency"] == "EUR"
    assert item["funding_min_native"] == 60000
    assert item["funding_max_native"] == 60000


def test_blank_funding_leaves_value_empty() -> None:
    item = ingest_eureka.normalise(_sample(funding_eur=None), "2026-10-22")
    assert item["funding_currency"] is None
    assert item["funding_min_native"] is None


def test_not_funding_calls_route_to_their_own_source() -> None:
    item = ingest_eureka.normalise(_sample(not_funding=True), "2026-10-22")
    assert item["source"] == "eureka_not_funding"


def _memory_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_eureka, "engine", engine)
    monkeypatch.setattr(ingest_eureka, "SessionLocal", local_session)
    return local_session


def test_upsert_skips_expired_and_sweeps_removed_rows(monkeypatch) -> None:
    local_session = _memory_session(monkeypatch)
    db = local_session()
    db.add(
        Opportunity(
            id="eureka:gone-call",
            source="eureka",
            title="Withdrawn Eureka call",
            url="https://www.eurekanetwork.org/programmes-and-calls/eureka-clusters/gone-call/",
            closes_date=date(2027, 1, 1),
            description="Old",
            status="open",
            last_seen=ingest_eureka.datetime.now(ingest_eureka.timezone.utc),
        )
    )
    db.commit()
    db.close()

    live = ingest_eureka.normalise(_sample(), "2026-10-22")
    expired = ingest_eureka.normalise(_sample(closes="2020-01-01", url="https://x/old/"), "2026-10-22")
    ingest_eureka.upsert([live, expired], mark_stale=True)

    db = local_session()
    try:
        assert db.query(Opportunity).filter(Opportunity.id == live["id"]).one().status == "open"
        assert db.query(Opportunity).filter(Opportunity.id == expired["id"]).one_or_none() is None
        assert db.query(Opportunity).filter(Opportunity.id == "eureka:gone-call").one().status == "inactive"
    finally:
        db.close()


def test_committed_dataset_is_uk_eligible_and_valid() -> None:
    data = ingest_eureka.load_dataset()
    calls = data["calls"]
    assert calls, "dataset should not be empty"
    assert data.get("captured_at")
    for call in calls:
        assert {"title", "programme", "url", "closes", "summary"} <= call.keys()
        assert call["url"].startswith("https://www.eurekanetwork.org/")
