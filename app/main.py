import logging
import os
import json
import re
import secrets
import time
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import ingest_all, ingest_innovateuk
from .database import SessionLocal, engine
from .models import AppMetadata, Opportunity
from .schema import ensure_database_schema

app = FastAPI(title="Find a Grant")
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


def format_uk_date(value: Optional[date]) -> str:
    if not value:
        return "TBD"
    return value.strftime("%d/%m/%Y")



def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None

def _parse_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

templates.env.filters["uk_date"] = format_uk_date
templates.env.globals["date"] = date

SOURCE_LABELS = {
    "innovate_uk": "Innovate UK",
    "Innovate UK": "Innovate UK",
    "iuk_business_connect": "Innovate UK",
    "horizon_europe": "Horizon Europe",
    "ukri": "UKRI",
    "konfer": "Konfer",
    "Konfer": "Konfer",
}

SOURCE_ALIASES = {
    "innovate_uk": ["innovate_uk", "Innovate UK", "iuk_business_connect"],
    "horizon_europe": ["horizon_europe"],
    "ukri": ["ukri"],
    "konfer": ["konfer", "Konfer"],
}

SOURCE_ORDER = {
    "innovate_uk": 0,
    "ukri": 1,
    "horizon_europe": 2,
    "konfer": 3,
}

CORE_SOURCE_FILTERS = ("innovate_uk", "ukri", "horizon_europe", "konfer")
DEFAULT_INGEST_TIMEOUT_SECONDS = 300.0
INGEST_TIMEOUT_ENV = "INGEST_TIMEOUT_SECONDS"
LAST_SUCCESSFUL_INGEST_KEY = "last_successful_ingest_at"
LAST_INGEST_RUN_KEY = "last_ingest_run"
SOURCE_STATUS_PREFIX = "source_status:"
LAST_KONFER_CHECK_KEY = "source_check:konfer"
INGEST_STATUS_SOURCES = (
    ("innovate_uk", "Innovate UK"),
    ("iuk_business_connect", "IUK Business Connect"),
    ("ukri", "UKRI"),
    ("horizon_europe", "Horizon Europe"),
    ("konfer", "Konfer"),
)


def source_key(value: str) -> str:
    normalised = (value or "").strip()
    if normalised == "Innovate UK":
        return "innovate_uk"
    if normalised == "iuk_business_connect":
        return "innovate_uk"
    if normalised == "Konfer":
        return "konfer"
    return normalised


def source_label(value: str) -> str:
    return SOURCE_LABELS.get(value, value.replace("_", " ").title())


def normalise_source_filter(value: Optional[str | list[str]]) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [source_key(item) for item in values if item]


def source_reference(opportunity: Opportunity) -> str:
    if opportunity.source == "horizon_europe" and opportunity.id.startswith("horizon:"):
        text = "\n".join(
            value
            for value in [
                opportunity.sector_tags,
                opportunity.niche_tags,
                opportunity.description,
                opportunity.id.removeprefix("horizon:"),
            ]
            if value
        )
        match = re.search(r"\bHORIZON-[A-Za-z0-9-]+(?:-[A-Za-z0-9]+)*\b", text)
        if match:
            return match.group(0)
        return opportunity.id.removeprefix("horizon:").upper()
    return ""


def _cron_secret() -> str:
    return os.environ.get("CRON_SECRET", "")


def _ingest_timeout_seconds() -> float:
    raw_value = os.environ.get(INGEST_TIMEOUT_ENV)
    if not raw_value:
        return DEFAULT_INGEST_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning("Invalid %s value %r; using default.", INGEST_TIMEOUT_ENV, raw_value)
        return DEFAULT_INGEST_TIMEOUT_SECONDS
    return max(1.0, value)


def format_ingest_timestamp(value: Optional[datetime]) -> str:
    if not value:
        return "Not yet updated"
    utc_value = value.astimezone(timezone.utc) if value.tzinfo else value
    return f"{utc_value.day} {utc_value.strftime('%B %Y, %H:%M')} UTC"


def get_last_successful_ingest(db: Session) -> Optional[datetime]:
    row = db.query(AppMetadata).filter(AppMetadata.key == LAST_SUCCESSFUL_INGEST_KEY).one_or_none()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        logger.warning("Invalid last successful ingest timestamp: %r", row.value)
        return None


def get_konfer_check_status(db: Session) -> Optional[str]:
    row = db.query(AppMetadata).filter(AppMetadata.key == LAST_KONFER_CHECK_KEY).one_or_none()
    if not row:
        return None
    try:
        data = json.loads(row.value)
        checked_at = datetime.fromisoformat(data["checked_at"])
        count = int(data["non_duplicate_records"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Invalid Konfer check metadata: %r", row.value)
        return None

    if count == 0:
        return "Konfer checked successfully: no unique opportunities found."
    return None


def _parse_metadata_datetime(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_json_metadata(db: Session, key: str) -> dict:
    row = db.query(AppMetadata).filter(AppMetadata.key == key).one_or_none()
    if not row:
        return {}
    try:
        value = json.loads(row.value)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON metadata for %s: %r", key, row.value)
        return {}
    return value if isinstance(value, dict) else {}


def get_ingest_status(db: Session) -> dict:
    run_data = _load_json_metadata(db, LAST_INGEST_RUN_KEY)
    legacy_last_success = get_last_successful_ingest(db)
    source_rows = []
    failed = 0
    succeeded = 0

    for source, label in INGEST_STATUS_SOURCES:
        data = _load_json_metadata(db, f"{SOURCE_STATUS_PREFIX}{source}")
        status = data.get("status") if isinstance(data.get("status"), str) else "unknown"
        if status == "success":
            succeeded += 1
        elif status == "failed":
            failed += 1

        source_rows.append(
            {
                "source": source,
                "label": label,
                "status": status,
                "count": data.get("count"),
                "checked_at": format_ingest_timestamp(_parse_metadata_datetime(data.get("checked_at"))),
                "last_successful_at": format_ingest_timestamp(
                    _parse_metadata_datetime(data.get("last_successful_at"))
                ),
                "error": data.get("error") if isinstance(data.get("error"), str) else None,
            }
        )

    run_status = run_data.get("status") if isinstance(run_data.get("status"), str) else None
    if run_status not in {"success", "partial_success", "failed"}:
        if failed and succeeded:
            run_status = "partial_success"
        elif failed:
            run_status = "failed"
        elif succeeded:
            run_status = "success"
        elif legacy_last_success:
            run_status = "success"
        else:
            run_status = "unknown"

    status_text = {
        "success": "Successful loading",
        "partial_success": "Partial successful loading",
        "failed": "Daily run failed",
        "unknown": "Ingest status unknown",
    }[run_status]

    status_class = {
        "success": "border-green-700 bg-green-100 text-green-900",
        "partial_success": "border-amber-700 bg-amber-100 text-amber-950",
        "failed": "border-red-700 bg-red-100 text-red-900",
        "unknown": "border-gray-500 bg-white/70 text-gray-900",
    }[run_status]

    return {
        "status": run_status,
        "label": status_text,
        "class": status_class,
        "checked_at": format_ingest_timestamp(_parse_metadata_datetime(run_data.get("checked_at")) or legacy_last_success),
        "sources": source_rows,
    }


def require_cron_secret(request: Request) -> None:
    expected_secret = _cron_secret()
    auth_header = request.headers.get("authorization", "")
    expected_header = f"Bearer {expected_secret}" if expected_secret else ""
    if not expected_header or not secrets.compare_digest(auth_header, expected_header):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.on_event("startup")
def on_startup() -> None:
    ensure_database_schema(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_filters(
    db: Session,
    keyword: Optional[str],
    source: Optional[str | list[str]],
    opens_from: Optional[date],
    closes_from: Optional[date],
    closes_to: Optional[date],
    sector_or_niche: Optional[str],
    min_funding: Optional[float],
    max_funding: Optional[float],
):
    query = db.query(Opportunity)
    query = query.filter(
        Opportunity.status != "inactive",
        or_(
            Opportunity.closes_date.is_(None),
            Opportunity.closes_date >= date.today(),
        ),
    )

    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                Opportunity.title.ilike(pattern),
                Opportunity.summary.ilike(pattern),
                Opportunity.description.ilike(pattern),
            )
        )

    selected_sources = normalise_source_filter(source)
    if selected_sources:
        aliases = []
        for key in selected_sources:
            aliases.extend(SOURCE_ALIASES.get(key, [key]))
        query = query.filter(Opportunity.source.in_(aliases))

    if opens_from:
        query = query.filter(Opportunity.opened_date >= opens_from)

    if closes_from:
        query = query.filter(Opportunity.closes_date >= closes_from)

    if closes_to:
        query = query.filter(Opportunity.closes_date <= closes_to)

    if sector_or_niche:
        pattern = f"%{sector_or_niche.strip()}%"
        query = query.filter(
            or_(
                Opportunity.title.ilike(pattern),
                Opportunity.summary.ilike(pattern),
                Opportunity.sector_tags.ilike(pattern),
                Opportunity.niche_tags.ilike(pattern),
                Opportunity.description.ilike(pattern),
            )
        )

    if min_funding is not None:
        query = query.filter(
            or_(
                Opportunity.funding_max.is_(None),
                Opportunity.funding_max >= min_funding,
            )
        )

    if max_funding is not None:
        query = query.filter(
            or_(
                Opportunity.funding_min.is_(None),
                Opportunity.funding_min <= max_funding,
            )
        )

    opportunities = query.order_by(
        Opportunity.status,
        Opportunity.closes_date.is_(None),
        Opportunity.closes_date,
        Opportunity.title,
    ).all()

    grouped = {"open": [], "rolling": [], "upcoming": []}
    for item in opportunities:
        if item.status in grouped:
            grouped[item.status].append(item)

    return grouped


def get_sources(db: Session):
    rows = (
        db.query(Opportunity.source)
        .filter(
            Opportunity.status != "inactive",
            or_(
                Opportunity.closes_date.is_(None),
                Opportunity.closes_date >= date.today(),
            ),
        )
        .distinct()
        .order_by(Opportunity.source)
        .all()
    )
    sources = {key: source_label(key) for key in CORE_SOURCE_FILTERS}
    for row in rows:
        key = source_key(row[0])
        sources[key] = source_label(key)
    return [
        {"value": value, "label": label}
        for value, label in sorted(sources.items(), key=lambda item: (SOURCE_ORDER.get(item[0], 99), item[1]))
    ]


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    keyword: Optional[str] = Query(default=None),
    source: Optional[list[str]] = Query(default=None),
    opens_from: Optional[str] = Query(default=None),
    closes_from: Optional[str] = Query(default=None),
    closes_to: Optional[str] = Query(default=None),
    sector_or_niche: Optional[str] = Query(default=None),
    min_funding: Optional[str] = Query(default=None),
    max_funding: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    opens_from_date = _parse_date(opens_from)
    closes_from_date = _parse_date(closes_from)
    closes_to_date = _parse_date(closes_to)
    min_funding_value = _parse_float(min_funding)
    max_funding_value = _parse_float(max_funding)
    selected_sources = normalise_source_filter(source)

    grouped = apply_filters(
        db,
        keyword,
        selected_sources,
        opens_from_date,
        closes_from_date,
        closes_to_date,
        sector_or_niche,
        min_funding_value,
        max_funding_value,
    )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "grouped": grouped,
            "sources": get_sources(db),
            "last_successful_ingest": format_ingest_timestamp(get_last_successful_ingest(db)),
            "ingest_status": get_ingest_status(db),
            "konfer_check_status": get_konfer_check_status(db),
            "filters": {
                "keyword": keyword or "",
                "sources": selected_sources,
                "opens_from": opens_from or "",
                "closes_to": closes_to or "",
                "sector_or_niche": sector_or_niche or "",
                "min_funding": min_funding or "",
                "max_funding": max_funding or "",
            },
        },
    )


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities_partial(
    request: Request,
    keyword: Optional[str] = Query(default=None),
    source: Optional[list[str]] = Query(default=None),
    opens_from: Optional[str] = Query(default=None),
    closes_from: Optional[str] = Query(default=None),
    closes_to: Optional[str] = Query(default=None),
    sector_or_niche: Optional[str] = Query(default=None),
    min_funding: Optional[str] = Query(default=None),
    max_funding: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    opens_from_date = _parse_date(opens_from)
    closes_from_date = _parse_date(closes_from)
    closes_to_date = _parse_date(closes_to)
    min_funding_value = _parse_float(min_funding)
    max_funding_value = _parse_float(max_funding)
    selected_sources = normalise_source_filter(source)

    grouped = apply_filters(
        db,
        keyword,
        selected_sources,
        opens_from_date,
        closes_from_date,
        closes_to_date,
        sector_or_niche,
        min_funding_value,
        max_funding_value,
    )

    return templates.TemplateResponse(
        "_opportunity_list.html",
        {
            "request": request,
            "grouped": grouped,
        },
    )


@app.get("/opportunities/{opportunity_id}/details", response_class=HTMLResponse)
def opportunity_details(
    opportunity_id: str,
    request: Request,
    expanded: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    if not expanded:
        return HTMLResponse(content="")

    opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if not opportunity:
        return HTMLResponse(content="", status_code=404)

    return templates.TemplateResponse(
        "_opportunity_details.html",
        {
            "request": request,
            "opportunity": opportunity,
            "source_reference": source_reference(opportunity),
        },
    )


@app.get("/api/ingest")
def run_cloud_ingest(request: Request):
    require_cron_secret(request)

    timeout_seconds = _ingest_timeout_seconds()
    started = time.monotonic()
    deadline = started + timeout_seconds
    try:
        results = ingest_all.run(deadline=deadline)
    except Exception as exc:
        elapsed = time.monotonic() - started
        logger.exception("Cloud ingest failed after %.1fs.", elapsed)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "elapsed_seconds": round(elapsed, 1),
                "error": str(exc),
            },
        ) from exc

    elapsed = time.monotonic() - started
    if elapsed > timeout_seconds:
        logger.error("Cloud ingest exceeded %.1fs budget; elapsed %.1fs.", timeout_seconds, elapsed)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "elapsed_seconds": round(elapsed, 1),
                "error": "Ingest exceeded configured runtime budget.",
            },
        )

    logger.info("Cloud ingest completed in %.1fs: %s", elapsed, results)
    return {
        "success": True,
        "elapsed_seconds": round(elapsed, 1),
        "results": results,
    }


@app.get("/api/test-enrichment")
def test_enrichment(request: Request, db: Session = Depends(get_db)):
    require_cron_secret(request)

    row = (
        db.query(Opportunity)
        .filter(
            Opportunity.source.in_(["innovate_uk", "Innovate UK"]),
            Opportunity.description == Opportunity.summary,
        )
        .order_by(Opportunity.title)
        .first()
    )

    if not row:
        return {
            "competition_id": None,
            "title": None,
            "detail_url": None,
            "success": False,
            "description_length": 0,
            "funding_min": None,
            "funding_max": None,
            "error": "No unenriched Innovate UK records found.",
        }

    url = ingest_innovateuk.detail_url(row.id)
    response = {
        "competition_id": row.id,
        "title": row.title,
        "detail_url": url,
        "success": False,
        "description_length": 0,
        "funding_min": None,
        "funding_max": None,
        "error": None,
    }

    if not url:
        response["error"] = f"Cannot build detail URL for record id {row.id!r}."
        return response

    try:
        detail = ingest_innovateuk.parse_detail_page(
            ingest_innovateuk.fetch_detail_page(row.id),
            source_url=url,
        )
        description = detail.get("description") or ""
        response.update(
            {
                "success": True,
                "description_length": len(description),
                "funding_min": detail.get("funding_min"),
                "funding_max": detail.get("funding_max"),
            }
        )
    except Exception as exc:
        response["error"] = str(exc)

    return response
