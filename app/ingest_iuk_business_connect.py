from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from .database import SessionLocal, engine
from .geography import classify_geographic_scope
from .models import Opportunity
from .schema import ensure_database_schema

BASE = "https://iuk-business-connect.org.uk"
START_URL = f"{BASE}/opportunities/"
SOURCE = "iuk_business_connect"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def parse_business_connect_date(text: str) -> Optional[date]:
    text = clean_text(text)
    match = re.search(r"\d{2}/\d{2}/\d{4}", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%d/%m/%Y").date()
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
    match = re.search(r"(?:\u00a3|GBP\s*)([\d,]+(?:\.\d+)?)\s*(million|m|k)?", text, re.I)
    if not match:
        return None

    value = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    if suffix in {"million", "m"}:
        value *= 1_000_000
    elif suffix == "k":
        value *= 1_000
    return value


def opportunity_id(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else urlparse(url).netloc
    return f"iukbc:{slug}"


def fetch(url: str) -> str:
    response = requests.get(
        url,
        timeout=25,
        headers={"User-Agent": "find-a-grant-ingester/0.1"},
    )
    response.raise_for_status()
    return response.text


def parse_listing_page(html: str, page_url: str = START_URL) -> tuple[list[dict], Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    results = soup.select_one("#search-filter-results-366") or soup
    for card in results.select('a[href*="/opportunities/"]'):
        title_el = card.select_one("h3")
        if not title_el:
            continue

        href = card.get("href", "").strip()
        if not href:
            continue

        detail_url = urljoin(page_url, href)
        title = clean_text(title_el.get_text(" ", strip=True))
        summary_el = card.select_one(".line-clamp-5")
        summary = clean_text(summary_el.get_text(" ", strip=True)) if summary_el else ""

        opened = None
        closes = None
        for paragraph in card.select("p"):
            text = paragraph.get_text(" ", strip=True)
            if "Opens:" in text:
                opened = parse_business_connect_date(text)
            elif "Closes:" in text:
                closes = parse_business_connect_date(text)

        items.append(
            {
                "id": opportunity_id(detail_url),
                "source": SOURCE,
                "title": title,
                "source_url": detail_url,
                "url": detail_url,
                "summary": summary,
                "description": summary,
                "opened_date": opened,
                "closes_date": closes,
                "funding_min": None,
                "funding_max": None,
                "sector_tags": None,
                "niche_tags": None,
            }
        )

    next_link = results.select_one('.pagination a[rel="next"][href], .pagination a.next[href]')
    if not next_link:
        for link in results.select(".pagination a[href]"):
            if "next" in clean_text(link.get_text(" ", strip=True)).lower():
                next_link = link
                break
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return items, next_url


def _section_block(soup: BeautifulSoup, heading: str) -> Optional[Tag]:
    for h5 in soup.select("h5"):
        if clean_text(h5.get_text(" ", strip=True)).lower() == heading.lower():
            return h5.find_parent("div")
    return None


def _section_text(soup: BeautifulSoup, heading: str) -> Optional[str]:
    block = _section_block(soup, heading)
    if not block:
        return None

    values = []
    for child in block.find_all("div", recursive=False):
        text = clean_text(child.get_text(" ", strip=True))
        if text and text.lower() != heading.lower():
            values.append(text)
    return ", ".join(values) or None


def _section_links(soup: BeautifulSoup, heading: str) -> list[str]:
    block = _section_block(soup, heading)
    if not block:
        return []
    return [clean_text(link.get_text(" ", strip=True)) for link in block.select("a") if clean_text(link.get_text(" ", strip=True))]


def _application_link(soup: BeautifulSoup) -> Optional[str]:
    for link in soup.select('a[href]'):
        text = clean_text(link.get_text(" ", strip=True)).lower()
        if "apply" in text or "register" in text:
            return urljoin(BASE, link["href"])
    return None


def parse_detail_page(html: str, source_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    opportunity_type = _section_text(soup, "Opportunity Type")
    organisation = _section_text(soup, "Organisation")
    sectors = _section_links(soup, "Sector") or ([clean_text(_section_text(soup, "Sector") or "")] if _section_text(soup, "Sector") else [])
    award = _section_text(soup, "Award")
    application_url = _application_link(soup)

    description_el = soup.select_one("article.single_opportunities .prose")
    if description_el:
        description = clean_text(description_el.get_text("\n", strip=True))
    else:
        meta_description = soup.select_one('meta[name="description"]')
        description = clean_text(meta_description.get("content", "")) if meta_description else ""

    niche_values = [value for value in [opportunity_type, organisation] if value]
    description_parts = []
    if award:
        description_parts.append(f"Award: {award}")
    if source_url:
        description_parts.append(f"Source page: {source_url}")
    if description:
        description_parts.append(description)

    amount = funding_value(award or "")
    funding_min = amount if amount and not re.search(r"\b(up to|maximum|share of)\b", award or "", re.I) else None

    return {
        "url": application_url or source_url,
        "application_url": application_url,
        "opportunity_type": opportunity_type,
        "organisation": organisation,
        "award": award,
        "description": "\n\n".join(description_parts) or "",
        "funding_min": funding_min,
        "funding_max": amount,
        "sector_tags": ", ".join(sectors) if sectors else None,
        "niche_tags": ", ".join(niche_values) if niche_values else None,
    }


def enrich_item(item: dict) -> dict:
    detail = parse_detail_page(fetch(item["source_url"]), item["source_url"])
    enriched = {**item, **detail}
    if not enriched["description"]:
        enriched["description"] = enriched.get("summary") or enriched["title"]
    return enriched


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
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def crawl(max_pages: int = 20) -> list[dict]:
    page_url: Optional[str] = START_URL
    pages_seen: set[str] = set()
    items: list[dict] = []

    while page_url and page_url not in pages_seen and len(pages_seen) < max_pages:
        pages_seen.add(page_url)
        page_items, next_url = parse_listing_page(fetch(page_url), page_url)
        if not page_items:
            break
        items.extend(enrich_item(item) for item in page_items)
        page_url = next_url

    return items


def run(max_pages: int = 20) -> int:
    items = crawl(max_pages=max_pages)
    if not items:
        return 0
    return upsert(items, mark_stale=True)


if __name__ == "__main__":
    n = run()
    print(f"Saved {n} records")
