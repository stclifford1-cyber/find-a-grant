"""EIT (European Institute of Innovation and Technology) ingest.

EIT is Horizon Europe Pillar 3. It does not run a single grant; it funds through
sector KICs (Knowledge & Innovation Communities), each running its own open calls
on its own site with no shared public API. So, like the Eureka source, this module
reads a curated JSON dataset (`app/data/eit_calls.json`), pulled from the EIT
opportunities hub and the individual KIC sites and committed to the repo, and
upserts it like any other source.

Only UK-eligible calls are included (confirmed against each call's official manual
and the EC Horizon Europe participating-countries list). EIT calls published on the
EU Funding & Tenders portal (identifiers beginning ``HORIZON-EIT-``) are excluded
because the Horizon Europe source already ingests them; see the dataset ``note``.
Refresh by re-pulling the sites and updating the JSON; the capture date is shown on
every card.
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

SOURCE = "eit"
NOT_FUNDING_SOURCE = "eit_not_funding"
DATA_FILE = Path(__file__).parent / "data" / "eit_calls.json"


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
    parts = [call["summary"], f"Delivered by {call['kic']}, a Horizon Europe (Pillar 3) EIT Knowledge & Innovation Community."]
    funding_max = call.get("funding_max_eur")
    funding_min = call.get("funding_min_eur")
    if funding_max and funding_min == funding_max:
        parts.append(f"Funding: lump sum of EUR {funding_max:,} per project.")
    elif funding_max:
        parts.append(f"Funding: up to EUR {funding_max:,} per project.")
    else:
        parts.append("Funding: set per call route rather than as a single published amount; see the call page.")
    parts.append(f"EIT data captured on {captured_at}. UK participation in Horizon Europe is administered through UKRI/Innovate UK.")
    parts.append(f"More details: {call['url']}")
    return "\n\n".join(parts)


def normalise(call: dict, captured_at: str) -> dict:
    funding_min = call.get("funding_min_eur")
    funding_max = call.get("funding_max_eur")
    has_funding = funding_min is not None or funding_max is not None
    return {
        "id": f"eit:{slug(call['url'])}",
        "source": NOT_FUNDING_SOURCE if call.get("not_funding") else SOURCE,
        "title": call["title"],
        "url": call["url"],
        "opened_date": parse_iso(call.get("opened")),
        "closes_date": parse_iso(call.get("closes")),
        "funding_min_native": float(funding_min) if funding_min is not None else None,
        "funding_max_native": float(funding_max) if funding_max is not None else None,
        "funding_currency": "EUR" if has_funding else None,
        "niche_tags": call["kic"],
        "geographic_scope": UK_WIDE,
        "eligible_applicants": call.get("eligible_applicants") or ANY,
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
