"""Eureka Network ingest.

Eureka's site is Cloudflare-protected with no public API, so it cannot be scraped
by the automated pipeline. Instead this module reads a curated JSON dataset
(`app/data/eureka_calls.json`), pulled from the logged-in Eureka site and committed
to the repo, and upserts it like any other source. Only UK-eligible calls are
included (the UK must be a listed participating country). Refresh by re-pulling the
site and updating the JSON; the capture date is shown on every card.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from .database import SessionLocal, engine
from .eligibility import ANY
from .geography import UK_WIDE
from .models import Opportunity
from .schema import ensure_database_schema

SOURCE = "eureka"
NOT_FUNDING_SOURCE = "eureka_not_funding"
DATA_FILE = Path(__file__).parent / "data" / "eureka_calls.json"


def parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def status_from_dates(opened: Optional[date], closes: Optional[date]) -> str:
    today = date.today()
    if opened and opened > today:
        return "upcoming"
    if closes and closes < today:
        return "expired"
    if not closes:
        return "rolling"
    return "open"


def slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def load_dataset(path: Path = DATA_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_description(call: dict, captured_at: str) -> str:
    parts = [call["summary"], f"Programme: {call['programme']}. The United Kingdom is a participating country."]
    if call.get("funding_eur"):
        parts.append(f"Funding: lump sum of EUR {call['funding_eur']:,} per project.")
    else:
        parts.append("Funding: set nationally by each participating country; no single call-level amount is published.")
    parts.append(f"Eureka data captured on {captured_at}. UK participation in Eureka is administered by Innovate UK.")
    parts.append(f"More details: {call['url']}")
    return "\n\n".join(parts)


def normalise(call: dict, captured_at: str) -> dict:
    funding = call.get("funding_eur")
    return {
        "id": f"eureka:{slug(call['url'])}",
        "source": NOT_FUNDING_SOURCE if call.get("not_funding") else SOURCE,
        "title": call["title"],
        "url": call["url"],
        "opened_date": parse_iso(call.get("opened")),
        "closes_date": parse_iso(call.get("closes")),
        "funding_min_native": float(funding) if funding else None,
        "funding_max_native": float(funding) if funding else None,
        "funding_currency": "EUR" if funding else None,
        "niche_tags": call["programme"],
        "geographic_scope": UK_WIDE,
        "eligible_applicants": ANY,
        "summary": call["summary"],
        "description": build_description(call, captured_at),
    }


def upsert(items: list[dict], mark_stale: bool = True) -> int:
    ensure_database_schema(engine)
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    changed = 0
    seen_ids: set[str] = set()
    try:
        for item in items:
            status = status_from_dates(item["opened_date"], item["closes_date"])
            if status == "expired":
                continue
            seen_ids.add(item["id"])
            existing = db.query(Opportunity).filter(Opportunity.id == item["id"]).one_or_none()
            target = existing or Opportunity(id=item["id"])
            target.source = item["source"]
            target.title = item["title"]
            target.url = item["url"]
            target.opened_date = item["opened_date"]
            target.closes_date = item["closes_date"]
            target.funding_min_native = item["funding_min_native"]
            target.funding_max_native = item["funding_max_native"]
            target.funding_currency = item["funding_currency"]
            target.niche_tags = item["niche_tags"]
            target.geographic_scope = item["geographic_scope"]
            target.eligible_applicants = item["eligible_applicants"]
            target.summary = item["summary"]
            target.description = item["description"]
            target.status = status
            target.last_seen = now
            if not existing:
                db.add(target)
            changed += 1

        if mark_stale:
            stale = db.query(Opportunity).filter(
                Opportunity.source.in_([SOURCE, NOT_FUNDING_SOURCE]),
                Opportunity.status != "inactive",
            )
            if seen_ids:
                stale = stale.filter(Opportunity.id.notin_(seen_ids))
            for row in stale.all():
                row.status = "inactive"
                row.last_seen = now
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run() -> int:
    data = load_dataset()
    captured_at = data.get("captured_at", "")
    items = [normalise(call, captured_at) for call in data.get("calls", [])]
    return upsert(items, mark_stale=True)


if __name__ == "__main__":
    print(f"Saved {run()} records")
