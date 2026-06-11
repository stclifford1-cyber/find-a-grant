# Find a Grant

Find a Grant is a FastAPI web application for finding UK and EU funding competitions relevant to SMEs, research organisations, and innovation partnerships.

The app aggregates live funding opportunities from several sources into a searchable card interface with filters, funding ranges, deadline status, keyword tags, and expandable details.

## Current Sources

All current sources have repeatable ingestion pipelines and are included in the combined daily ingest.

| Source | Ingest module | Notes |
| --- | --- | --- |
| Innovate UK Apply for Funding | `app.ingest_innovateuk` | Main Innovate UK competition source. |
| Innovate UK Business Connect | `app.ingest_iuk_business_connect` | Business Connect opportunities are normalised under the Innovate UK filter. |
| Konfer | `app.ingest_konfer` | Included as a standing source. Konfer records that point to Business Connect opportunities are skipped to avoid duplicates under two source labels. |
| Horizon Europe | `app.ingest_horizon_europe` | Uses the EU Funding & Tenders search API and stores euro values with approximate GBP conversion. |
| UKRI | `app.ingest_ukri` | Uses UKRI Funding Finder. Innovate-only UKRI listings are skipped to avoid duplicate Innovate UK competitions. |

## Features

- FastAPI backend with Jinja2 templates.
- SQLAlchemy ORM.
- SQLite database for local development.
- HTMX-powered filtering without full page reloads.
- Multi-select source filters for Innovate UK, UKRI, Horizon Europe, and Konfer.
- Keyword search across title, summary, and description.
- Sector/niche tag filtering.
- Open, rolling, and upcoming opportunity grouping.
- Automatic hiding of inactive and expired opportunities.
- Closing soon badge for opportunities closing within 14 days.
- Inline `Read more` / `Read less` details panels.
- Currency support for Horizon Europe native EUR amounts and approximate GBP values.
- Structured `geographic_scope` and `eligible_applicants` eligibility values for downstream filtering.
- Daily ingest script, macOS LaunchAgent helper, and GitHub Actions workflow.
- Production-ready `DATABASE_URL` support for Neon Postgres.
- Protected cloud ingest endpoint using `CRON_SECRET`.
- Security headers, production-disabled API docs, Dependabot, and CodeQL scanning.
- Top-of-page source health status showing the last successful ingest and relevant zero-result source checks.

## Project Structure

```text
app/
  main.py                         FastAPI routes, filters, and template rendering
  models.py                       SQLAlchemy Opportunity model
  schema.py                       Lightweight schema update helper
  database.py                     Database engine/session setup
  ingest_all.py                   Combined ingest and deduplication runner
  ingest_innovateuk.py            Innovate UK Apply for Funding ingest
  ingest_iuk_business_connect.py  IUK Business Connect ingest
  ingest_konfer.py                Konfer ingest
  ingest_horizon_europe.py        Horizon Europe ingest
  ingest_ukri.py                  UKRI Funding Finder ingest
  templates/                      Jinja2 templates
  static/                         Static assets
scripts/
  run_daily_ingest.sh             Runs `app.ingest_all`
  install_daily_ingest.sh         Installs the macOS LaunchAgent
  com.find-a-grant.daily-ingest.plist
tests/
  test_*.py                       Parser, filtering, ingestion, and dedupe tests
```

## Run Locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run all ingests:

```bash
python -m app.ingest_all
```

Start the app:

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Daily Local Ingest

The combined ingest can be run manually:

```bash
scripts/run_daily_ingest.sh
```

On macOS, install the LaunchAgent:

```bash
scripts/install_daily_ingest.sh
```

This is useful for local operation, but it depends on the Mac being awake and online.

## Tests

Run the test suite:

```bash
pytest
```

Current expected result:

```text
31 passed
```

## Current Database

Local development currently uses:

```text
find_a_grant.db
```

This SQLite file is suitable for local development and is ignored by Git.

In production, set:

```text
DATABASE_URL=postgresql://...
```

For Neon, use the Postgres connection string with `sslmode=require`. A pooled Neon connection string is recommended for serverless deployments.

## Production Data Rule

Production data must come from the live ingesters only:

```bash
python -m app.ingest_all
```

or the protected Vercel ingest endpoint. Do not run `seed.py` against Neon or any production database. `seed.py` contains illustrative local-development fixtures, not real funding opportunities, and refuses to run unless the configured database is SQLite.

## Structured Eligibility

Each opportunity stores a nullable `geographic_scope` value for applicant-location eligibility. The field is populated conservatively during ingest and defaults to `uk_wide` unless the scraped funder, title, or eligibility text clearly restricts applicants to a nation or English region.

Allowed values are lowercase snake case:

```text
uk_wide
scotland
wales
northern_ireland
england
england_north_east
england_north_west
england_yorkshire
england_east_midlands
england_west_midlands
england_east
england_london
england_south_east
england_south_west
unknown
```

Schemes that genuinely cover several English regions store a comma-separated list, for example:

```text
england_east_midlands,england_west_midlands
```

Horizon Europe calls are stored as `uk_wide`; this field describes UK applicant location eligibility, not consortium composition rules.

Each opportunity also stores a nullable `eligible_applicants` value for applicant-type eligibility. Values are a comma-separated set drawn from:

```text
business
academic
research_org
public_sector
charity
individual
any
```

Use multiple values where a scheme clearly requires a partnership, for example:

```text
business,academic
```

The applicant classifier is conservative and defaults to `any` unless the scraped funder, title, or eligibility text clearly restricts who may apply. Horizon Europe and other broad consortium programmes are stored as `any`.

Production rollout order:

```bash
export DATABASE_URL="postgresql://..."
python -m scripts.migrate                         # adds nullable columns, idempotent
python -m scripts.backfill_geographic_scope       # fills existing geographic_scope rows
python -m scripts.backfill_eligible_applicants    # fills existing eligible_applicants rows
```

`scripts.migrate` is additive and idempotent. It only ensures the nullable structured eligibility columns exist and reports whether each column is present, total rows, and rows where each field is not null. The backfills are also safe to re-run; each script only sets its own structured eligibility column from a deterministic classifier.

## Vercel Deployment

The repository includes:

- `.python-version` pinned to Python 3.14.
- `api/index.py` as the Vercel FastAPI entrypoint.
- `vercel.json` with Hobby-safe routing for the FastAPI app.
- `POST /api/ingest`, protected by `CRON_SECRET`.

Required Vercel environment variables:

```text
DATABASE_URL
CRON_SECRET
INGEST_TIMEOUT_SECONDS=300
```

Anonymous requests, incorrect bearer tokens, and unset `CRON_SECRET` are rejected with `401` before ingest code is called.

The ingest endpoint uses a 300 second application budget by default. The ingest runner keeps a 30 second safety margin before each stage, so slow runs fail with a non-200 response and logs instead of reporting a successful run too close to the configured timeout.

On Vercel Hobby, the protected endpoint is for manual/admin use only. The full scheduled ingest does not run on Vercel because the five-source scrape takes several minutes and exceeds Hobby function limits.

Production responses include standard browser security headers, including a Content Security Policy, clickjacking protection, `nosniff`, referrer policy, permissions policy, and HSTS. FastAPI's interactive API docs are available during local development but disabled when `VERCEL_ENV=production` or `ENVIRONMENT=production`.

Dependency and code scanning are configured through:

```text
.github/dependabot.yml
.github/workflows/codeql.yml
```

## GitHub Actions Daily Ingest

The daily production refresh runs from:

```text
.github/workflows/daily-ingest.yml
```

It has scheduled attempts at about 06:17, 08:17, 10:17, and 12:17 UTC, and can also be started manually from GitHub Actions. Multiple attempts reduce the impact of GitHub Actions delaying or dropping a scheduled event; the ingest is idempotent and the workflow uses concurrency so runs do not overlap. The off-hour minute reduces the chance of GitHub Actions delaying or dropping the scheduled run during top-of-hour load. The workflow installs dependencies and runs:

```bash
python -m app.ingest_all
```

directly against Neon. This avoids Vercel Hobby execution limits and records a successful UTC ingest timestamp in the database after all five sources, expiry cleanup, and deduplication finish.

Required GitHub repository secrets:

```text
DATABASE_URL
CRON_SECRET
```

`DATABASE_URL` is required by the scheduled ingest. `CRON_SECRET` protects the manual `POST /api/ingest` endpoint and is included in the workflow environment for consistency, though the scheduled workflow writes to Neon directly rather than calling that endpoint.

The top of the page renders the database freshness signal:

```text
Last updated: 2 June 2026, 06:04 UTC
Sources checked successfully
```

A stale timestamp is the public health check that the scheduled ingest did not complete successfully. After midnight UTC, the page shows `Today's refresh pending` until a successful refresh has been recorded for the new UTC day. The page also renders relevant source notes, including when Konfer was checked successfully but returned only duplicate Business Connect records:

```text
Konfer checked successfully: no unique opportunities found.
```

## Deployment Direction

Target public deployment:

- GitHub repository
- Vercel-hosted FastAPI app
- Neon Postgres database
- Secured manual ingest endpoint
- Security headers and automated dependency/code scanning
- GitHub Actions daily refresh

The next deployment phase should:

1. Create a Neon database and set `DATABASE_URL` in Vercel.
2. Set `CRON_SECRET` in Vercel to a random value with at least 16 characters.
3. Add `DATABASE_URL` and `CRON_SECRET` as GitHub repository secrets.
4. Deploy to Vercel from GitHub.
5. Run the GitHub Actions `Daily ingest` workflow manually once, or wait for the next scheduled run.
6. Confirm the workflow logs show a successful full ingest.
7. Confirm production data counts, public UI results, and the top-of-page freshness/source status.

## Notes

- Innovate UK and Business Connect are shown together under the Innovate UK filter.
- UKRI ingest intentionally avoids Innovate-only listings to reduce duplication.
- Horizon Europe links use a narrower EU Funding & Tenders search URL and expose EU references where available.
- Konfer is retained as a source even when no live non-duplicated opportunities are returned.
