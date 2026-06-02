from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ingest_all
from app.database import Base
from app.models import Opportunity


def _opportunity(
    id: str,
    source: str,
    title: str,
    url: str,
    closes_date: date,
    description: str = "Description",
) -> Opportunity:
    return Opportunity(
        id=id,
        source=source,
        title=title,
        url=url,
        opened_date=date(2026, 5, 1),
        closes_date=closes_date,
        description=description,
        status="open",
        last_seen=datetime.now(timezone.utc),
    )


def test_duplicate_pass_keeps_direct_innovate_uk_over_business_connect_and_konfer(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)

    db = local_session()
    db.add_all(
        [
            _opportunity(
                "ifs:2473",
                "innovate_uk",
                "Accelerated Knowledge Transfer Partnerships 6 (AKT 6)",
                "https://apply-for-innovation-funding.service.gov.uk/competition/2473/overview/token",
                date(2026, 7, 15),
            ),
            _opportunity(
                "iukbc:accelerated-knowledge-transfer-partnerships-6-akt-6",
                "iuk_business_connect",
                "Accelerated Knowledge Transfer Partnerships 6 (AKT 6)",
                "https://apply-for-innovation-funding.service.gov.uk/competition/2473/overview/token",
                date(2026, 7, 15),
            ),
            _opportunity(
                "konfer:abc",
                "konfer",
                "Accelerated Knowledge Transfer Partnerships 6 (AKT 6)",
                "https://iuk-business-connect.org.uk/opportunities/accelerated-knowledge-transfer-partnerships-6-akt-6/",
                date(2026, 7, 15),
            ),
        ]
    )
    db.commit()
    db.close()

    assert ingest_all.mark_duplicates_inactive() == 2

    db = local_session()
    try:
        direct = db.query(Opportunity).filter(Opportunity.id == "ifs:2473").one()
        business_connect = db.query(Opportunity).filter(Opportunity.source == "iuk_business_connect").one()
        konfer = db.query(Opportunity).filter(Opportunity.source == "konfer").one()
        assert direct.status == "open"
        assert business_connect.status == "inactive"
        assert konfer.status == "inactive"
    finally:
        db.close()


def test_duplicate_pass_keeps_business_connect_over_konfer_iukbc_link(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(ingest_all, "SessionLocal", local_session)

    db = local_session()
    db.add_all(
        [
            _opportunity(
                "iukbc:horizon-europe-2026-27-cancer-mission",
                "iuk_business_connect",
                "Horizon Europe 2026-27: Cancer Mission",
                "https://iuk-business-connect.org.uk/opportunities/horizon-europe-2026-27-cancer-mission/",
                date(2026, 9, 15),
            ),
            _opportunity(
                "konfer:horizon-cancer",
                "konfer",
                "Horizon Europe 2026-27: Cancer Mission",
                "https://iuk-business-connect.org.uk/opportunities/horizon-europe-2026-27-cancer-mission/",
                date(2026, 9, 15),
            ),
        ]
    )
    db.commit()
    db.close()

    assert ingest_all.mark_duplicates_inactive() == 1

    db = local_session()
    try:
        business_connect = db.query(Opportunity).filter(Opportunity.source == "iuk_business_connect").one()
        konfer = db.query(Opportunity).filter(Opportunity.source == "konfer").one()
        assert business_connect.status == "open"
        assert konfer.status == "inactive"
    finally:
        db.close()
