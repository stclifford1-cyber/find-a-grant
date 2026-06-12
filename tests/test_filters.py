from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.main import apply_filters, clean_tags, get_sources
from app.models import Opportunity


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return local_session()


def _seed(db: Session) -> None:
    rows = [
        Opportunity(
            id="OPEN-1",
            source="Innovate UK",
            title="Manufacturing Robotics Pilot",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/1000/overview",
            opened_date=date(2026, 4, 1),
            closes_date=date(2026, 8, 1),
            funding_min=50000,
            funding_max=250000,
            sector_tags="Manufacturing",
            niche_tags="Robotics",
            description="Pilot projects to improve line productivity.",
            status="open",
            last_seen=datetime.now(timezone.utc),
        ),
        Opportunity(
            id="ROLL-1",
            source="Konfer",
            title="Digital Operations Rolling Call",
            url="https://www.konfer.online/funding/digital-operations",
            opened_date=date(2025, 11, 1),
            closes_date=None,
            funding_min=10000,
            funding_max=100000,
            sector_tags="Technology",
            niche_tags="Cloud",
            description="Support for cyber upgrades and resilience.",
            status="rolling",
            last_seen=datetime.now(timezone.utc),
        ),
        Opportunity(
            id="UPCOMING-1",
            source="Innovate UK",
            title="Future Freight AI Challenge",
            url="https://apply-for-innovation-funding.service.gov.uk/competition/1010/overview",
            opened_date=date(2026, 9, 1),
            closes_date=date(2026, 12, 1),
            funding_min=200000,
            funding_max=1200000,
            sector_tags="Logistics",
            niche_tags="Forecasting",
            description="AI modelling for multi-modal freight planning.",
            status="upcoming",
            last_seen=datetime.now(timezone.utc),
        ),
    ]
    db.add_all(rows)
    db.commit()


def _flatten(grouped: dict[str, list[Opportunity]]) -> list[str]:
    return [item.id for group in grouped.values() for item in group]


def test_clean_tags_hides_filing_codes_and_redundant_programme_names() -> None:
    tags = [
        "HORIZON-INFRA-2026-TECH-01-02",
        "HORIZON-INFRA-2026-01",
        "HORIZON Research and Innovation Actions",
        "Research Infrastructures 2026",
        "Horizon Europe",
        "Research Infrastructures 2026",
    ]

    assert clean_tags(tags, "Horizon Europe") == [
        "Research and Innovation Actions",
        "Research Infrastructures 2026",
    ]


def test_keyword_filters_title_and_description() -> None:
    db = _make_session()
    try:
        _seed(db)
        db.query(Opportunity).filter(Opportunity.id == "OPEN-1").one().summary = (
            "Sensors for contamination monitoring in production environments."
        )
        db.commit()

        grouped = apply_filters(db, "freight", None, None, None, None, None, None, None)
        assert _flatten(grouped) == ["UPCOMING-1"]

        grouped = apply_filters(db, "cyber", None, None, None, None, None, None, None)
        assert _flatten(grouped) == ["ROLL-1"]

        grouped = apply_filters(db, "contamination", None, None, None, None, None, None, None)
        assert _flatten(grouped) == ["OPEN-1"]
    finally:
        db.close()


def test_sector_or_niche_also_filters_description() -> None:
    db = _make_session()
    try:
        _seed(db)
        grouped = apply_filters(db, None, None, None, None, None, "robotics", None, None)
        assert _flatten(grouped) == ["OPEN-1"]

        grouped = apply_filters(db, None, None, None, None, None, "resilience", None, None)
        assert _flatten(grouped) == ["ROLL-1"]
    finally:
        db.close()


def test_source_and_date_and_funding_filters() -> None:
    db = _make_session()
    try:
        _seed(db)

        grouped = apply_filters(db, None, "Konfer", None, None, None, None, None, None)
        assert _flatten(grouped) == ["ROLL-1"]

        grouped = apply_filters(
            db,
            None,
            None,
            date(2026, 3, 1),
            None,
            date(2026, 10, 1),
            None,
            None,
            None,
        )
        assert _flatten(grouped) == ["OPEN-1"]

        grouped = apply_filters(db, None, None, None, None, None, None, 150000, 300000)
        assert _flatten(grouped) == ["OPEN-1", "UPCOMING-1"]
    finally:
        db.close()


def test_multiple_source_filters_use_normalised_aliases() -> None:
    db = _make_session()
    try:
        _seed(db)
        db.add(
            Opportunity(
                id="IUK-BC-1",
                source="iuk_business_connect",
                title="Business Connect Programme",
                url="https://iuk-business-connect.org.uk/opportunities/example/",
                opened_date=date(2026, 5, 1),
                closes_date=date(2026, 9, 1),
                description="A Business Connect opportunity stored from the second Innovate UK source.",
                status="open",
                last_seen=datetime.now(timezone.utc),
            )
        )
        db.commit()

        grouped = apply_filters(db, None, ["innovate_uk"], None, None, None, None, None, None)
        assert _flatten(grouped) == ["OPEN-1", "IUK-BC-1", "UPCOMING-1"]

        grouped = apply_filters(db, None, ["innovate_uk", "konfer"], None, None, None, None, None, None)
        assert _flatten(grouped) == ["OPEN-1", "IUK-BC-1", "ROLL-1", "UPCOMING-1"]
    finally:
        db.close()


def test_core_source_filters_are_available_before_every_source_has_rows() -> None:
    db = _make_session()
    try:
        _seed(db)
        assert get_sources(db) == [
            {"value": "innovate_uk", "label": "Innovate UK"},
            {"value": "ukri", "label": "UKRI"},
            {"value": "horizon_europe", "label": "Horizon Europe"},
            {"value": "konfer", "label": "Konfer"},
        ]
    finally:
        db.close()


def test_konfer_source_filter_stays_available_without_live_rows() -> None:
    db = _make_session()
    try:
        db.add(
            Opportunity(
                id="OPEN-1",
                source="Innovate UK",
                title="Manufacturing Robotics Pilot",
                url="https://apply-for-innovation-funding.service.gov.uk/competition/1000/overview",
                opened_date=date(2026, 4, 1),
                closes_date=date(2026, 8, 1),
                description="Pilot projects to improve line productivity.",
                status="open",
                last_seen=datetime.now(timezone.utc),
            )
        )
        db.commit()

        assert get_sources(db) == [
            {"value": "innovate_uk", "label": "Innovate UK"},
            {"value": "ukri", "label": "UKRI"},
            {"value": "horizon_europe", "label": "Horizon Europe"},
            {"value": "konfer", "label": "Konfer"},
        ]
    finally:
        db.close()


def test_stale_closed_rows_are_hidden() -> None:
    db = _make_session()
    try:
        _seed(db)
        db.add(
            Opportunity(
                id="STALE-1",
                source="Innovate UK",
                title="Stale Stored Open Competition",
                url="https://apply-for-innovation-funding.service.gov.uk/competition/999/overview",
                opened_date=date(2026, 1, 1),
                closes_date=date(2026, 5, 29),
                description="This was stored as open but has now closed.",
                status="open",
                last_seen=datetime.now(timezone.utc),
            )
        )
        db.commit()

        grouped = apply_filters(db, "stale", None, None, None, None, None, None, None)
        assert _flatten(grouped) == []
    finally:
        db.close()
