from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_iuk_business_connect
from app.database import Base
from app.models import Opportunity


LISTING_HTML = """
<div id="search-filter-results-366">
  <div class="grid">
    <a class="group" href="https://iuk-business-connect.org.uk/opportunities/adopt-facilitator-support-grant-round-9/">
      <h3>ADOPT Facilitator Support Grant: Round 9</h3>
      <p><b>Opens:</b> 29/05/2026</p>
      <p><b>Closes:</b> 08/07/2026</p>
      <div class="line-clamp-5">Farming businesses can apply for a grant of &pound;2,500.</div>
    </a>
  </div>
  <div class="pagination">
    <a class="prev page-numbers" rel="next" href="https://iuk-business-connect.org.uk/opportunities/?sf_paged=2">Next &gt;</a>
  </div>
</div>
"""


DETAIL_HTML = """
<h1>ADOPT Facilitator Support Grant: Round 9</h1>
<div>
  <h5>Opportunity Type</h5>
  <hr>
  <div><span>Funding</span></div>
</div>
<div>
  <h5>Award</h5>
  <hr>
  <div>&pound;2,500 will be awarded to successful applicants.</div>
</div>
<div>
  <h5>Organisation</h5>
  <hr>
  <div>DEFRA</div>
</div>
<div>
  <h5>Sector</h5>
  <hr>
  <div>
    <a href="/opportunities/?_sft_sector=agrifood">Agrifood</a>,
    <a href="/opportunities/?_sft_sector=sustainability">Sustainability</a>
  </div>
</div>
<a href="https://apply-for-innovation-funding.service.gov.uk/competition/2482/overview">Find out more and apply</a>
<article class="single_opportunities">
  <div class="prose">
    <p>The Department for Environment, Food and Rural Affairs will invest in applications.</p>
    <ul><li>Support development of a Full ADOPT Grant application.</li></ul>
  </div>
</article>
"""


def test_parse_listing_page_extracts_card_and_next_page() -> None:
    items, next_url = ingest_iuk_business_connect.parse_listing_page(LISTING_HTML)

    assert next_url == "https://iuk-business-connect.org.uk/opportunities/?sf_paged=2"
    assert items == [
        {
            "id": "iukbc:adopt-facilitator-support-grant-round-9",
            "source": "iuk_business_connect",
            "title": "ADOPT Facilitator Support Grant: Round 9",
            "source_url": "https://iuk-business-connect.org.uk/opportunities/adopt-facilitator-support-grant-round-9/",
            "url": "https://iuk-business-connect.org.uk/opportunities/adopt-facilitator-support-grant-round-9/",
            "summary": "Farming businesses can apply for a grant of \u00a32,500.",
            "description": "Farming businesses can apply for a grant of \u00a32,500.",
            "opened_date": date(2026, 5, 29),
            "closes_date": date(2026, 7, 8),
            "funding_min": None,
            "funding_max": None,
            "sector_tags": None,
            "niche_tags": None,
        }
    ]


def test_parse_detail_page_extracts_structured_fields() -> None:
    detail = ingest_iuk_business_connect.parse_detail_page(
        DETAIL_HTML,
        "https://iuk-business-connect.org.uk/opportunities/adopt-facilitator-support-grant-round-9/",
    )

    assert detail["url"] == "https://apply-for-innovation-funding.service.gov.uk/competition/2482/overview"
    assert detail["opportunity_type"] == "Funding"
    assert detail["organisation"] == "DEFRA"
    assert detail["award"] == "\u00a32,500 will be awarded to successful applicants."
    assert detail["funding_min"] == 2500
    assert detail["funding_max"] == 2500
    assert detail["sector_tags"] == "Agrifood, Sustainability"
    assert detail["niche_tags"] == "Funding, DEFRA"
    assert "Source page: https://iuk-business-connect.org.uk/opportunities/adopt-facilitator-support-grant-round-9/" in detail["description"]
    assert "Support development of a Full ADOPT Grant application." in detail["description"]


def test_upsert_marks_stale_business_connect_rows_inactive(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_iuk_business_connect, "SessionLocal", local_session)

    db = local_session()
    db.add(
        Opportunity(
            id="iukbc:stale",
            source="iuk_business_connect",
            title="Old Business Connect Opportunity",
            url="https://iuk-business-connect.org.uk/opportunities/old/",
            opened_date=date(2026, 1, 1),
            closes_date=date(2026, 12, 31),
            description="Old row",
            status="open",
            last_seen=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.close()

    changed = ingest_iuk_business_connect.upsert(
        [
            {
                "id": "iukbc:fresh",
                "source": "iuk_business_connect",
                "title": "Fresh Business Connect Opportunity",
                "url": "https://apply-for-innovation-funding.service.gov.uk/competition/2482/overview",
                "opened_date": date(2026, 5, 29),
                "closes_date": date(2026, 7, 8),
                "funding_min": 2500,
                "funding_max": 2500,
                "sector_tags": "Agrifood",
                "niche_tags": "Funding, DEFRA",
                "summary": "Fresh summary",
                "description": "Fresh description",
            }
        ]
    )

    db = local_session()
    try:
        fresh = db.query(Opportunity).filter(Opportunity.id == "iukbc:fresh").one()
        stale = db.query(Opportunity).filter(Opportunity.id == "iukbc:stale").one()
        assert changed == 2
        assert fresh.status == "open"
        assert stale.status == "inactive"
    finally:
        db.close()
