from __future__ import annotations

import inspect
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import or_

from . import ingest_horizon_europe, ingest_innovateuk, ingest_iuk_business_connect, ingest_konfer, ingest_ukri
from .database import SessionLocal, engine
from .models import AppMetadata, Opportunity
from .schema import ensure_database_schema


SOURCE_PRIORITY = {
    "iuk_business_connect": 0,
    "innovate_uk": 1,
    "Innovate UK": 1,
    "ukri": 1,
    "horizon_europe": 0,
    "konfer": 2,
    "Konfer": 2,
}

DEFAULT_MIN_REMAINING_SECONDS = 30.0
LAST_SUCCESSFUL_INGEST_KEY = "last_successful_ingest_at"
LAST_INGEST_RUN_KEY = "last_ingest_run"
SOURCE_STATUS_PREFIX = "source_status:"
SOURCE_INGEST_STEPS = (
    "innovate_uk",
    "iuk_business_connect",
    "ukri",
    "horizon_europe",
    "konfer",
)
logger = logging.getLogger(__name__)


class IngestDeadlineExceeded(RuntimeError):
    pass


def _normalise_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value.strip())
    if not parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc.lower()}{path.lower()}"


def _competition_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"/competition/([^/]+)/", value)
    return match.group(1) if match else None


def _slug(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    parts = [part for part in parsed.path.rstrip("/").split("/") if part]
    if len(parts) >= 2 and parts[-2] == "opportunities":
        return parts[-1].lower()
    return None


def _is_generic_business_connect_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.netloc.lower().endswith("iuk-business-connect.org.uk") and parsed.path.startswith("/programme/")


def _title_key(value: str | None) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def canonical_keys(opportunity: Opportunity) -> set[str]:
    keys = set()
    url = opportunity.url or ""
    description = opportunity.description or ""
    combined = f"{url}\n{description}"

    for candidate in re.findall(r"https?://[^\s)]+", combined):
        if not _is_generic_business_connect_url(candidate):
            normalised = _normalise_url(candidate)
            if normalised:
                keys.add(f"url:{normalised}")
        competition_id = _competition_id(candidate)
        if competition_id:
            keys.add(f"ifs:{competition_id}")
        slug = _slug(candidate)
        if slug:
            keys.add(f"iukbc:{slug}")

    competition_id = _competition_id(url)
    if competition_id:
        keys.add(f"ifs:{competition_id}")

    slug = _slug(url)
    if slug:
        keys.add(f"iukbc:{slug}")

    if opportunity.title and opportunity.closes_date:
        keys.add(f"title-close:{_title_key(opportunity.title)}:{opportunity.closes_date.isoformat()}")

    return keys


def status_from_dates(opened: date | None, closes: date | None) -> str:
    today = date.today()
    if opened and opened > today:
        return "upcoming"
    if not closes:
        return "rolling"
    return "open"


def mark_expired_inactive() -> int:
    db = SessionLocal()
    try:
        rows = (
            db.query(Opportunity)
            .filter(
                Opportunity.closes_date.isnot(None),
                Opportunity.closes_date < date.today(),
                Opportunity.status != "inactive",
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "inactive"
            row.last_seen = now
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_duplicates_inactive() -> int:
    db = SessionLocal()
    try:
        rows = db.query(Opportunity).filter(
            or_(
                Opportunity.closes_date.is_(None),
                Opportunity.closes_date >= date.today(),
            ),
        ).all()

        owner_by_key: dict[str, Opportunity] = {}
        duplicates: set[str] = set()
        preferred_with_duplicates: set[str] = set()

        for row in sorted(rows, key=lambda item: (SOURCE_PRIORITY.get(item.source, 99), item.title)):
            row_keys = canonical_keys(row)
            owners = {owner_by_key[key] for key in row_keys if key in owner_by_key}
            if owners:
                duplicates.add(row.id)
                preferred_with_duplicates.update(owner.id for owner in owners)
                continue
            for key in row_keys:
                owner_by_key[key] = row

        now = datetime.now(timezone.utc)
        changed = 0
        for row in rows:
            if row.id in duplicates:
                if row.status != "inactive":
                    row.status = "inactive"
                    row.last_seen = now
                    changed += 1
            elif row.id in preferred_with_duplicates and row.status == "inactive":
                row.status = status_from_dates(row.opened_date, row.closes_date)
                row.last_seen = now
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_successful_ingest(timestamp: datetime | None = None) -> None:
    db = SessionLocal()
    try:
        value = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        row = db.query(AppMetadata).filter(AppMetadata.key == LAST_SUCCESSFUL_INGEST_KEY).one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppMetadata(key=LAST_SUCCESSFUL_INGEST_KEY, value=value))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _metadata_upsert(db, key: str, value: str) -> None:
    row = db.query(AppMetadata).filter(AppMetadata.key == key).one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppMetadata(key=key, value=value))


def _load_json_metadata(db, key: str) -> dict[str, Any]:
    row = db.query(AppMetadata).filter(AppMetadata.key == key).one_or_none()
    if not row:
        return {}
    try:
        value = json.loads(row.value)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON metadata for %s: %r", key, row.value)
        return {}
    return value if isinstance(value, dict) else {}


def record_source_ingest_status(
    source: str,
    status: str,
    *,
    count: int | None = None,
    error: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    checked_at = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    key = f"{SOURCE_STATUS_PREFIX}{source}"
    db = SessionLocal()
    try:
        previous = _load_json_metadata(db, key)
        data: dict[str, Any] = {
            "source": source,
            "status": status,
            "checked_at": checked_at,
            "last_successful_at": previous.get("last_successful_at"),
        }
        if count is not None:
            data["count"] = count
        if error:
            data["error"] = error
        if status == "success":
            data["last_successful_at"] = checked_at
        _metadata_upsert(db, key, json.dumps(data, sort_keys=True))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_ingest_run_status(status: str, results: dict[str, Any], timestamp: datetime | None = None) -> None:
    checked_at = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    db = SessionLocal()
    try:
        _metadata_upsert(
            db,
            LAST_INGEST_RUN_KEY,
            json.dumps(
                {
                    "status": status,
                    "checked_at": checked_at,
                    "results": results,
                },
                sort_keys=True,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _check_deadline(deadline: float | None, label: str, min_remaining_seconds: float) -> None:
    if deadline is None:
        return
    remaining = deadline - time.monotonic()
    if remaining < min_remaining_seconds:
        raise IngestDeadlineExceeded(
            f"Stopping before {label}: only {remaining:.1f}s remain in the ingest budget."
        )


def _run_step(
    results: dict[str, Any],
    name: str,
    action,
    deadline: float | None,
    min_remaining_seconds: float,
) -> None:
    _check_deadline(deadline, name, min_remaining_seconds)
    results[name] = action()
    _check_deadline(deadline, f"continuing after {name}", min_remaining_seconds)


def _run_source_step(
    results: dict[str, Any],
    failures: dict[str, str],
    name: str,
    action: Callable[[], int],
    deadline: float | None,
    min_remaining_seconds: float,
) -> None:
    try:
        _run_step(results, name, action, deadline, min_remaining_seconds)
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        failures[name] = message
        results[name] = {"status": "failed", "error": message}
        logger.exception("Ingest source %s failed; continuing with remaining sources.", name)
        record_source_ingest_status(name, "failed", error=message)
    else:
        record_source_ingest_status(name, "success", count=results[name])


def _run_innovate_uk(deadline: float | None) -> int:
    if "deadline" in inspect.signature(ingest_innovateuk.run).parameters:
        return ingest_innovateuk.run(deadline=deadline)
    return ingest_innovateuk.run()


def run(deadline: float | None = None, min_remaining_seconds: float = DEFAULT_MIN_REMAINING_SECONDS) -> dict[str, Any]:
    ensure_database_schema(engine)
    results: dict[str, Any] = {}
    failures: dict[str, str] = {}
    _run_step(results, "expired_marked_inactive_before", mark_expired_inactive, deadline, min_remaining_seconds)
    _run_source_step(results, failures, "innovate_uk", lambda: _run_innovate_uk(deadline), deadline, min_remaining_seconds)
    _run_source_step(results, failures, "iuk_business_connect", ingest_iuk_business_connect.run, deadline, min_remaining_seconds)
    _run_source_step(results, failures, "ukri", ingest_ukri.run, deadline, min_remaining_seconds)
    _run_source_step(results, failures, "horizon_europe", ingest_horizon_europe.run, deadline, min_remaining_seconds)
    _run_source_step(results, failures, "konfer", ingest_konfer.run, deadline, min_remaining_seconds)
    _run_step(results, "duplicates_marked_inactive", mark_duplicates_inactive, deadline, min_remaining_seconds)
    _run_step(results, "expired_marked_inactive_after", mark_expired_inactive, deadline, min_remaining_seconds)
    overall_status = "failed" if len(failures) == len(SOURCE_INGEST_STEPS) else "partial_success" if failures else "success"
    results["overall_status"] = overall_status
    if failures:
        results["source_failures"] = failures
    _check_deadline(deadline, "recording ingest status", min_remaining_seconds)
    record_ingest_run_status(overall_status, results)
    if overall_status == "success":
        record_successful_ingest()
    return results


if __name__ == "__main__":
    results = run()
    for source, count in results.items():
        print(f"{source}: {count}")
