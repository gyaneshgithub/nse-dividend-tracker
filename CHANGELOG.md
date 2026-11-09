# Changelog

All notable changes to this project are documented here.

## [Unreleased]

### Added

- **Dividend compounder agent.** Briefing `dividend-compounder` tasks for
  holdings with ≥4-year streak, positive DPS CAGR, and no recent cuts; opens
  Insight (descriptive, not buy advice). Coach ruleset v7.
- **Portfolio agents user guide.** [docs/PORTFOLIO_AGENTS.md](docs/PORTFOLIO_AGENTS.md)
  explains each Briefing helper, workflows, and privacy for non-technical users.
- **Cashflow stress agent.** `forecast.stress_report` compares 12-month base vs
  conservative scenarios and explicit −10% quantity/DPS shocks. Briefing adds
  `cashflow-stress` tasks when gaps exceed thresholds; summary may note severe
  stress. Coach ruleset v6.
- **Income attribution agent.** Calendar-year briefing tasks decompose YoY income
  per ticker (quantity vs DPS vs composition) via `db.holding_yoy_attribution`.
- **Refresh diff agent.** Saves detect new or revised payouts; `dividendAlerts`
  persist on holdings; Briefing lists `new-payout` / `dps-change` with Dismiss.
- **Yield snapshot history and valuation insight.** Each successful refresh appends
  to `priceHistory` on the holding. With five or more snapshots, Insight scores
  **Yield vs own history** (valuation component) from today's yield versus the
  stock's own refresh median.
- **Monthly cashflow and India FY view.** Analysis returns `cashflowByMonth`; the
  year filter adds financial years (April–March). FY mode totals income by
  payout date and shows a monthly cashflow section in Analysis.
- **Income bridge and DPS growth columns.** Calendar-year analysis includes a
  year-on-year income decomposition (`quantity`, `dps`, `composition`) plus
  per-ticker DPS YoY and CAGR with sparklines on the dashboard table.
- **Portfolio briefing (coach v1).** `coach.py` now emits prioritized tasks
  (missing quantities, failed or stale fetches, dividend cuts, concentration,
  scenario spread) and dividend context events. The **Briefing** header button
  shows an urgent-count badge; the modal supports one-click actions (focus
  quarter, refresh, open Analysis or Insight).
- **Next-year income scenarios.** For a selected year, `GET /api/analysis` now
  returns `projections` with conservative, base and growth bands (plus notes).
  `projectedNextYearIncome` remains the base scenario. The dashboard shows Low /
  Base / Growth chips under the headline projection; Analysis and Insight Income
  tabs list all three.
- **Portfolio coach API (stub).** `GET /api/coach?year=2026|all` returns a stable,
  read-only briefing payload (`briefing`, `tasks`, `events`, `counts`, `ruleset`)
  computed from local holdings. Task and event lists are empty until coach rules
  land in a follow-up change. New module: `coach.py`.
- **First-run onboarding.** When the tracker is empty, a short panel explains
  how to add an NSE ticker, that dividend data comes from stockanalysis.com and
  is cached for six hours, and that holdings are stored locally. Example chips
  add a common stock in one click.
- **Actionable fetch errors.** Dividend failures now return a machine-readable
  `code` and a plain-language `hint` alongside `error`, distinguishing invalid
  tickers, missing dividend history, network failures, rate limiting and
  upstream errors. The UI shows a dismissible banner with the reason,
  **Retry**, and **Copy debug info**.
- **Portfolio insight** popup. A read-only, on-demand view of dividend health:
  an overall 0–100 score, income-resilience, growth and diversification
  components, plain-language strengths, risks and watchlist, and charts for
  income by year and income concentration. Served by `GET /api/insight` and
  computed from local tracker data; the result is never persisted. New module:
  `insight.py`.

### Changed

- **Narrow-screen layout.** On phones and small windows the controls and summary
  cards reflow, the table scrolls horizontally with a sticky header and a pinned
  stock column, and a swipe hint appears above it.
- **Code structure split with no behaviour change.** The UI is now an HTML
  shell (`templates/index.html`) plus `static/app.css` and `static/app.js`,
  loaded with plain `<link>`/`<script>` tags — still no bundler, npm or CDN.
  The Flask API is grouped into a `routes/` blueprint package (pages, portfolio,
  dividends, analysis, system), while `server.py` stays the composition root
  for request guards, security headers, error handlers and startup.
- **Year rollover now copies the previous year's quarterly quantities into a
  newly added year.** When the calendar rolls over (for example from 2026 to
  2027), each holding inherits its Q1–Q4 quantities from the previous year, so
  the new year starts with the quantity actually held last year instead of
  starting blank.

### Fixed

- The **projected next year income** shown in the portfolio insight now
  refreshes as soon as a quarter quantity is committed (on change/blur) while
  the insight popup is open, instead of waiting for the next sync poll.

## [1.1.0] — 2026-11-02

First release with the **Projected next year income** dashboard card.

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
