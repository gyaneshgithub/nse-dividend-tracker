# Architecture

The tracker is a small local web application: a Flask server, an embedded
ChromaDB store, a dividend fetcher, and one vanilla JavaScript page (HTML
shell plus static CSS/JS assets).

```text
┌───────────────────────────────────────┐
│ Browser (templates/index.html +       │
│  static/app.css + static/app.js)      │
│  table · import · analysis            │
│  insight · search                     │
└──────────────┬────────────────────────┘
               │ JSON over HTTP
┌──────────────▼───────────────┐
│ server.py (Flask)            │
│  guards · headers · errors   │
│  routes/ blueprints          │
│  page + /api/* endpoints     │
└───────┬───────────────┬──────┘
        │               │
┌───────▼──────┐  ┌─────▼──────────────┐
│ db.py        │  │ market.py          │
│ ChromaDB     │  │ stockanalysis.com  │
│ JSON mirror  │  │ in-memory cache    │
└──────────────┘  └────────────────────┘
```

## Components

### `server.py`

- Composition root: creates the Flask app, registers the blueprints, and
  starts the server.
- Loads `.env` before importing `db`, because `db` resolves storage paths at
  import time.
- Adds the cross-origin write guard, security headers and JSON error handlers.
- Fails fast at startup if ChromaDB cannot be opened. Running without
  persistence would silently lose holdings.
- Adds `Cache-Control: no-store` so browsers do not cache portfolio responses.

### `routes/`

Blueprints grouped by area, registered by `routes.register_blueprints(app)`.
Each module owns its routes and the storage calls they need.

- `pages.py` — the page aliases and Jinja render of `index.html`.
- `portfolio.py` — `/api/portfolio` reads, upserts, deletes and bulk import,
  plus `/api/revision`.
- `dividends.py` — `/api/dividends`; resolves `server.get_dividends` at request
  time so the documented patch point still works. Failures return `error` plus
  a stable `code` (invalid symbol, missing history, network, rate limit,
  upstream) and a user-facing `hint`.
- `analysis.py` — `/api/analysis`, `/api/insight` and `/api/search`.
- `system.py` — `/api/health` and `/api/storage`.
- `common.py` — shared storage-status and storage-error helpers.

### `db.py`

- Opens one embedded ChromaDB `PersistentClient` per process.
- Normalizes and merges holdings.
- Maintains the payout index used by analysis and search.
- Writes the JSON mirror.
- Stores the revision counter and delete tombstones in the `meta` collection.
- Serializes writes with a re-entrant lock (`threading.RLock`).

### `insight.py`

- Turns holdings and `db.analysis()` output into a scored, explainable summary.
- Pure read path: no network, no language model, no writes. `insight.build()`
  loads holdings and analysis, computes per-holding dividend metrics and the
  portfolio components, then returns scores, narrative, chart data and the
  method description.
- Components are renormalised over the ones with data, so missing inputs do not
  drag the score down. Deferred signals are returned as a list, not scored.
- Reuses `db.analysis()` for income, payouts, yield and projected income so the
  popup and the dashboard cannot disagree.

### `market.py`

- Fetches the NSE dividend page from stockanalysis.com.
- Parses the company name, price, annual dividend, yield, and dividend history
  from the page's embedded JSON.
- Caches successful responses in memory for `DIVIDEND_CACHE_TTL` seconds
  (default six hours).
- Raises `LookupError` when no dividend history exists, `ValueError` for an
  invalid ticker, and propagates network errors.

### `templates/index.html`

The page shell: the static markup plus one inline `window.__INITIAL__` script
that Flask fills with the starting holdings, revision and storage status. Styles
and behaviour live in `static/` and are loaded with plain `<link>` and
`<script>` tags, so the browser needs no build step, npm, CDN, or framework.

### `static/app.css` and `static/app.js`

- `app.css` — all dashboard styling (layout, tables, cards, insight popup) and
  the narrow-screen rules: sticky table header, pinned stock column, and
  reflowed controls.
- `app.js` — table rendering, import parsing, analysis, insight, search,
  sorting, cross-window synchronization, the first-run onboarding panel, and
  the fetch-error banner with retry/debug-copy actions.
- Both are served by Flask's built-in static route; `Cache-Control: no-store`
  applies to every response, so edits show up on reload.

## Data model

A holding:

```json
{
  "ticker": "INFY",
  "name": "Infosys",
  "shares": 10,
  "quarters": {"2026-Q1": 10, "2026-Q2": 4},
  "quarterUpdatedAt": {"2026-Q1": "2026-04-01T10:00:00+00:00"},
  "dividends": [{"date": "2026-05-01", "amount": 2.5}],
  "yearTotals": {"2026": 2.5},
  "price": 1500.0,
  "status": "ok",
  "lastError": null,
  "lastFetched": "2026-05-02T08:00:00+00:00",
  "updatedAt": "2026-05-02T08:00:00+00:00"
}
```

Key rules:

- `quarters` is keyed by `YYYY-Qn`. A missing key means zero, not "inherit
  shares" and not "use the previous quarter".
- `quarterUpdatedAt` records explicit manual edits so two windows changing
  different quarters both survive a merge.
- `dividends` is the full per-share history returned by the upstream source.
- `yearTotals` is the per-share total for each year and is recomputed on write.
- `updatedAt` drives last-writer-wins merging for non-quarter fields.

A payout record contains `ticker`, `name`, `date`, `year`, `quarter`,
`quarter_key`, `amount`, `quantity`, and `income`. Income is
`amount × quantity in that payout's quarter`, rounded to two decimals.

## Write path

1. The browser sends `PUT /api/portfolio/<ticker>` or a bulk import.
2. `db.normalize_holding()` validates and canonicalizes the payload.
3. `db.save_holding()` checks tombstones, loads the existing record, and merges.
4. `_save_holding_locked()` upserts the holding, rebuilds that ticker's payout
   records, bumps the revision, and rewrites the JSON mirror.
5. The response includes the saved holding, the new revision, and storage info.

All writes are serialized by the module lock, so concurrent Flask threads cannot
interleave a read-modify-write sequence.

## Delete safety

Deleting a stock must stay deleted, even when another window is mid-refresh.
The design has three layers:

1. **Server tombstone** — `delete_holding()` writes the ticker and a deletion
   timestamp into `meta/tombstones`.
2. **Stale-write rejection** — `save_holding()` compares the incoming
   `updatedAt` with the tombstone. If it is not newer, it returns `None` and the
   API responds with `ignored: true`.
3. **Client tombstone** — the browser records the deletion before sending the
   request, cancels pending sync timers, and rejects in-flight fetch results for
   that ticker.

A deliberate re-add sends a newer `updatedAt`, which clears the tombstone.
Bulk import also clears tombstones because it is an explicit user action.

## Cross-window synchronization

Every write increments `meta/revision`. The browser polls `/api/revision` every
few seconds. When the revision changes, it fetches the portfolio and merges
remote state:

- local edits in the focused quantity input are deferred until focus leaves;
- deleted tickers are ignored;
- stale responses are discarded by comparing revisions and object identity;
- the local cache in `localStorage` is updated.

This is last-writer-wins with per-quarter timestamps, not a full CRDT.

## Year rollover

The UI derives available years from dividend dates plus the current calendar
year. The next year is deliberately not shown before 1 January.

When the calendar year rolls over, the new year inherits the quantity held in
the previous year (2027 inherits from 2026, 2028 from 2027, and so on) while
stored quantities for earlier years are never touched.

Quarter quantities for a year that was never rolled into stay blank and
contribute zero income until the user enters a quantity.

The starting quantity in the add form is applied once, only to quarters present
in the fetched dividend history. It is a convenience estimate, not a rollover
rule.

## Analysis

`GET /api/analysis?year=YYYY|all` reads payout metadata from ChromaDB and
aggregates:

- total income and payout count;
- income by year (always all years);
- income by stock, share of total, and TTM yield;
- income by quarter;
- the five largest payouts.

Analysis does not recompute from the browser's table; it reads the indexed
payout records, which are rebuilt whenever a holding changes.

For a specific calendar year, analysis also returns `projections`:

- **conservative** — average quarterly quantity × flat total dividend per share;
- **base** — peak quarterly quantity × flat dividend (same as
  `projectedNextYearIncome`);
- **growth** — peak quantity × dividend grown by per-share CAGR (capped at ±15%;
  flat dividend when fewer than three years of payout history).

The all-years view omits `projections` and sets `projectedNextYearIncome` to
`null`.

For a specific calendar year, `incomeBridge` explains the move from the
previous year (`quantity`, `dps`, `composition`), and `growthByTicker` carries
`dpsYoyPct`, `dpsCagrPct` and a short `dpsTrend` series for the UI.

`GET /api/analysis?year=fy:2025` filters payouts from 1 April 2025 through
31 March 2026. Calendar-only features (bridge, growth columns, projections) are
omitted for FY requests. Every scoped analysis includes `cashflowByMonth`
(payment month × income).

## Insight

`GET /api/insight?year=YYYY|all` builds a descriptive score of dividend health
from the same holdings and analysis data. It is read-only: the route never
writes, bumps the revision, or changes the JSON mirror.

The response contains:

- `scores` — overall 0–100, band (`strong` / `steady` / `uneven` / `fragile` /
  `unknown`), confidence, component scores with weights, and deferred signals;
- `narrative` — headline, summary, strengths, risks, watchlist and caveats;
- `stats` — income, payouts, yield, concentration, cuts and coverage counts;
- `charts` — income by year, income concentration, and per-holding rows;
- `method` — weights, formula notes and inputs, so the UI can explain itself.

Component scores are renormalised over the components that have data. The
income component weights holdings by their share of income and penalises
dividend cuts; diversification is capped by the number of income sources so a
two-stock portfolio cannot score as fully diversified. A past-year view
truncates each dividend series at that year, so a later cut cannot appear
retroactively. The overall score is withheld when the selected period has no
recorded income; the components remain visible so the popup can explain the
gap. Signals that need price history or external data are declared as deferred
rather than scored as zero.

## Coach

`GET /api/coach?year=YYYY|all` builds a read-only portfolio briefing from local
holdings and the same analysis aggregates used by insight. Nothing is written,
bumped or mirrored.

The response contract (stable across releases):

- `briefing` — `headline` and `summary` for the selected period;
- `tasks` — prioritized action items (`p0` / `p1` / `p2`);
- `events` — dividend payouts and payment-pattern hints for the selected period;
- `counts` — `total`, `p0`, `p1`, `p2` tallies for the task list;
- `ruleset` — `{ id, version }` so clients know which rule engine produced the payload;
- `disclaimer` — same descriptive-not-advice wording as insight.

Each task, when populated, uses:

```json
{
  "id": "unique-stable-id",
  "priority": "p0",
  "type": "missing-quantity",
  "ticker": "TCS",
  "year": 2026,
  "quarter": "2026-Q2",
  "message": "Enter Q2 quantity for TCS",
  "action": "focus-quarter"
}
```

`action` is a UI hint (`focus-quarter`, `refresh`, `open-insight`, …), not a
server-side command. Events use `{ "id", "type", "ticker", "message", "at" }`
with ISO-8601 timestamps where applicable.

## Search

`GET /api/search?q=...&limit=N` embeds the query with the same deterministic
embedding function used for payout documents and asks ChromaDB for nearest
neighbors. It is semantic-ish keyword search, not an LLM. Results include a
distance-derived score.

## API summary

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Health plus storage status |
| `GET` | `/api/revision` | Revision and counts for polling |
| `GET` | `/api/portfolio` | All holdings, revision, storage info |
| `PUT` | `/api/portfolio/<ticker>` | Upsert one holding |
| `DELETE` | `/api/portfolio/<ticker>` | Delete and tombstone one holding |
| `POST` | `/api/portfolio/import` | Bulk upsert |
| `GET` | `/api/dividends?symbol=...` | Upstream dividend data |
| `GET` | `/api/analysis?year=...` | Income analysis |
| `GET` | `/api/insight?year=...` | Read-only portfolio scores, narrative and charts |
| `GET` | `/api/coach?year=...` | Read-only briefing, tasks and events (ruleset expands over time) |
| `GET` | `/api/search?q=...` | Semantic payout search |
| `GET` | `/api/storage` | ChromaDB status |

Unknown `/api/*` routes return JSON 404s so the browser never receives an HTML
error page.

## Failure handling

- **Upstream failure:** the row keeps its stored data, is marked `error`, and
  shows the message. Other rows still refresh.
- **ChromaDB unavailable at startup:** the server exits with installation and
  diagnostic instructions.
- **ChromaDB failure during a request:** the API returns `503` with a JSON error
  and `backend: chromadb`.
- **Backup write failure:** logged, but does not fail the save. ChromaDB remains
  authoritative.
- **Invalid input:** rejected with `400`; bulk import skips invalid rows and
  reports them.

## Tests

- `tests/test_market.py` — parsing and cache behavior, with no network.
- `tests/test_db.py` — normalization, merge, tombstones, analysis, backup
  restore.
- `tests/test_server.py` — Flask routes and error responses.
- `scripts/smoke_test.py` — real subprocess, real ChromaDB, temporary storage,
  full add/read/analyze/delete cycle.

Every test uses a temporary database and data directory. CI runs the suite on
Linux (Python 3.11–3.13), Windows, and macOS.
