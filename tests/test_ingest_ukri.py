from datetime import date

from app.ingest_ukri import parse_detail_page, parse_listing_page, status_from_dates


def test_parse_listing_page_skips_innovate_only_duplicates() -> None:
    html = """
    <div class="opportunity">
      <h3><a class="ukri-funding-opp__link" href="https://www.ukri.org/opportunity/ukri-wide-call/">UKRI wide call</a></h3>
      <div class="entry-content"><p>Apply for UKRI-wide funding.</p></div>
      <dl class="opportunity__summary">
        <div class="govuk-table__row"><dt>Opportunity status:</dt><dd><span>Open</span></dd></div>
        <div class="govuk-table__row"><dt>Funders:</dt><dd>UK Research and Innovation, <a>Medical Research Council (MRC)</a></dd></div>
        <div class="govuk-table__row"><dt>Funding type:</dt><dd>Grant</dd></div>
        <div class="govuk-table__row"><dt>Award range:</dt><dd>£200,000 - £1,000,000</dd></div>
        <div class="govuk-table__row"><dt>Opening date:</dt><dd><time datetime="2026-08-13T09:00:00">13 August 2026</time></dd></div>
        <div class="govuk-table__row"><dt>Closing date:</dt><dd><time datetime="2026-11-05T16:00:00">5 November 2026</time></dd></div>
      </dl>
    </div>
    <div class="opportunity">
      <h3><a class="ukri-funding-opp__link" href="https://www.ukri.org/opportunity/innovate-only/">Innovate duplicate</a></h3>
      <div class="entry-content"><p>This funding is from Innovate UK.</p></div>
      <dl class="opportunity__summary">
        <div class="govuk-table__row"><dt>Funders:</dt><dd><a>Innovate UK</a></dd></div>
        <div class="govuk-table__row"><dt>Opening date:</dt><dd><time datetime="2026-06-02T09:00:00">2 June 2026</time></dd></div>
        <div class="govuk-table__row"><dt>Closing date:</dt><dd><time datetime="2026-08-03T11:00:00">3 August 2026</time></dd></div>
      </dl>
    </div>
    <a class="next page-numbers" href="/opportunity/page/2/?filter_status%5B0%5D=open">Next</a>
    """

    items, next_url = parse_listing_page(html, "https://www.ukri.org/opportunity/")

    assert next_url == "https://www.ukri.org/opportunity/page/2/?filter_status%5B0%5D=open"
    assert len(items) == 1
    assert items[0]["id"] == "ukri:ukri-wide-call"
    assert items[0]["source"] == "ukri"
    assert items[0]["opened_date"] == date(2026, 8, 13)
    assert items[0]["closes_date"] == date(2026, 11, 5)
    assert items[0]["funding_min"] == 200000
    assert items[0]["funding_max"] == 1000000
    assert items[0]["funding_currency"] == "GBP"


def test_parse_detail_page_extracts_application_link_and_description() -> None:
    html = """
    <h1 class="main-area__page-title"><span>Funding opportunity: </span>UKRI policy internships 2026</h1>
    <dl class="opportunity__summary">
      <div class="govuk-table__row"><dt>Funders:</dt><dd>UK Research and Innovation</dd></div>
      <div class="govuk-table__row"><dt>Opening date:</dt><dd><time datetime="2026-06-02T09:00:00">2 June 2026</time></dd></div>
      <div class="govuk-table__row"><dt>Closing date:</dt><dd><time datetime="2026-09-08T16:00:00">8 September 2026</time></dd></div>
    </dl>
    <a id="analytics-start-application" href="https://funding-service.ukri.org/OPP1284/apply/1322">Start application</a>
    <div class="single-opportunity__entry-content">
      <div class="description"><p>An opportunity for UKRI-funded doctoral students.</p></div>
    </div>
    """

    detail = parse_detail_page(html, "https://www.ukri.org/opportunity/ukri-policy-internships-2026/")

    assert detail["title"] == "UKRI policy internships 2026"
    assert detail["application_url"] == "https://funding-service.ukri.org/OPP1284/apply/1322"
    assert detail["opened_date"] == date(2026, 6, 2)
    assert detail["closes_date"] == date(2026, 9, 8)
    assert "UKRI-funded doctoral students" in detail["description"]


def test_status_from_dates_supports_upcoming_and_rolling() -> None:
    assert status_from_dates(date(2099, 1, 1), None) == "upcoming"
    assert status_from_dates(None, None) == "rolling"
