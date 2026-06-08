from app.geography import classify_geographic_scope


def test_classifier_defaults_to_uk_wide_when_no_restriction_is_clear() -> None:
    assert classify_geographic_scope({"title": "Open innovation grant", "description": "UK businesses can apply."}) == "uk_wide"


def test_classifier_keeps_horizon_europe_uk_wide() -> None:
    assert classify_geographic_scope({"source": "horizon_europe", "title": "A call for Wales"}) == "uk_wide"


def test_classifier_detects_scottish_funders_and_smart_scotland() -> None:
    assert classify_geographic_scope({"title": "SMART: Scotland grant"}) == "scotland"
    assert classify_geographic_scope({"niche_tags": "Highlands and Islands Enterprise"}) == "scotland"
    assert classify_geographic_scope({"niche_tags": "South of Scotland Enterprise"}) == "scotland"
    assert classify_geographic_scope({"description": "A programme for Scotland."}) == "scotland"


def test_classifier_detects_welsh_funders() -> None:
    assert classify_geographic_scope({"niche_tags": "Development Bank of Wales"}) == "wales"
    assert classify_geographic_scope({"description": "Delivered by Business Wales."}) == "wales"
    assert classify_geographic_scope({"description": "Funded by Welsh Government."}) == "wales"
    assert classify_geographic_scope({"title": "Innovation grants for Wales"}) == "wales"


def test_classifier_detects_northern_ireland_funders() -> None:
    assert classify_geographic_scope({"niche_tags": "Invest Northern Ireland"}) == "northern_ireland"
    assert classify_geographic_scope({"description": "Support for Northern Ireland businesses."}) == "northern_ireland"


def test_classifier_detects_british_business_bank_regional_funds() -> None:
    assert classify_geographic_scope({"title": "Midlands Engine Investment Fund"}) == "england_east_midlands,england_west_midlands"
    assert classify_geographic_scope({"title": "Northern Powerhouse Investment Fund"}) == (
        "england_north_east,england_north_west,england_yorkshire"
    )
    assert classify_geographic_scope({"title": "South West Investment Fund"}) == "england_south_west"


def test_classifier_detects_explicit_based_in_text() -> None:
    assert classify_geographic_scope({"description": "Open to businesses based in London."}) == "england_london"
    assert classify_geographic_scope({"description": "Applicants must be registered in England."}) == "england"
