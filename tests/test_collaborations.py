import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import ingest_all, ingest_konfer
from app.database import Base
from app.main import app, get_db
from app.models import Opportunity


def _fmt(d: date) -> str:
    return d.strftime("%b %d %Y")


def _record(mongo_id: str, title: str, end: date | None) -> dict:
    return {
        "mongoId": mongo_id,
        "title": title,
        "summary": "<p>Seeking <b>SMEs</b> to partner on a student project.</p>",
        "url": f"/collaboration/detail/{mongo_id}",
        "startDate": _fmt(date.today() - timedelta(days=5)),
        "endDate": _fmt(end) if end else "",
        "organisation": "Kingston University",
        "sector": "Engineering",
    }


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine)


def _row(id_, source, title, url=None, description=None):
    return Opportunity(
        id=id_, source=source, title=title, url=url or f"https://example.org/{id_}",
        opened_date=date.today() - timedelta(days=5), closes_date=date.today() + timedelta(days=30),
        description=description or title, status="open", last_seen=datetime.now(timezone.utc),
    )


def test_normalise_collaboration():
    item = ingest_konfer.normalise_collaboration(_record("abc", "Geothermal foundations", date.today() + timedelta(days=40)))
    assert item["id"] == "konfer-collab:abc"
    assert item["source"] == "konfer_collaboration"
    assert item["url"] == "https://konfer.online/collaboration/detail/abc"
    assert item["summary"] == "Seeking SMEs to partner on a student project."
    assert item["closes_date"] == date.today() + timedelta(days=40)
    assert item["funding_min"] is None and item["funding_max"] is None
    assert item["description"].startswith("Collaboration offer, not funding.")
    assert item["niche_tags"] == "Kingston University, Collaboration"


def test_run_collaborations_uses_collaborations_feed_and_skips_expired(monkeypatch):
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_konfer, "engine", engine)
    monkeypatch.setattr(ingest_konfer, "SessionLocal", local_session)
    requested = []

    def fake_fetch(page, api=ingest_konfer.API):
        requested.append(api)
        records = [
            _record("live", "Live collaboration", date.today() + timedelta(days=10)),
            _record("gone", "Expired collaboration", date.today() - timedelta(days=1)),
        ]
        return {"total": 2, "results": records if page == 1 else []}

    monkeypatch.setattr(ingest_konfer, "fetch_page", fake_fetch)
    ingest_konfer.run_collaborations()

    assert set(requested) == {ingest_konfer.COLLABORATIONS_API}
    db = local_session()
    assert [o.id for o in db.query(Opportunity).all()] == ["konfer-collab:live"]


def test_collaboration_refresh_only_retires_its_own_rows(monkeypatch):
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_konfer, "engine", engine)
    monkeypatch.setattr(ingest_konfer, "SessionLocal", local_session)
    db = local_session()
    db.add_all([_row("konfer:grant", "konfer", "Konfer grant"), _row("konfer-collab:old", "konfer_collaboration", "Old collab")])
    db.commit()

    ingest_konfer.upsert([], mark_stale=True, stale_sources=(ingest_konfer.COLLABORATION_SOURCE,))

    statuses = {o.id: o.status for o in local_session().query(Opportunity).all()}
    assert statuses == {"konfer:grant": "open", "konfer-collab:old": "inactive"}


def test_collaborations_never_suppress_grants_in_duplicate_pass(monkeypatch):
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)
    grant_url = "https://apply-for-innovation-funding.service.gov.uk/competition/2400/overview/x"
    db = local_session()
    db.add_all([
        _row("iuk:2400", "innovate_uk", "Engineering biology R&D", url=grant_url),
        _row("konfer-collab:1", "konfer_collaboration", "Engineering biology R&D", description=f"See {grant_url}"),
    ])
    db.commit()

    ingest_all.mark_duplicates_inactive()

    statuses = {o.id: o.status for o in local_session().query(Opportunity).all()}
    assert statuses == {"iuk:2400": "open", "konfer-collab:1": "open"}


def test_failed_collaboration_step_does_not_stop_refresh(monkeypatch):
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_all, "engine", engine)
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)
    monkeypatch.setattr(ingest_all, "mark_expired_inactive", lambda: 0)
    monkeypatch.setattr(ingest_all, "mark_duplicates_inactive", lambda: 0)
    for module in (ingest_all.ingest_innovateuk, ingest_all.ingest_iuk_business_connect, ingest_all.ingest_ukri,
                   ingest_all.ingest_horizon_europe, ingest_all.ingest_konfer):
        monkeypatch.setattr(module, "run", lambda: 1)
    monkeypatch.setattr(ingest_all.ingest_konfer, "run_collaborations",
                        lambda: (_ for _ in ()).throw(RuntimeError("HTTP 503")))

    results = ingest_all.run()

    assert results["overall_status"] == "partial_success"
    assert results["source_failures"] == {"konfer_collaboration": "HTTP 503"}
    assert results["konfer"] == 1


@pytest.fixture
def client():
    engine, local_session = _session_factory()
    session = local_session()
    session.add_all([
        _row("iuk:1", "innovate_uk", "Engineering biology grant"),
        _row("konfer-collab:1", "konfer_collaboration", "Engineering biology student project"),
    ])
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()
    session.close()


def test_collaborations_appear_in_all_with_not_funding_tag(client):
    html = client.get("/opportunities").text
    assert "Engineering biology grant" in html
    assert "Engineering biology student project" in html
    assert html.count("Not funding · collaboration") == 1


def test_collaborations_filter_button(client):
    page = client.get("/").text
    assert 'value="konfer_collaboration"' in page and "Konfer Collaborations" in page
    html = client.get("/opportunities", params={"source": "konfer_collaboration"}).text
    assert "Engineering biology student project" in html
    assert "Engineering biology grant" not in html


def test_csv_marks_collaborations_not_funding(client):
    body = client.get("/opportunities.csv").content.decode("utf-8-sig")
    rows = {r[0]: r[3] for r in csv.reader(io.StringIO(body))}
    assert rows["Engineering biology student project"] == "Not funding"
    assert rows["Engineering biology grant"] == ""
