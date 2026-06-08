from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base
from app.models import Opportunity


def test_cron_ingest_rejects_anonymous_request(monkeypatch) -> None:
    called = False

    def fake_run(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setattr(main.ingest_all, "run", fake_run)

    with TestClient(main.app) as client:
        response = client.get("/api/ingest")

    assert response.status_code == 401
    assert called is False


def test_cron_ingest_rejects_wrong_bearer_token(monkeypatch) -> None:
    called = False

    def fake_run(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setattr(main.ingest_all, "run", fake_run)

    with TestClient(main.app) as client:
        response = client.get("/api/ingest", headers={"Authorization": "Bearer wrong-secret"})

    assert response.status_code == 401
    assert called is False


def test_cron_ingest_accepts_correct_bearer_token(monkeypatch) -> None:
    called = False

    def fake_run(**kwargs):
        nonlocal called
        called = True
        assert kwargs["deadline"] is not None
        return {"innovate_uk": 1}

    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setenv("INGEST_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(main.ingest_all, "run", fake_run)

    with TestClient(main.app) as client:
        response = client.get("/api/ingest", headers={"Authorization": "Bearer test-secret"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["results"] == {"innovate_uk": 1}
    assert called is True


def test_cron_ingest_rejects_when_cron_secret_is_unset(monkeypatch) -> None:
    called = False

    def fake_run(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setattr(main.ingest_all, "run", fake_run)

    with TestClient(main.app) as client:
        response = client.get("/api/ingest", headers={"Authorization": "Bearer anything"})

    assert response.status_code == 401
    assert called is False


def test_test_enrichment_fetches_first_unenriched_innovate_record(monkeypatch) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = local_session()
    db.add(
        Opportunity(
            id="ifs:1234",
            source="innovate_uk",
            title="Diagnostic competition",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview",
            summary="Short summary",
            description="Short summary",
            status="open",
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.close()

    def override_get_db():
        session = local_session()
        try:
            yield session
        finally:
            session.close()

    def fake_fetch_detail_page(record_id: str) -> str:
        assert record_id == "ifs:1234"
        return "<main>detail page</main>"

    def fake_parse_detail_page(html: str, source_url: str | None = None) -> dict:
        assert html == "<main>detail page</main>"
        assert source_url == "https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview"
        return {
            "description": "Full parsed detail",
            "funding_min": 100000.0,
            "funding_max": 200000.0,
        }

    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setattr(main.ingest_innovateuk, "fetch_detail_page", fake_fetch_detail_page)
    monkeypatch.setattr(main.ingest_innovateuk, "parse_detail_page", fake_parse_detail_page)
    main.app.dependency_overrides[main.get_db] = override_get_db
    try:
        with TestClient(main.app) as client:
            response = client.get("/api/test-enrichment", headers={"Authorization": "Bearer test-secret"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "competition_id": "ifs:1234",
        "title": "Diagnostic competition",
        "detail_url": "https://apply-for-innovation-funding.service.gov.uk/competition/1234/overview",
        "success": True,
        "description_length": 18,
        "funding_min": 100000.0,
        "funding_max": 200000.0,
        "error": None,
    }
