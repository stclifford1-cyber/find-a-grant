# Grant Finder Project Memory

## Current State

Grant Finder is a local FastAPI/Jinja2/SQLAlchemy application that aggregates UK and EU funding opportunities into a searchable web interface.

The local app is working and currently uses SQLite:

```text
find_a_grant.db
```

The app has been expanded from an Innovate UK-only prototype into a multi-source funding finder.

## Current Live Sources

All active sources now have repeatable ingests.

| Source | Module | Status |
| --- | --- | --- |
| Innovate UK Apply for Funding | `app.ingest_innovateuk` | Working |
| Innovate UK Business Connect | `app.ingest_iuk_business_connect` | Working |
| Konfer | `app.ingest_konfer` | Working, but the live Konfer site may currently return no results |
| Horizon Europe | `app.ingest_horizon_europe` | Working |
| UKRI Funding Finder | `app.ingest_ukri` | Working |

Combined runner:

```bash
python -m app.ingest_all
```

Daily local script:

```bash
scripts/run_daily_ingest.sh
```

## Important Implementation Notes

- `app.ingest_all` runs all source ingests, marks expired opportunities inactive, and runs cross-source deduplication.
- Production must be populated only by live ingesters. `seed.py` is local-development fixtures and must not write to Neon or any production database.
- Business Connect opportunities are normalised under the Innovate UK source filter.
- UKRI ingest skips Innovate-only UKRI Funding Finder listings to avoid duplicating Innovate UK competitions.
- Konfer is kept as a standing source filter even if there are no live Konfer opportunities.
- Horizon Europe stores native EUR amounts and approximate GBP conversions.
- Search includes title, summary, and description.
- Sector/niche filters include title, summary, sector tags, niche tags, and description.
- Closing soon is defined as open opportunities closing within 14 days.
- `Read more` details now toggle to `Read less` and collapse correctly.
- Site background has been changed to Crystal Blue: `#d8eef5`.
- Section headers use dark navy `#123a5a` for contrast on the Crystal Blue background.

## Current UI

Homepage tagline:

```text
UK/EU funding competitions for SMEs
```

Standing source filters:

- All
- Innovate UK
- UKRI
- Horizon Europe
- Konfer

Main grouping:

- Open
- Rolling
- Upcoming

## Current Test Status

Last verified command:

```bash
.venv/bin/python -m pytest
```

Expected result:

```text
21 passed
```

## Current Local Deployment

Local preview command:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Local URL:

```text
http://127.0.0.1:8000
```

## Next Major Phase

The next phase is public deployment.

Target:

- GitHub repository
- Vercel-hosted FastAPI app
- Neon Postgres database
- Secured cloud daily ingest
- GitHub Actions scheduled daily ingest

Deployment preparation added:

- Root `.gitignore` for local databases, caches, virtualenvs, `.DS_Store`, logs, and backup files.
- MIT `LICENSE`.
- `DATABASE_URL` support with SQLite fallback.
- Neon/Postgres support via SQLAlchemy and psycopg.
- `.python-version` pinned to Python 3.14.
- Vercel FastAPI entrypoint in `api/index.py`.
- `vercel.json` with Hobby-safe FastAPI rewrite only.
- GitHub Actions daily ingest workflow at `.github/workflows/daily-ingest.yml`.
- Protected `/api/ingest` endpoint using `CRON_SECRET`.
- Default cloud ingest budget of 300 seconds with a 30 second stage safety margin so slow/partial runs fail visibly.
- Auth tests proving anonymous, wrong-token, and unset-secret calls cannot trigger ingest.
- Last successful ingest timestamp recorded in database metadata and rendered in the page footer.

This should be treated as a deliberate deployment build, not a quick tidy-up.

Recommended steps:

1. Clean repository before commit:
   - add `.gitignore`
   - remove backup files such as `*.bak`
   - exclude `.venv`, `__pycache__`, `.pytest_cache`, `.DS_Store`, and local database files
   - decide whether `find_a_grant.db` should be excluded from Git
2. Add production database configuration:
   - use `DATABASE_URL` when present
   - keep SQLite as local fallback if desired
3. Prepare Postgres compatibility:
   - verify `schema.py` against Postgres
   - consider a lightweight migration approach if needed
4. Add a secured ingest endpoint:
   - protect with `CRON_SECRET`
   - ensure ordinary visitors cannot trigger ingest
5. Add Vercel configuration:
   - FastAPI routing
   - environment variables
6. Add GitHub Actions scheduled ingest:
   - daily around 06:00 UTC
   - `DATABASE_URL` from repository secrets
   - direct `python -m app.ingest_all` run against Neon
7. Deploy to Vercel with Neon.
8. Run and verify a production ingest.
9. Check logs, timeouts, data counts, footer freshness timestamp, and public UI.

Production verification must include confirming the full five-source ingest completes in GitHub Actions and updates the footer timestamp. Vercel Hobby should host the site only; the scheduled public refresh runs off Vercel.

## Fresh Chat Handoff Prompt

Use this prompt to start the next Codex session:

```text
We are working on the Grant Finder FastAPI app in /Users/simonclifford/projects/find-a-grant.

Current local state:
- FastAPI/Jinja2/SQLAlchemy app
- SQLite local DB find_a_grant.db
- Sources with repeatable ingests:
  - app.ingest_innovateuk
  - app.ingest_iuk_business_connect
  - app.ingest_konfer
  - app.ingest_horizon_europe
  - app.ingest_ukri
- Combined ingest: app.ingest_all
- Local daily script: scripts/run_daily_ingest.sh
- Tests currently pass: 21 passed
- UI has source chips, Read more/Read less collapse, 14-day closing soon badge, Crystal Blue background #d8eef5

Objective:
Prepare this app for public GitHub/Vercel deployment with Neon Postgres and a secured cloud scheduled ingest.

Start by inspecting the repo, README.md, PROJECT_MEMORY.md, current git status, requirements, database config, schema helper, and ingest_all.py.

Do not push or commit until explicitly approved.
First propose a deployment plan, including .gitignore cleanup, local-vs-production database config, Postgres compatibility, Vercel FastAPI config, CRON_SECRET-protected ingest endpoint, and GitHub Actions scheduled ingest.
```
