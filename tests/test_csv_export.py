import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app, csv_filename, get_db
from app.models import Opportunity

HEADER = ["Title", "Source", "Status", "Funding?", "Opens", "Closes", "Funding Min GBP", "Funding Max GBP", "Tags", "URL"]


def _row(id_, title, source="innovate_uk", **kw):
    today = date.today()
    return Opportunity(
        id=id_,
        source=source,
        title=title,
        url=f"https://example.org/{id_}",
        opened_date=kw.get("opened", today - timedelta(days=10)),
        closes_date=kw.get("closes", today + timedelta(days=30)),
        funding_min=kw.get("funding_min"),
        funding_max=kw.get("funding_max"),
        sector_tags=kw.get("sector_tags"),
        niche_tags=kw.get("niche_tags"),
        description=kw.get("description", title),
        status=kw.get("status", "open"),
        last_seen=datetime.now(timezone.utc),
    )


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        _row("EB-1", "Engineering biology mission hub", funding_min=100000, funding_max=500000,
             sector_tags="Life sciences", niche_tags="Synthetic biology"),
        _row("UKRI-1", "Quantum networks call", source="ukri"),
        _row("EVIL-1", "=HYPERLINK(\"http://x\",\"click\")"),
        _row("OLD-1", "Engineering biology expired", closes=date.today() - timedelta(days=1)),
        _row("UP-1", "Engineering biology future round", status="upcoming",
             opened=date.today() + timedelta(days=20), closes=date.today() + timedelta(days=60)),
    ])
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()
    session.close()


def _parse(response):
    assert response.content.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_csv_respects_keyword_and_includes_all_sections(client):
    response = client.get("/opportunities.csv", params={"keyword": "engineering biology"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = _parse(response)
    assert rows[0] == HEADER
    assert sorted(r[0] for r in rows[1:]) == ["Engineering biology future round", "Engineering biology mission hub"]


def test_csv_row_contents(client):
    rows = _parse(client.get("/opportunities.csv", params={"keyword": "mission hub"}))
    title, source, status, _funding, _opens, _closes, fmin, fmax, tags, url = rows[1]
    assert (title, source, status, fmin, fmax, url) == (
        "Engineering biology mission hub", "Innovate UK", "open", "100000", "500000", "https://example.org/EB-1")
    assert tags == "Life sciences; Synthetic biology"


def test_csv_respects_source_filter(client):
    rows = _parse(client.get("/opportunities.csv", params={"source": "ukri"}))
    assert [r[0] for r in rows[1:]] == ["Quantum networks call"]


def test_empty_search_gives_header_only(client):
    rows = _parse(client.get("/opportunities.csv", params={"keyword": "no-such-thing-xyz"}))
    assert rows == [HEADER]


def test_formula_like_titles_are_neutralised(client):
    rows = _parse(client.get("/opportunities.csv", params={"keyword": "HYPERLINK"}))
    assert rows[1][0].startswith("'=")


def test_filename_describes_search():
    now = datetime(2026, 9, 24, 14, 32)
    assert csv_filename("Engineering Biology!", ["innovate_uk"], now) == "find-a-grant_engineering-biology_2026-09-24_1432.csv"
    assert csv_filename(None, ["innovate_uk", "ukri"], now) == "find-a-grant_innovate-uk_ukri_2026-09-24_1432.csv"
    assert csv_filename("", [], now) == "find-a-grant_all_2026-09-24_1432.csv"


def test_download_sets_attachment_filename(client):
    disposition = client.get("/opportunities.csv", params={"keyword": "quantum"}).headers["content-disposition"]
    assert disposition.startswith('attachment; filename="find-a-grant_quantum_')
