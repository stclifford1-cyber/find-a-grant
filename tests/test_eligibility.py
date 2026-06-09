from app.eligibility import classify_eligible_applicants


def test_classifier_detects_research_council_academic_grant() -> None:
    assert (
        classify_eligible_applicants(
            {
                "title": "EPSRC standard research grant",
                "description": "This funding is for universities and higher education institutions.",
            }
        )
        == "academic"
    )


def test_classifier_detects_ktp_business_academic_partnership() -> None:
    assert classify_eligible_applicants({"title": "Accelerated Knowledge Transfer Partnerships 6"}) == (
        "business,academic"
    )


def test_classifier_detects_innovate_uk_business_competition() -> None:
    assert (
        classify_eligible_applicants(
            {
                "source": "innovate_uk",
                "title": "Smart grants",
                "description": "UK registered SMEs can apply.",
            }
        )
        == "business"
    )


def test_classifier_keeps_horizon_europe_broad() -> None:
    assert classify_eligible_applicants({"source": "horizon_europe", "title": "Horizon Europe collaborative call"}) == "any"


def test_classifier_defaults_to_any_when_unclear() -> None:
    assert classify_eligible_applicants({"title": "Open innovation support"}) == "any"
