# AGENTS.md — instructions for AI coding assistants

This is the canonical guide for any AI assistant working in this repository.
Tool-specific pointer files (`CLAUDE.md`, `GEMINI.md`, `.cursorrules`,
`.windsurfrules`, `.github/copilot-instructions.md`) refer here.

## What this project is

A local-first NSE dividend tracker:

- `server.py` — Flask app composition root: request guards, headers, error
  handlers, startup.
- `routes/` — blueprints by area: pages, portfolio storage, dividends,
  analysis/insight/search, system status.
- `db.py` — embedded ChromaDB storage, merging, tombstones, analysis, search.
- `insight.py` — read-only portfolio scoring and narrative (no writes, no network).
- `coach.py` — read-only portfolio briefing, tasks and events (no writes, no network).
- `agent.py` — deterministic portfolio agents (reconcile, import/export, calendar,
  tax, refresh-diff alerts, income attribution, cashflow stress); writes only on
  import/autofill apply paths it orchestrates, not from coach.
- `forecast.py` — rolling cashflow scenarios, autofill plans, and stress metrics
  (read-only math).
- `market.py` — fetches and parses dividend history from stockanalysis.com.
- `templates/index.html` — page shell; `static/app.css` and `static/app.js`
  hold the UI styling and behaviour (vanilla, no build step).
- `run.py` — universal launcher; creates `.venv` on first run.
- `scripts/` — setup check, smoke test, repository privacy audit.
- `tests/` — pytest suite.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing storage or
concurrency behavior. The **Portfolio agents** table there lists `agent.py` and
`forecast.py` capabilities (reconcile, import, refresh diff, attribution,
stress, autofill).

## Commands

```bash
python run.py                    # bootstrap and start
python run.py --no-venv          # use current Python (CI/Docker)
python -m pytest                 # full test suite
python -m ruff check .           # lint
python scripts/check_setup.py    # environment + ChromaDB self-test
python scripts/smoke_test.py     # end-to-end API test, temporary storage
python scripts/check_repo.py     # privacy/packaging audit
```

On macOS/Linux, `./start.sh`; on Windows, `start.bat`.

## Non-negotiable invariants

1. **Never commit user data.** `data/`, `chroma_db/`, `.env`, and personal HTML
   exports must stay untracked. Do not add demo holdings to the live database or
   to `data/portfolio.json`.
2. **Tests must use temporary storage.** Set `DIVIDEND_DB_DIR` and
   `DIVIDEND_DATA_DIR` to `tmp_path`; call `db.reset_connection()`.
3. **No network in tests.** Mock `market._fetch_html` or `server.get_dividends`.
4. **Deletes are authoritative.** `db.delete_holding()` writes a tombstone. A
   write whose `updatedAt` is not newer than the tombstone must return `None`
   and must not re-create the holding.
5. **Every successful write bumps the revision and refreshes the JSON mirror.**
6. **Quarter quantities are per year and per quarter.** Missing quarters mean
   zero income; the automatic year rollover is the only place where the previous
   year's quantity is inherited into a newly added year (2027 inherits from
   2026, 2028 from 2027, ...).
7. **Cross-platform.** Use `pathlib`/`os.path`, avoid POSIX-only shell calls in
   Python, and keep line endings per `.gitattributes`.
8. **Keep the UI dependency-free.** No bundler, npm, CDN scripts, or framework.
9. **ChromaDB is embedded.** Do not introduce a server requirement.
10. **Fail fast on storage errors at startup.** A tracker that silently cannot
    persist data is worse than one that refuses to start.

## Storage map

- Chroma collections: `holdings`, `payouts`, `meta`.
- `meta` holds `revision` and `tombstones`.
- `data/portfolio.json` is a mirror used to restore an empty database.
- `db.init_storage()` opens the DB and restores the mirror when empty.
- `db.storage_info()` is the source of truth for status endpoints.

## Style

- Python 3.11+, standard library where practical, line length 120.
- Keep functions focused; preserve existing naming and response shapes.
- Comments explain *why*, not *what*.
- Do not reformat unrelated code.
- Do not add dependencies without a clear need and a pinned version.

## Before finishing a change

Run the full gate:

```bash
python scripts/preflight.py
```

It runs, in order:

```bash
python -m ruff check .
python -m pytest
python scripts/smoke_test.py
python scripts/check_repo.py
python scripts/check_docs.py
python scripts/check_ci.py      # latest CI run on the current branch must be green
```

`python scripts/install_hooks.py` installs the same gate as Git hooks, so
commits and pushes are checked even when a change is made outside OpenCode.

Report what you changed, what you tested, and any remaining risk. Do not claim a
Windows UI test unless you actually ran one; CI covers the Windows backend path.
