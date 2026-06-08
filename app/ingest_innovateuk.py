from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .database import SessionLocal
from .models import Opportunity

BASE = "https://apply-for-innovation-funding.service.gov.uk"
SEARCH = f"{BASE}/competition/search"
DETAIL_TIMEOUT_SECONDS = 25
DETAIL_MIN_REMAINING_SECONDS = 15
NEW_RECORD_WINDOW = timedelta(hours=24)
REQUEST_HEADERS = {"User-Agent": "find-a-grant-ingester/0.1"}

logger = logging.getLogger(__name__)


def parse_ifs_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d %B %Y").date()
    except ValueError:
        return None


def status_from_dates(opened: Optional[date], closes: Optional[date]) -> str:
    today = date.today()
    if opened and opened > today:
        return "upcoming"
    if closes and closes < today:
        # We do not store expired items by default.
        return "expired"
    return "open"


def fetch_page(page: int) -> str:
    url = SEARCH if page == 1 else f"{SEARCH}?page={page}"
    r = requests.get(
        url,
        timeout=25,
        headers={"User-Agent": "find-a-grant-ingester/0.1"},
    )
    r.raise_for_status()
    return r.text


def ingest_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    for li in soup.select("ul.govuk-list > li"):
        a = li.select_one("h2 a.govuk-link[href]")
        if not a:
            continue

        title = a.get_text(" ", strip=True)
        href = a.get("href", "").strip()
        if not href:
            continue

        source_url = href if href.startswith("http") else f"{BASE}{href}"

        # One-line summary: first div after the title
        summary_div = li.select_one("div.wysiwyg-styles")
        summary = summary_div.get_text(" ", strip=True) if summary_div else ""

        dds = li.select("dl.date-definition-list dd")
        opened = parse_ifs_date(dds[0].get_text(" ", strip=True)) if len(dds) >= 1 else None
        closes = parse_ifs_date(dds[1].get_text(" ", strip=True)) if len(dds) >= 2 else None

        # Use a stable id based on the competition number in the URL: /competition/<id>/...
        comp_id = None
        parts = href.split("/")
        for i, p in enumerate(parts):
            if p == "competition" and i + 1 < len(parts):
                comp_id = parts[i + 1]
                break
        if not comp_id:
            continue

        items.append(
            {
                "id": f"ifs:{comp_id}",
                "source": "innovate_uk",
                "title": title,
                "url": source_url,          # temporary, see Step 4
                "summary": summary,
                "description": summary,     # one-line summary
                "opened_date": opened,
                "closes_date": closes,
            }
        )

    return items


def upsert(items: list[dict]) -> int:
    db = SessionLocal()
    # Remove Innovate UK competitions that have already closed.
    today = date.today()
    db.query(Opportunity).filter(
        Opportunity.source.in_(["innovate_uk", "Innovate UK"]),
        Opportunity.closes_date.isnot(None),
        Opportunity.closes_date < today,
    ).delete(synchronize_session=False)

    db.commit()

    now = datetime.now(timezone.utc)
    changed = 0

    try:
        for item in items:
            status = status_from_dates(item["opened_date"], item["closes_date"])
            if status == "expired":
                continue

            existing = db.query(Opportunity).filter(Opportunity.id == item["id"]).one_or_none()
            if existing:
                was_unenriched = (existing.description or "") == (existing.summary or "")
                existing.source = item["source"]
                existing.title = item["title"]
                existing.url = item["url"]
                existing.summary = item.get("summary")
                if was_unenriched:
                    existing.description = item["description"]
                existing.opened_date = item["opened_date"]
                existing.closes_date = item["closes_date"]
                existing.status = status
                existing.last_seen = now
                changed += 1
            else:
                db.add(
                    Opportunity(
                        id=item["id"],
                        source=item["source"],
                        title=item["title"],
                        url=item["url"],
                        opened_date=item["opened_date"],
                        closes_date=item["closes_date"],
                        funding_min=None,
                        funding_max=None,
                        sector_tags=None,
                        niche_tags=None,
                        summary=item.get("summary"),
                        description=item["description"],
                        status=status,
                        last_seen=now,
                    )
                )
                changed += 1

        db.commit()
        return changed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def competition_number(record_id: str | None) -> str | None:
    match = re.fullmatch(r"ifs:(\d+)", (record_id or "").strip())
    return match.group(1) if match else None


def detail_url(record_id: str) -> str | None:
    numeric_id = competition_number(record_id)
    if not numeric_id:
        return None
    return f"{BASE}/competition/{numeric_id}/overview"


def fetch_detail_page(record_id: str) -> str:
    url = detail_url(record_id)
    if not url:
        raise ValueError(f"Cannot build Innovate UK detail URL for {record_id!r}.")
    response = requests.get(
        url,
        timeout=DETAIL_TIMEOUT_SECONDS,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    return response.text


def clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def parse_pound_amount(value: str) -> float | None:
    match = re.search(r"£\s*([\d,]+(?:\.\d+)?)\s*(million|m|thousand|k)?\b", value, re.I)
    if not match:
        return None

    amount = float(match.group(1).replace(",", ""))
    multiplier = (match.group(2) or "").lower()
    if multiplier in {"million", "m"}:
        amount *= 1_000_000
    elif multiplier in {"thousand", "k"}:
        amount *= 1_000
    return amount


def parse_funding_range(text: str) -> tuple[float | None, float | None]:
    amount_pattern = r"£\s*[\d,]+(?:\.\d+)?\s*(?:million|m|thousand|k)?\b"
    amounts = [
        amount
        for amount in (
            parse_pound_amount(match.group(0))
            for match in re.finditer(amount_pattern, text or "", re.I)
        )
        if amount is not None
    ]
    if not amounts:
        return None, None
    return min(amounts), max(amounts)


def _main_content(soup: BeautifulSoup):
    return (
        soup.select_one("#main-content")
        or soup.select_one("main")
        or soup.select_one(".govuk-main-wrapper")
        or soup.body
        or soup
    )


def _section_texts(main) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    headings = main.find_all(re.compile(r"^h[1-4]$"))

    for heading in headings:
        title = clean_text(heading.get_text("\n", strip=True))
        chunks: list[str] = []
        for sibling in heading.find_next_siblings():
            if sibling.name and re.fullmatch(r"h[1-4]", sibling.name):
                break
            if getattr(sibling, "name", None) in {"script", "style", "nav"}:
                continue
            text = clean_text(sibling.get_text("\n", strip=True))
            if text:
                chunks.append(text)
        body = "\n".join(chunks).strip()
        if title and body:
            sections.append((title, body))

    return sections


def parse_detail_page(html: str, source_url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    main = _main_content(soup)
    for element in main.select("script, style, nav, .govuk-breadcrumbs, .govuk-phase-banner"):
        element.decompose()

    wanted_heading = re.compile(
        r"\b(description|scope|specific themes|eligibility|who can apply|your project|funding|project costs?|subsidy|grant)\b",
        re.I,
    )
    sections = [
        (heading, body)
        for heading, body in _section_texts(main)
        if wanted_heading.search(heading)
    ]

    description_parts: list[str] = []
    if source_url:
        description_parts.append(f"Source page: {source_url}")

    for heading, body in sections:
        description_parts.append(f"{heading}\n{body}")

    if not sections:
        wysiwyg_parts = [
            clean_text(element.get_text("\n", strip=True))
            for element in main.select(".wysiwyg-styles, .govuk-body")
        ]
        description_parts.extend(part for part in wysiwyg_parts if part)

    description = "\n\n".join(part for part in description_parts if part).strip()
    funding_min, funding_max = parse_funding_range(description or clean_text(main.get_text("\n", strip=True)))

    return {
        "description": description,
        "funding_min": funding_min,
        "funding_max": funding_max,
    }


def apply_enrichment(opportunity: Opportunity, enriched: dict) -> bool:
    description = (enriched.get("description") or "").strip()
    if not description:
        return False

    opportunity.description = description
    if enriched.get("funding_min") is not None:
        opportunity.funding_min = enriched["funding_min"]
        opportunity.funding_currency = "GBP"
        opportunity.funding_min_native = enriched["funding_min"]
        opportunity.exchange_rate = 1.0
        opportunity.exchange_rate_date = date.today()
    if enriched.get("funding_max") is not None:
        opportunity.funding_max = enriched["funding_max"]
        opportunity.funding_currency = "GBP"
        opportunity.funding_max_native = enriched["funding_max"]
        opportunity.exchange_rate = 1.0
        opportunity.exchange_rate_date = date.today()
    return True


def enrich_new_records(deadline: float | None = None) -> int:
    db = SessionLocal()
    processed = 0
    enriched_count = 0
    since = datetime.now(timezone.utc) - NEW_RECORD_WINDOW

    try:
        rows = (
            db.query(Opportunity)
            .filter(
                Opportunity.source.in_(["innovate_uk", "Innovate UK"]),
                Opportunity.description == Opportunity.summary,
                Opportunity.last_seen >= since,
            )
            .order_by(Opportunity.last_seen.desc(), Opportunity.title)
            .all()
        )

        for row in rows:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining < DETAIL_MIN_REMAINING_SECONDS:
                    logger.info(
                        "Stopping Innovate UK detail enrichment after %s/%s records; %.1fs remain.",
                        processed,
                        len(rows),
                        remaining,
                    )
                    break

            url = detail_url(row.id)
            if not url:
                logger.warning("Skipping Innovate UK enrichment for %s: invalid id.", row.id)
                processed += 1
                continue

            try:
                enriched = parse_detail_page(fetch_detail_page(row.id), source_url=url)
                if apply_enrichment(row, enriched):
                    enriched_count += 1
                    logger.info("Enriched Innovate UK competition %s (%s).", row.title, row.id)
                else:
                    logger.warning("Skipping Innovate UK enrichment for %s: no detail text found.", row.id)
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning("Failed to enrich Innovate UK competition %s (%s): %s", row.title, row.id, exc)
            finally:
                processed += 1

        return enriched_count
    finally:
        db.close()


def run(max_pages: int = 20, deadline: float | None = None) -> int:
    total_changed = 0
    for page in range(1, max_pages + 1):
        html = fetch_page(page)
        items = ingest_page(html)
        if not items:
            break
        total_changed += upsert(items)
    return total_changed


if __name__ == "__main__":
    n = run()
    print(f"Saved {n} records")
