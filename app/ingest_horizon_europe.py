from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .database import SessionLocal, engine
from .geography import UK_WIDE
from .models import Opportunity
from .schema import ensure_database_schema

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
PORTAL_BASE = "https://ec.europa.eu/info/funding-tenders/opportunities/portal"
ECB_DAILY_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
SOURCE = "horizon_europe"
FRAMEWORK_PROGRAMME = "43108390"
PROGRAMME_PERIOD = "2021 - 2027"
OPEN_STATUS = "31094502"
FORTHCOMING_STATUS = "31094501"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "find-a-grant-ingester/0.1",
}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "").strip())


def first(values: object) -> str | None:
    if isinstance(values, list) and values:
        return str(values[0])
    if isinstance(values, str):
        return values
    return None


def parse_api_datetime(value: str | None) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
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


def horizon_query() -> dict:
    return {
        "bool": {
            "must": [
                {"terms": {"type": ["1", "2", "8"]}},
                {"terms": {"status": [FORTHCOMING_STATUS, OPEN_STATUS]}},
                {"term": {"programmePeriod": PROGRAMME_PERIOD}},
                {"terms": {"frameworkProgramme": [FRAMEWORK_PROGRAMME]}},
            ]
        }
    }


def search_page(page_number: int, page_size: int = 50) -> dict:
    files = {
        "query": (None, json.dumps(horizon_query()), "application/json"),
        "languages": (None, json.dumps(["en"]), "application/json"),
        "sort": (None, json.dumps({"order": "ASC", "field": "deadlineDate"}), "application/json"),
    }
    response = requests.post(
        API,
        params={"apiKey": "SEDIA", "text": "***", "pageSize": str(page_size), "pageNumber": str(page_number)},
        files=files,
        headers=HEADERS,
        timeout=40,
    )
    response.raise_for_status()
    return response.json()


def topic_details(identifier: str) -> dict:
    response = requests.post(
        API,
        params={"apiKey": "SEDIA", "text": f'"{identifier}"', "pageSize": "5", "pageNumber": "1"},
        headers=HEADERS,
        timeout=40,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    for result in results:
        metadata = result.get("metadata", {})
        if first(metadata.get("identifier")) == identifier:
            return result
    return results[0] if results else {}


def opportunity_id(identifier: str) -> str:
    return f"horizon:{identifier.lower()}"


def portal_url(identifier: str, metadata: dict) -> str:
    result_url = first(metadata.get("url")) or ""
    record_type = first(metadata.get("type"))
    if record_type == "8" or "/competitive-calls-cs/" in result_url:
        query = urlencode(
            {
                "order": "DESC",
                "pageNumber": "1",
                "pageSize": "50",
                "sortBy": "relevance",
                "keywords": identifier,
                "isExactMatch": "true",
                "status": f"{FORTHCOMING_STATUS},{OPEN_STATUS}",
                "programmePeriod": PROGRAMME_PERIOD,
                "frameworkProgramme": FRAMEWORK_PROGRAMME,
                "type": "8",
            }
        )
        return f"{PORTAL_BASE}/screen/opportunities/calls-for-proposals?{query}"
    return f"{PORTAL_BASE}/screen/opportunities/topic-details/{identifier}"


def description_text(metadata: dict) -> str:
    description_html = first(metadata.get("descriptionByte")) or ""
    if not description_html:
        return ""
    soup = BeautifulSoup(description_html, "html.parser")
    return clean_text(soup.get_text("\n", strip=True))


def deadline_dates(metadata: dict) -> list[date]:
    dates = []
    for value in metadata.get("deadlineDate") or []:
        parsed = parse_api_datetime(str(value))
        if parsed:
            dates.append(parsed)
    return sorted(set(dates))


def current_close_date(metadata: dict) -> Optional[date]:
    dates = deadline_dates(metadata)
    today = date.today()
    future = [value for value in dates if value >= today]
    if future:
        return future[0]
    return dates[-1] if dates else None


def parse_budget_overview(metadata: dict) -> tuple[Optional[float], Optional[float], list[str]]:
    raw_values = metadata.get("budgetOverview") or []
    min_values: list[float] = []
    max_values: list[float] = []
    notes: list[str] = []

    for raw in raw_values:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue

        action_map = payload.get("budgetTopicActionMap") or {}
        for actions in action_map.values():
            for action in actions or []:
                if action.get("minContribution") is not None and float(action["minContribution"]) > 0:
                    min_values.append(float(action["minContribution"]))
                if action.get("maxContribution") is not None:
                    max_values.append(float(action["maxContribution"]))

                year_map = action.get("budgetYearMap") or {}
                total = sum(float(value) for value in year_map.values() if str(value).replace(".", "", 1).isdigit())
                if total:
                    notes.append(f"Topic budget: EUR {total:,.0f}")
                if action.get("expectedGrants"):
                    notes.append(f"Expected grants: {action['expectedGrants']}")

    funding_min = min(min_values) if min_values else None
    funding_max = max(max_values) if max_values else None
    return funding_min, funding_max, notes


def fetch_eur_to_gbp_rate() -> tuple[Optional[float], Optional[date]]:
    response = requests.get(
        ECB_DAILY_XML,
        headers={"Accept": "application/xml", "User-Agent": "find-a-grant-ingester/0.1"},
        timeout=20,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)

    time_value = None
    for element in root.iter():
        if element.attrib.get("time"):
            time_value = element.attrib["time"]
        if element.attrib.get("currency") == "GBP":
            return float(element.attrib["rate"]), parse_api_datetime(time_value)
    return None, parse_api_datetime(time_value)


def normalise_result(result: dict, eur_to_gbp: Optional[float], rate_date: Optional[date]) -> Optional[dict]:
    metadata = result.get("metadata", {})
    identifier = first(metadata.get("identifier")) or first(metadata.get("callIdentifier"))
    if not identifier:
        return None

    title = clean_text(first(metadata.get("title")) or result.get("summary") or result.get("content") or identifier)
    opened = parse_api_datetime(first(metadata.get("startDate")))
    closes = current_close_date(metadata)
    funding_min_eur, funding_max_eur, budget_notes = parse_budget_overview(metadata)
    funding_min_gbp = funding_min_eur * eur_to_gbp if funding_min_eur is not None and eur_to_gbp else None
    funding_max_gbp = funding_max_eur * eur_to_gbp if funding_max_eur is not None and eur_to_gbp else None

    action_type = clean_text(first(metadata.get("typesOfAction")))
    call_title = clean_text(first(metadata.get("callTitle")))
    keywords = [clean_text(value) for value in metadata.get("keywords") or [] if clean_text(value)]
    description_parts = []
    if action_type:
        description_parts.append(f"Action type: {action_type}")
    if call_title:
        description_parts.append(f"Call: {call_title}")
    description_parts.extend(budget_notes)

    description = description_text(metadata)
    if description:
        description_parts.append(description)

    url = portal_url(identifier, metadata)
    description_parts.append(f"Source page: {url}")

    summary = clean_text(result.get("summary") or description[:320] or title)
    return {
        "id": opportunity_id(identifier),
        "source": SOURCE,
        "title": title,
        "url": url,
        "opened_date": opened,
        "closes_date": closes,
        "funding_min": funding_min_gbp,
        "funding_max": funding_max_gbp,
        "funding_currency": "EUR" if funding_min_eur is not None or funding_max_eur is not None else None,
        "funding_min_native": funding_min_eur,
        "funding_max_native": funding_max_eur,
        "exchange_rate": eur_to_gbp,
        "exchange_rate_date": rate_date,
        "sector_tags": ", ".join(keywords[:8]) if keywords else None,
        "niche_tags": ", ".join(value for value in [action_type, call_title] if value) or None,
        "geographic_scope": UK_WIDE,
        "summary": summary,
        "description": "\n\n".join(part for part in description_parts if part) or summary,
    }


def merge_listing_and_detail(listing: dict, detail: dict) -> dict:
    merged = {**(detail or {}), **listing}
    detail_metadata = (detail or {}).get("metadata", {})
    listing_metadata = listing.get("metadata", {})
    metadata = {**detail_metadata, **listing_metadata}

    for key in ("descriptionByte", "budgetOverview", "typesOfAction", "callTitle", "keywords"):
        if not metadata.get(key) and detail_metadata.get(key):
            metadata[key] = detail_metadata[key]

    merged["metadata"] = metadata
    return merged


def crawl(max_pages: int = 20, page_size: int = 50) -> list[dict]:
    try:
        eur_to_gbp, rate_date = fetch_eur_to_gbp_rate()
    except requests.RequestException:
        eur_to_gbp, rate_date = None, None

    items: list[dict] = []
    seen_ids: set[str] = set()
    detail_cache: dict[str, dict] = {}

    for page in range(1, max_pages + 1):
        payload = search_page(page, page_size=page_size)
        results = payload.get("results") or []
        if not results:
            break

        for result in results:
            metadata = result.get("metadata", {})
            identifier = first(metadata.get("identifier")) or first(metadata.get("callIdentifier"))
            if not identifier:
                continue
            item_id = opportunity_id(identifier)
            if item_id in seen_ids:
                continue

            if identifier not in detail_cache:
                detail_cache[identifier] = topic_details(identifier)
            detail = detail_cache[identifier]
            item = normalise_result(merge_listing_and_detail(result, detail), eur_to_gbp, rate_date)
            if item:
                seen_ids.add(item_id)
                items.append(item)

        if len(results) < page_size:
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
                existing.funding_currency = item.get("funding_currency")
                existing.funding_min_native = item.get("funding_min_native")
                existing.funding_max_native = item.get("funding_max_native")
                existing.exchange_rate = item.get("exchange_rate")
                existing.exchange_rate_date = item.get("exchange_rate_date")
                existing.sector_tags = item.get("sector_tags")
                existing.niche_tags = item.get("niche_tags")
                existing.geographic_scope = item.get("geographic_scope") or UK_WIDE
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
                        funding_currency=item.get("funding_currency"),
                        funding_min_native=item.get("funding_min_native"),
                        funding_max_native=item.get("funding_max_native"),
                        exchange_rate=item.get("exchange_rate"),
                        exchange_rate_date=item.get("exchange_rate_date"),
                        sector_tags=item.get("sector_tags"),
                        niche_tags=item.get("niche_tags"),
                        geographic_scope=item.get("geographic_scope") or UK_WIDE,
                        summary=item.get("summary"),
                        description=item["description"],
                        status=status,
                        last_seen=now,
                    )
                )
            changed += 1

        if mark_stale:
            stale_query = db.query(Opportunity).filter(
                Opportunity.source == SOURCE,
                Opportunity.status != "inactive",
            )
            if seen_ids:
                stale_query = stale_query.filter(Opportunity.id.notin_(seen_ids))
            for stale in stale_query.all():
                stale.status = "inactive"
                stale.last_seen = now
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run(max_pages: int = 20) -> int:
    items = crawl(max_pages=max_pages)
    return upsert(items, mark_stale=True)


if __name__ == "__main__":
    n = run()
    print(f"Saved {n} records")
