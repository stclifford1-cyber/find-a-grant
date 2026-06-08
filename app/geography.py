from __future__ import annotations

import re
from typing import Any


UK_WIDE = "uk_wide"
UNKNOWN = "unknown"

ALLOWED_GEOGRAPHIC_SCOPES = {
    UK_WIDE,
    "scotland",
    "wales",
    "northern_ireland",
    "england",
    "england_north_east",
    "england_north_west",
    "england_yorkshire",
    "england_east_midlands",
    "england_west_midlands",
    "england_east",
    "england_london",
    "england_south_east",
    "england_south_west",
    UNKNOWN,
}

MIDLANDS_ENGINE_SCOPE = "england_east_midlands,england_west_midlands"
NORTHERN_POWERHOUSE_SCOPE = "england_north_east,england_north_west,england_yorkshire"


def _value(opportunity: Any, key: str) -> str:
    if isinstance(opportunity, dict):
        value = opportunity.get(key)
    else:
        value = getattr(opportunity, key, None)
    return str(value or "")


def _combined_text(opportunity: Any) -> str:
    keys = (
        "source",
        "title",
        "organisation",
        "funders",
        "funder",
        "award",
        "opportunity_type",
        "sector_tags",
        "niche_tags",
        "summary",
        "description",
        "eligibility",
        "eligibility_text",
    )
    return "\n".join(_value(opportunity, key) for key in keys)


def _normalise(text: str) -> str:
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, *phrases: str) -> bool:
    return any(_normalise(phrase) in text for phrase in phrases)


def _explicit_location_scope(text: str) -> str | None:
    explicit_patterns = (
        r"\b(?:businesses|organisations|applicants|companies|smes|enterprises)\s+(?:must\s+be\s+)?(?:based|located|registered)\s+in\s+([a-z ]+)",
        r"\bopen\s+to\s+(?:businesses|organisations|applicants|companies|smes|enterprises)\s+(?:based|located|registered)\s+in\s+([a-z ]+)",
        r"\bfor\s+(?:businesses|organisations|applicants|companies|smes|enterprises)\s+(?:based|located|registered)\s+in\s+([a-z ]+)",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        location = match.group(1).strip()
        scoped = _scope_from_location_phrase(location)
        if scoped:
            return scoped
    return None


def _scope_from_location_phrase(location: str) -> str | None:
    location = _normalise(location)
    if "scotland" in location:
        return "scotland"
    if "wales" in location:
        return "wales"
    if "northern ireland" in location:
        return "northern_ireland"
    if "north east" in location:
        return "england_north_east"
    if "north west" in location:
        return "england_north_west"
    if "yorkshire" in location or "humber" in location:
        return "england_yorkshire"
    if "east midlands" in location:
        return "england_east_midlands"
    if "west midlands" in location:
        return "england_west_midlands"
    if "london" in location:
        return "england_london"
    if "south east" in location:
        return "england_south_east"
    if "south west" in location:
        return "england_south_west"
    if re.search(r"\beast of england\b|\beast england\b", location):
        return "england_east"
    if "england" in location:
        return "england"
    return None


def classify_geographic_scope(opportunity: Any) -> str:
    """Return a conservative applicant-location eligibility scope.

    Multiple English regions are stored as a comma-separated list, for example
    ``england_east_midlands,england_west_midlands`` for Midlands Engine funds.
    """
    source = _value(opportunity, "source").strip().lower()
    if source in {"horizon_europe", "horizon europe"}:
        return UK_WIDE

    text = _normalise(_combined_text(opportunity))
    if not text:
        return UNKNOWN

    explicit_scope = _explicit_location_scope(text)
    if explicit_scope:
        return explicit_scope

    if _contains_phrase(
        text,
        "Scottish Enterprise",
        "SMART Scotland",
        "Highlands and Islands Enterprise",
        "Highlands Islands Enterprise",
        "South of Scotland Enterprise",
    ) or re.search(r"\bfor scotland\b", text):
        return "scotland"

    if _contains_phrase(
        text,
        "Development Bank of Wales",
        "Business Wales",
        "Welsh Government",
    ) or re.search(r"\bfor wales\b", text):
        return "wales"

    if _contains_phrase(
        text,
        "Invest Northern Ireland",
        "Invest NI",
    ) or re.search(r"\bfor northern ireland\b", text):
        return "northern_ireland"

    if _contains_phrase(text, "Midlands Engine"):
        return MIDLANDS_ENGINE_SCOPE

    if _contains_phrase(text, "Northern Powerhouse"):
        return NORTHERN_POWERHOUSE_SCOPE

    regional_fund_patterns = (
        ("South West Investment Fund", "england_south_west"),
        ("South East Investment Fund", "england_south_east"),
        ("North East Investment Fund", "england_north_east"),
        ("North West Investment Fund", "england_north_west"),
        ("Yorkshire and Humber Investment Fund", "england_yorkshire"),
        ("Yorkshire Investment Fund", "england_yorkshire"),
        ("East Midlands Investment Fund", "england_east_midlands"),
        ("West Midlands Investment Fund", "england_west_midlands"),
        ("East of England Investment Fund", "england_east"),
        ("London Investment Fund", "england_london"),
    )
    for phrase, scope in regional_fund_patterns:
        if _contains_phrase(text, phrase):
            return scope

    return UK_WIDE
