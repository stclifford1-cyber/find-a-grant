from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .database import SessionLocal, engine
from .eligibility import classify_eligible_applicants
from .geography import classify_geographic_scope
from .models import AppMetadata, Opportunity
from .schema import ensure_database_schema

BASE = "https://konfer.online"
API = "https://api.konfer.online/api/search/fundingopportunities"
SOURCE = "konfer"
PAGE_SIZE = 90
LAST_KONFER_CHECK_KEY = "source_check:konfer"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": BASE,
    "Referer": f"{BASE}/funding",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}


def clean_text(value: str) -> str:
    text = BeautifulSoup(html.unescape(value or ""), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_konfer_date(value: str) -> Optional[date]:
    value = clean_text(value)
    if not value:
        return None

    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value, flags=re.I)
    for fmt in ("%b %d %Y", "%B %d %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
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


def funding_value(text: str) -> Optional[float]:
    matches = re.findall(r"(?:£|GBP\s*)([\d,]+(?:\.\d+)?)\s*(million|m|k)?", text or "", re.I)
    if not matches:
        return None

    values = []
    for amount, suffix in matches:
        value = float(amount.replace(",", ""))
        suffix = suffix.lower()
        if suffix in {"million", "m"}:
            value *= 1_000_000
        elif suffix == "k":
            value *= 1_000
        values.append(value)
    return max(values) if values else None


def is_business_connect_url(value: str | None) -> bool:
    if not value:
        return False
    return urlparse(value).netloc.lower().endswith("iuk-business-connect.org.uk")


def fetch_page(page: int = 1) -> dict:
    params = {
        "q": "",
        "page": page,
        "itemsRequired": PAGE_SIZE,
        "sortBy": "openDate",
    }
    response = requests.get(API, params=params, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return response.json()


def normalise_record(record: dict) -> dict:
    mongo_id = record.get("mongoId") or record.get("elasticSearchId")
    title = clean_text(record.get("title", ""))
    summary = clean_text(record.get("summary", ""))
    award = clean_text(record.get("award", ""))
    organisation = clean_text(record.get("organisation") or record.get("institutionName") or "")
    sector = clean_text(record.get("sector") or (record.get("konferCategory") or {}).get("category") or "")
    opened = parse_konfer_date(record.get("registrationStartDate") or record.get("datePublished") or "")
    closes = parse_konfer_date(record.get("registrationCloseDate") or "")
    funding_url = record.get("fundingUrl") or ""
    detail_url = f"{BASE}{record.get('url')}" if record.get("url", "").startswith("/") else record.get("url", "")
    url = funding_url or detail_url or f"{BASE}/fundings"

    description_parts = []
    if award:
        description_parts.append(f"Award: {award}")
    if organisation:
        description_parts.append(f"Organisation: {organisation}")
    if detail_url:
        description_parts.append(f"Konfer page: {detail_url}")
    if summary:
        description_parts.append(summary)

    amount = funding_value(award)
    return {
        "id": f"konfer:{mongo_id}",
        "source": SOURCE,
        "title": title,
        "url": url,
        "summary": summary,
        "description": "\n\n".join(description_parts) or summary or title,
        "opened_date": opened,
        "closes_date": closes,
        "funding_min": None,
        "funding_max": amount,
        "sector_tags": sector or None,
        "niche_tags": ", ".join(value for value in [organisation, "Grant Funding"] if value) or None,
    }


def crawl(max_pages: int = 20) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        data = fetch_page(page)
        results = data.get("results") or []
        if not results:
            break

        for record in results:
            if is_business_connect_url(record.get("fundingUrl")):
                continue
            item = normalise_record(record)
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)

        total = data.get("total") or 0
        if total and len(seen) >= total:
            break

    return items


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

            geographic_scope = item.get("geographic_scope") or classify_geographic_scope(item)
            eligible_applicants = item.get("eligible_applicants") or classify_eligible_applicants(item)
            seen_ids.add(item["id"])
            existing = db.query(Opportunity).filter(Opportunity.id == item["id"]).one_or_none()
            if existing:
                existing.source = item["source"]
                existing.title = item["title"]
                existing.url = item["url"]
                existing.opened_date = item["opened_date"]
                existing.closes_date = item["closes_date"]
                existing.funding_min = item.get("funding_min")
                existing.funding_max = item.get("funding_max")
                existing.sector_tags = item.get("sector_tags")
                existing.niche_tags = item.get("niche_tags")
                existing.geographic_scope = geographic_scope
                existing.eligible_applicants = eligible_applicants
                existing.summary = item.get("summary")
                existing.description = item["description"]
                existing.status = status
                existing.last_seen = now
            else:
                db.add(
                    Opportunity(
                        id=item["id"],
                        source=item["source"],
                        title=item["title"],
                        url=item["url"],
                        opened_date=item["opened_date"],
                        closes_date=item["closes_date"],
                        funding_min=item.get("funding_min"),
                        funding_max=item.get("funding_max"),
                        sector_tags=item.get("sector_tags"),
                        niche_tags=item.get("niche_tags"),
                        geographic_scope=geographic_scope,
                        eligible_applicants=eligible_applicants,
                        summary=item.get("summary"),
                        description=item["description"],
                        status=status,
                        last_seen=now,
                    )
                )
            changed += 1

        if mark_stale:
            stale_query = db.query(Opportunity).filter(
                Opportunity.source.in_([SOURCE, "Konfer"]),
                Opportunity.status != "inactive",
            )
            if seen_ids:
                stale_query = stale_query.filter(Opportunity.id.notin_(seen_ids))
            for stale in stale_query.all():
                stale.status = "inactive"
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def record_konfer_check(non_duplicate_records: int, timestamp: datetime | None = None) -> None:
    db = SessionLocal()
    try:
        value = json.dumps(
            {
                "checked_at": (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                "non_duplicate_records": non_duplicate_records,
            }
        )
        row = db.query(AppMetadata).filter(AppMetadata.key == LAST_KONFER_CHECK_KEY).one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppMetadata(key=LAST_KONFER_CHECK_KEY, value=value))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run(max_pages: int = 20) -> int:
    items = crawl(max_pages=max_pages)
    changed = upsert(items, mark_stale=True)
    record_konfer_check(len(items))
    return changed


if __name__ == "__main__":
    n = run()
    print(f"Changed {n} records")
