from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import ingest_all, main
from app.database import Base
from app.models import AppMetadata


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_successful_ingest_records_last_successful_timestamp(monkeypatch) -> None:
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_all, "engine", engine)
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)
    monkeypatch.setattr(ingest_all, "mark_expired_inactive", lambda: 0)
    monkeypatch.setattr(ingest_all, "mark_duplicates_inactive", lambda: 0)
    monkeypatch.setattr(ingest_all.ingest_innovateuk, "run", lambda: 1)
    monkeypatch.setattr(ingest_all.ingest_iuk_business_connect, "run", lambda: 2)
    monkeypatch.setattr(ingest_all.ingest_ukri, "run", lambda: 3)
    monkeypatch.setattr(ingest_all.ingest_horizon_europe, "run", lambda: 4)
    monkeypatch.setattr(ingest_all.ingest_konfer, "run", lambda: 5)

    results = ingest_all.run()

    assert results == {
        "expired_marked_inactive_before": 0,
        "innovate_uk": 1,
        "iuk_business_connect": 2,
        "ukri": 3,
        "horizon_europe": 4,
        "konfer": 5,
        "duplicates_marked_inactive": 0,
        "expired_marked_inactive_after": 0,
        "overall_status": "success",
    }

    db = local_session()
    try:
        row = db.query(AppMetadata).filter(AppMetadata.key == ingest_all.LAST_SUCCESSFUL_INGEST_KEY).one()
        assert datetime.fromisoformat(row.value).tzinfo is not None
        run = db.query(AppMetadata).filter(AppMetadata.key == ingest_all.LAST_INGEST_RUN_KEY).one()
        assert '"status": "success"' in run.value
        horizon = db.query(AppMetadata).filter(AppMetadata.key == "source_status:horizon_europe").one()
        assert '"count": 4' in horizon.value
        assert '"status": "success"' in horizon.value
    finally:
        db.close()


def test_source_failure_records_partial_success_and_continues(monkeypatch) -> None:
    engine, local_session = _session_factory()
    monkeypatch.setattr(ingest_all, "engine", engine)
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)
    monkeypatch.setattr(ingest_all, "mark_expired_inactive", lambda: 0)
    monkeypatch.setattr(ingest_all, "mark_duplicates_inactive", lambda: 7)
    monkeypatch.setattr(ingest_all.ingest_innovateuk, "run", lambda: 1)
    monkeypatch.setattr(ingest_all.ingest_iuk_business_connect, "run", lambda: 2)
    monkeypatch.setattr(ingest_all.ingest_ukri, "run", lambda: 3)
    monkeypatch.setattr(ingest_all.ingest_horizon_europe, "run", lambda: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    monkeypatch.setattr(ingest_all.ingest_konfer, "run", lambda: 5)

    results = ingest_all.run()

    assert results["innovate_uk"] == 1
    assert results["iuk_business_connect"] == 2
    assert results["ukri"] == 3
    assert results["konfer"] == 5
    assert results["duplicates_marked_inactive"] == 7
    assert results["overall_status"] == "partial_success"
    assert results["source_failures"] == {"horizon_europe": "HTTP 500"}

    db = local_session()
    try:
        assert db.query(AppMetadata).filter(AppMetadata.key == ingest_all.LAST_SUCCESSFUL_INGEST_KEY).one_or_none() is None
        run = db.query(AppMetadata).filter(AppMetadata.key == ingest_all.LAST_INGEST_RUN_KEY).one()
        assert '"status": "partial_success"' in run.value
        horizon = db.query(AppMetadata).filter(AppMetadata.key == "source_status:horizon_europe").one()
        assert '"error": "HTTP 500"' in horizon.value
        assert '"status": "failed"' in horizon.value
    finally:
        db.close()


def test_homepage_renders_last_successful_ingest_timestamp(monkeypatch) -> None:
    _, local_session = _session_factory()
    db = local_session()
    db.add(
        AppMetadata(
            key=main.LAST_SUCCESSFUL_INGEST_KEY,
            value=datetime(2026, 6, 2, 6, 4, tzinfo=timezone.utc).isoformat(),
        )
    )
    db.add(
        AppMetadata(
            key=main.LAST_INGEST_RUN_KEY,
            value='{"checked_at": "2026-06-02T06:04:00+00:00", "status": "success"}',
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

    main.app.dependency_overrides[main.get_db] = override_get_db
    try:
        with TestClient(main.app) as client:
            response = client.get("/")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Last updated: 2 June 2026, 06:04 UTC" in response.text
    assert "Loaded successfully" in response.text


def test_homepage_renders_partial_ingest_status(monkeypatch) -> None:
    _, local_session = _session_factory()
    db = local_session()
    db.add(
        AppMetadata(
            key=main.LAST_INGEST_RUN_KEY,
            value='{"checked_at": "2026-06-09T16:13:19+00:00", "status": "partial_success"}',
        )
    )
    db.add(
        AppMetadata(
            key="source_status:horizon_europe",
            value=(
                '{"checked_at": "2026-06-09T16:13:19+00:00", "error": "HTTP 500", '
                '"last_successful_at": "2026-06-08T06:20:00+00:00", "source": "horizon_europe", '
                '"status": "failed"}'
            ),
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

    main.app.dependency_overrides[main.get_db] = override_get_db
    try:
        with TestClient(main.app) as client:
            response = client.get("/")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Partially loaded" in response.text
    assert "Horizon Europe:" in response.text
    assert "Last successful: 8 June 2026, 06:20 UTC" in response.text


def test_homepage_renders_konfer_zero_non_duplicate_status(monkeypatch) -> None:
    _, local_session = _session_factory()
    db = local_session()
    db.add(
        AppMetadata(
            key=main.LAST_KONFER_CHECK_KEY,
            value='{"checked_at": "2026-06-04T12:51:00+00:00", "non_duplicate_records": 0}',
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

    main.app.dependency_overrides[main.get_db] = override_get_db
    try:
        with TestClient(main.app) as client:
            response = client.get("/")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Konfer checked successfully: no unique opportunities found." in response.text
