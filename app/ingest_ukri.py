from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from .database import SessionLocal, engine
from .models import Opportunity
from .schema import ensure_database_schema

BASE = "https://www.ukri.org"
START_URL = (
    f"{BASE}/opportunity/"
    "?filter_status%5B0%5D=open"
    "&filter_status%5B1%5D=upcoming"
    "&filter_submitted=true"
    "&filter_order=publication_date"
)
SOURCE = "ukri"
HEADERS = {
    "User-Agent": "find-a-grant-ingester/0.1",
}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def fetch(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def opportunity_id(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else urlparse(url).netloc
    return f"ukri:{slug}"


def parse_date_value(cell: Optional[Tag]) -> Optional[date]:
    if not cell:
        return None

    time_el = cell.select_one("time[datetime]")
    if time_el:
        raw = (time_el.get("datetime") or "").split("T", 1)[0]
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass

    text = clean_text(cell.get_text(" ", strip=True))
    if not text or "to be confirmed" in text.lower() or "no closing date" in text.lower():
        return None

    match = re.search(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%d %B %Y").date()
    except ValueError:
        return None


def money_value(raw: str) -> Optional[float]:
    match = re.search(r"(?:£|GBP\s*)([\d,]+(?:\.\d+)?)\s*(million|m|k)?", raw, re.I)
    if not match:
        return None

    value = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    if suffix in {"million", "m"}:
        value *= 1_000_000
    elif suffix == "k":
        value *= 1_000
    return value


def money_values(raw: str) -> list[float]:
    values = []
    for match in re.finditer(r"(?:£|GBP\s*)([\d,]+(?:\.\d+)?)\s*(million|m|k)?", raw, re.I):
        value = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        if suffix in {"million", "m"}:
            value *= 1_000_000
        elif suffix == "k":
            value *= 1_000
        values.append(value)
    return values


def status_from_dates(opened: Optional[date], closes: Optional[date]) -> str:
    today = date.today()
    if opened and opened > today:
        return "upcoming"
    if closes and closes < today:
        return "expired"
    if not closes:
        return "rolling"
    return "open"


def summary_fields(scope: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in scope.select(".opportunity__summary .govuk-table__row"):
        label_el = row.select_one("dt")
        value_el = row.select_one("dd")
        if not label_el or not value_el:
            continue
        label = clean_text(label_el.get_text(" ", strip=True)).rstrip(":").lower()
        fields[label] = clean_text(value_el.get_text(" ", strip=True))
    return fields


def summary_date(scope: Tag, label: str) -> Optional[date]:
    for row in scope.select(".opportunity__summary .govuk-table__row"):
        label_el = row.select_one("dt")
        value_el = row.select_one("dd")
        if not label_el or not value_el:
            continue
        if clean_text(label_el.get_text(" ", strip=True)).rstrip(":").lower() == label:
            return parse_date_value(value_el)
    return None


def funding_from_fields(fields: dict[str, str]) -> tuple[Optional[float], Optional[float], list[str]]:
    notes = []
    funding_min = None
    funding_max = None

    range_values = money_values(fields.get("award range", ""))
    if range_values:
        funding_min = min(range_values)
        funding_max = max(range_values)
        notes.append(f"Award range: {fields['award range']}")

    min_value = money_value(fields.get("minimum award", ""))
    if min_value is not None:
        funding_min = min_value
        notes.append(f"Minimum award: {fields['minimum award']}")

    max_value = money_value(fields.get("maximum award", ""))
    if max_value is not None:
        funding_max = max_value
        notes.append(f"Maximum award: {fields['maximum award']}")

    total_value = money_value(fields.get("total fund", ""))
    if total_value is not None:
        funding_max = funding_max if funding_max is not None else total_value
        notes.append(f"Total fund: {fields['total fund']}")

    return funding_min, funding_max, notes


def is_innovate_only(fields: dict[str, str]) -> bool:
    funders = clean_text(fields.get("funders", ""))
    if not funders:
        return False
    funder_names = [clean_text(value) for value in funders.split(",") if clean_text(value)]
    return bool(funder_names) and all(value.lower() == "innovate uk" for value in funder_names)


def normalise_item(
    *,
    title: str,
    detail_url: str,
    summary: str,
    fields: dict[str, str],
    opened: Optional[date],
    closes: Optional[date],
    description: str,
    application_url: Optional[str],
) -> Optional[dict]:
    if is_innovate_only(fields):
        return None

    funding_min, funding_max, funding_notes = funding_from_fields(fields)
    funders = fields.get("funders")
    funding_type = fields.get("funding type")
    status = fields.get("opportunity status")

    description_parts = []
    if funders:
        description_parts.append(f"Funders: {funders}")
    if funding_type:
        description_parts.append(f"Funding type: {funding_type}")
    description_parts.extend(funding_notes)
    description_parts.append(f"Source page: {detail_url}")
    if description:
        description_parts.append(description)
    elif summary:
        description_parts.append(summary)

    return {
        "id": opportunity_id(detail_url),
        "source": SOURCE,
        "title": title,
        "url": application_url or detail_url,
        "source_url": detail_url,
        "opened_date": opened,
        "closes_date": closes,
        "funding_min": funding_min,
        "funding_max": funding_max,
        "funding_currency": "GBP" if funding_min is not None or funding_max is not None else None,
        "funding_min_native": funding_min,
        "funding_max_native": funding_max,
        "exchange_rate": 1.0 if funding_min is not None or funding_max is not None else None,
        "exchange_rate_date": date.today() if funding_min is not None or funding_max is not None else None,
        "sector_tags": funders,
        "niche_tags": ", ".join(value for value in [funding_type, status] if value) or None,
        "summary": summary,
        "description": "\n\n".join(part for part in description_parts if part) or title,
    }


def parse_listing_page(html: str, page_url: str = START_URL) -> tuple[list[dict], Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for card in soup.select("div.opportunity"):
        link = card.select_one("a.ukri-funding-opp__link[href]")
        if not link:
            continue
        detail_url = urljoin(page_url, link["href"])
        title = clean_text(link.get_text(" ", strip=True))
        summary_el = card.select_one(".entry-content")
        summary = clean_text(summary_el.get_text(" ", strip=True)) if summary_el else ""
        fields = summary_fields(card)
        opened = summary_date(card, "opening date")
        closes = summary_date(card, "closing date")
        item = normalise_item(
            title=title,
            detail_url=detail_url,
            summary=summary,
            fields=fields,
            opened=opened,
            closes=closes,
            description=summary,
            application_url=None,
        )
        if item:
            items.append(item)

    next_link = soup.select_one("a.next.page-numbers[href]")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return items, next_url


def parse_detail_page(html: str, source_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.main-area__page-title")
    title = clean_text(title_el.get_text(" ", strip=True).replace("Funding opportunity:", "")) if title_el else ""
    fields = summary_fields(soup)
    opened = summary_date(soup, "opening date")
    closes = summary_date(soup, "closing date")

    description_el = soup.select_one(".single-opportunity__entry-content")
    description = clean_text(description_el.get_text("\n", strip=True)) if description_el else ""
    application_link = soup.select_one("a#analytics-start-application[href]")
    application_url = urljoin(source_url, application_link["href"]) if application_link else None

    return {
        "title": title,
        "fields": fields,
        "opened_date": opened,
        "closes_date": closes,
        "description": description,
        "application_url": application_url,
    }


def enrich_item(item: dict) -> dict:
    try:
        detail = parse_detail_page(fetch(item["source_url"]), item["source_url"])
    except requests.RequestException:
        return item

    fields = {**summary_fields_from_item(item), **detail.get("fields", {})}
    return normalise_item(
        title=detail.get("title") or item["title"],
        detail_url=item["source_url"],
        summary=item.get("summary") or "",
        fields=fields,
        opened=detail.get("opened_date") or item.get("opened_date"),
        closes=detail.get("closes_date") or item.get("closes_date"),
        description=detail.get("description") or item.get("description") or "",
        application_url=detail.get("application_url"),
    ) or item


def summary_fields_from_item(item: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    if item.get("sector_tags"):
        fields["funders"] = item["sector_tags"]
    if item.get("niche_tags"):
        fields["funding type"] = item["niche_tags"].split(",", 1)[0]
    return fields


def crawl(max_pages: int = 20) -> list[dict]:
    page_url: Optional[str] = START_URL
    pages_seen: set[str] = set()
    items: list[dict] = []
    seen_ids: set[str] = set()

    while page_url and page_url not in pages_seen and len(pages_seen) < max_pages:
        pages_seen.add(page_url)
        page_items, next_url = parse_listing_page(fetch(page_url), page_url)
        if not page_items:
            break
        for item in page_items:
            if item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            items.append(enrich_item(item))
        page_url = next_url

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
    if not items:
        return 0
    return upsert(items, mark_stale=True)


if __name__ == "__main__":
    n = run()
    print(f"Saved {n} records")
