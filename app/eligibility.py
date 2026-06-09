from __future__ import annotations

import re
from typing import Any


ANY = "any"
BUSINESS = "business"
ACADEMIC = "academic"
RESEARCH_ORG = "research_org"
PUBLIC_SECTOR = "public_sector"
CHARITY = "charity"
INDIVIDUAL = "individual"

ALLOWED_ELIGIBLE_APPLICANTS = {
    BUSINESS,
    ACADEMIC,
    RESEARCH_ORG,
    PUBLIC_SECTOR,
    CHARITY,
    INDIVIDUAL,
    ANY,
}

RESEARCH_COUNCIL_NAMES = (
    "AHRC",
    "Arts and Humanities Research Council",
    "BBSRC",
    "Biotechnology and Biological Sciences Research Council",
    "EPSRC",
    "Engineering and Physical Sciences Research Council",
    "ESRC",
    "Economic and Social Research Council",
    "MRC",
    "Medical Research Council",
    "NERC",
    "Natural Environment Research Council",
    "STFC",
    "Science and Technology Facilities Council",
)


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


def _format(values: set[str]) -> str:
    if not values or ANY in values:
        return ANY
    order = (BUSINESS, ACADEMIC, RESEARCH_ORG, PUBLIC_SECTOR, CHARITY, INDIVIDUAL)
    return ",".join(value for value in order if value in values)


def _explicit_applicant_types(text: str) -> set[str]:
    values: set[str] = set()
    if not re.search(
        r"\b(?:eligible|eligibility|apply|applicants?|open to|for|must be|lead organisation|lead applicant|can apply)\b",
        text,
    ):
        return values

    if re.search(r"\b(?:businesses|companies|smes|micro businesses|enterprises|industry partners?)\b", text):
        values.add(BUSINESS)
    if re.search(r"\b(?:universities|university|higher education institutions?|heis?|academic institutions?)\b", text):
        values.add(ACADEMIC)
    if re.search(r"\b(?:research organisations?|research institutes?|rtos?|catapults?)\b", text):
        values.add(RESEARCH_ORG)
    if re.search(r"\b(?:public sector|local authorities|local authority|nhs|government departments?)\b", text):
        values.add(PUBLIC_SECTOR)
    if re.search(r"\b(?:charities|charity|non profits?|not for profits?|voluntary organisations?)\b", text):
        values.add(CHARITY)
    if re.search(r"\b(?:individuals|doctoral students?|students?|fellows?|researchers?)\b", text):
        values.add(INDIVIDUAL)

    return values


def _is_ktp(text: str) -> bool:
    return bool(re.search(r"\b(?:accelerated\s+)?knowledge transfer partnership(?:s)?\b|\baktp\b|\bktp\b", text))


def _is_personal_award(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:fellowship|fellowships|doctoral|studentship|phd|early career|new investigator|personal award)\b",
            text,
        )
    )


def _is_business_programme(text: str) -> bool:
    return _contains_phrase(
        text,
        "Innovate UK",
        "SMART grant",
        "SMART grants",
        "Contracts for Innovation",
        "Small Business Research Initiative",
        "SBRI",
        "collaborative research and development",
        "collaborative R&D",
        "accelerator",
        "investment fund",
        "loan fund",
        "business loan",
    )


def _is_research_council_text(text: str) -> bool:
    return _contains_phrase(text, *RESEARCH_COUNCIL_NAMES)


def classify_eligible_applicants(opportunity: Any) -> str:
    """Return a conservative applicant-type eligibility set.

    Values are stored as a comma-separated set drawn from
    ``business, academic, research_org, public_sector, charity, individual, any``.
    ``any`` is the safe default when a clear applicant-type restriction is not
    present.
    """
    source = _value(opportunity, "source").strip().lower()
    text = _normalise(_combined_text(opportunity))
    if not text:
        return ANY

    if source in {"horizon_europe", "horizon europe"}:
        return ANY

    if _is_ktp(text):
        return _format({BUSINESS, ACADEMIC})

    if _is_personal_award(text):
        values = {INDIVIDUAL}
        if _contains_phrase(text, "university", "higher education institution", "hei", "academic institution"):
            values.add(ACADEMIC)
        return _format(values)

    if source in {"innovate_uk", "innovate uk", "iuk_business_connect"}:
        explicit_values = _explicit_applicant_types(text)
        if ACADEMIC in explicit_values and re.search(r"\b(?:must|requires?|required)\b.{0,80}\b(?:academic|university|hei)\b", text):
            return _format({BUSINESS, ACADEMIC})
        return BUSINESS

    if _is_business_programme(text):
        explicit_values = _explicit_applicant_types(text)
        if ACADEMIC in explicit_values and re.search(r"\b(?:must|requires?|required)\b.{0,80}\b(?:academic|university|hei)\b", text):
            return _format({BUSINESS, ACADEMIC})
        return BUSINESS

    explicit_values = _explicit_applicant_types(text)
    if explicit_values:
        return _format(explicit_values)

    if _is_research_council_text(text):
        return ACADEMIC

    return ANY
