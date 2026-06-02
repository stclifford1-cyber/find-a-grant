from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .database import SessionLocal
from .models import Opportunity

BASE = "https://apply-for-innovation-funding.service.gov.uk"
SEARCH = f"{BASE}/competition/search"


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
# Remove Innovate UK competitions that have already closed
    today = date.today()
    db.query(Opportunity).filter(
        Opportunity.source.in_(["innovate_uk", "Innovate UK"]),
        Opportunity.closes_date.isnot(None),
        Opportunity.closes_date < today
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
                existing.source = item["source"]
                existing.title = item["title"]
                existing.url = item["url"]
                existing.summary = item.get("summary")
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


def run(max_pages: int = 20) -> int:
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

