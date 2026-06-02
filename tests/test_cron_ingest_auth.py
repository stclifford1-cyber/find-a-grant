from fastapi.testclient import TestClient

from app import main


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
