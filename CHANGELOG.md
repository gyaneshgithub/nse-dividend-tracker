# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- **Projected next year income** dashboard card. For each stock it uses
  `max(Q1, Q2, Q3, Q4) × total dividend per share` for the selected year, sums
  the result, and highlights the percentage change versus that year's annual
  income. The same figures are exposed by `/api/analysis` as
  `projectedNextYearIncome` and `projectedIncreasePct`.

## [1.0.0] — 2026-11-02

First public release.

### Added

- NSE dividend history fetching and parsing with an in-memory TTL cache.
- Embedded ChromaDB storage with `holdings`, `payouts`, and `meta` collections.
- Quarterly quantity tracking, per-quarter income, annual and monthly totals.
- Automatic year rollover with blank quantities for the new year.
- Bulk import from spreadsheets and CSV.
- Analysis by year, stock, and quarter; semantic payout search.
- Cross-window revision polling and merge.
- Server and client delete tombstones that prevent resurrection by stale writes.
- JSON mirror at `data/portfolio.json` and restore into an empty database.
- Universal `run.py` launcher plus `start.sh` and `start.bat`.
- Setup check, end-to-end smoke test, and repository privacy audit.
- pytest suite and GitHub Actions CI on Linux, Windows, and macOS.
- Optional Dockerfile and Compose setup.
- Setup, user, ChromaDB, and architecture documentation.
- `AGENTS.md` plus pointer files for common AI coding assistants.

### Security

- Telemetry disabled by default.
- `data/`, `chroma_db/`, and `.env` excluded from git.
- No portfolio data is included in the repository.
