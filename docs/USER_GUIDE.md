# User guide

This guide walks through the NSE Dividend Tracker interface.

## Start the app

```bash
./start.sh          # macOS / Linux
start.bat           # Windows
python run.py       # any platform
```

Open <http://localhost:8901/Dividend%20tracker>. The status pill in the header
shows whether the local ChromaDB connection is healthy.

## First run

A fresh tracker shows a short onboarding panel instead of an empty table. It
explains the three things you need to know:

1. **How to add a stock** — type an NSE symbol in the add box (or click one of
   the example chips) and set the starting quarterly quantity.
2. **Where the data comes from** — prices, yields and dividend history are
   fetched live from stockanalysis.com and cached for six hours.
3. **Where your data lives** — holdings stay in a local ChromaDB database with a
   JSON mirror on this computer. There is no account or cloud sync; only
   dividend lookups leave the machine.

The panel disappears as soon as the first stock is added.

## The dashboard

The five cards at the top summarize the selected year:

| Card | Meaning |
| --- | --- |
| **Holdings** | Number of stocks in the tracker. |
| **Annual income** | Sum of `dividend per share × quantity` for every payout in the year. |
| **Next year income (base)** | Base scenario: each stock's highest quarterly quantity × total dividend per share for the selected year. **Low** and **Growth** chips show conservative and CAGR-adjusted bands. The badge is the change versus the selected year's annual income. |
| **Monthly income** | Annual income ÷ 12. This is an average, not a payment schedule. |
| **Avg. yield (TTM)** | Average trailing-12-month dividend yield across stocks with a known price. |

The **Year** selector controls the table and the income cards. Choose
**All years** to see total income and total dividend per share across all
recorded history. Quarter columns are hidden in the all-years view.

**FY 2025-26** (and similar options) use India's April–March financial year:
income and Analysis totals follow **payout dates** in that window, not calendar
year totals. Quarterly quantity cells are hidden in FY view; use a calendar year
to edit quantities.

**Analysis** includes a **Monthly cashflow** section (payment month × income)
for the selected calendar year or FY.

A new calendar year appears automatically on 1 January. It starts with blank
quantities; earlier years are never changed.

## Add stocks

1. Type one or more NSE tickers in the add box, separated by commas:
   `RELIANCE, TCS, HDFCBANK`.
2. Set the starting quantity (default `1`).
3. Click **＋ Add**.

The app fetches the company name, current price, and dividend history. A
green dot means the refresh succeeded; a red dot plus a ⚠ marker means it
failed. The error banner above the table explains what went wrong and offers
**Retry** and **Copy debug info**.

Ticker validation is forgiving: commas and spaces separate multiple symbols,
`.NS` is removed, and invalid symbols are skipped with a clear message. Use the
NSE trading symbol (`RELIANCE`), not the company name.

The starting quantity is applied **once** to every quarter in the fetched
dividend history where a dividend was paid. That gives you an immediate
estimate. Replace those values with the quantity you actually held in each
quarter. Missing cells count as zero, and future quarters are never seeded
from an older year.

You can also paste a Yahoo-style ticker such as `TCS.NS`; the `.NS` suffix is
removed automatically.

## Enter quarterly quantities

Select a specific year. Each stock has four quantity cells: Q1, Q2, Q3, Q4.

- A quarter is January–March (Q1), April–June (Q2), July–September (Q3), or
  October–December (Q4).
- The small hint under each cell shows the total dividend per share paid in
  that quarter.
- Income for a payout is calculated with the quantity in the **payout's**
  quarter, not today's holding.
- Edits save automatically as you type and are persisted when you leave the
  cell.
- Blank means zero. Zero is a valid value and is stored explicitly.

The **TOTAL DIVIDEND** column is per share and ignores quantity. **Annual
income** applies your quarterly quantities to those same payouts.

### Clear a year

Select a year and click **🧹 Clear year** to set every saved quantity for that
year to `0`. This is useful after copying a spreadsheet with the wrong values.
It asks for confirmation and cannot be undone. The all-years view cannot be
cleared.

## Sort the table

Click any column heading to sort; click again to reverse. Stock, income, total
dividend, yield, and quarter quantities are sortable. Unknown yields always
sort last.

## Refresh data

- **🔄 Refresh All** re-fetches dividend data for every stock.
- The **🔄** button on a row refreshes one stock.
- **📜** opens the full dividend history for that stock.

Upstream responses are cached for six hours by default. Change
`DIVIDEND_CACHE_TTL` in `.env` if you need fresher data. Refreshing updates
names, prices, and dividend history; it does not overwrite your quantities.

If a fetch fails, an error banner explains whether the problem is the ticker,
a missing dividend history, the network, rate limiting, or the upstream site.
Use **Retry** to try again, or **Copy debug info** to copy the ticker, error
code, message, storage status and browser details for troubleshooting.

## Bulk import

Click **📥 Import** to load quarterly quantities from a spreadsheet.

### Format

One row per stock:

```text
SHARE NAME    Q1 Qty    Q2 Qty    Q3 Qty    Q4 Qty
INFY          10        10        12        12
ITC           25        25        25        25
```

- Tabs, commas, or spaces are accepted.
- A header row such as `SHARE NAME`, `TICKER`, or `SYMBOL` is skipped.
- `.NS` suffixes are removed.
- Blank quantity cells stay blank.
- Rows whose stock is already in the tracker are skipped; import adds new
  stocks rather than overwriting existing quantities.
- Invalid tickers are reported with their line numbers.

You can paste directly into the box or choose a CSV file. Select the target
**Year** before importing.

The importer uses the ticker in the first column. It also maps the shorthand
`JYOTIRESINS` to `JYOTIRES`.

After import, the app fetches dividend history for the new stocks. If a fetch
fails, the quantities are still saved; use **Refresh** later.

## Analysis

Click **📊 Analysis** to open the analysis panel. Choose a year to see:

- income by year;
- income by stock, including share of the year's income and TTM yield;
- quarterly income bars;
- the largest individual payouts.

Click **↻ Recompute** to rebuild from the local database.

The **Search dividend records** box runs a semantic search over the indexed
payouts. Try queries such as `TCS 2025`, `Reliance dividends`, `highest
income`, or `Q2 payouts`. Results show the ticker, date, quantity, and income.

## Portfolio briefing

Click **📋 Briefing** for a read-only action list built from your local data.
It never calls the network on its own and nothing is written back to storage.

Choose a year (or **All years**) to see:

- a short headline and summary;
- **tasks** sorted by urgency (**P0** missing quantities and bad refreshes first);
- recent **dividend context** (payouts and typical payment months).

The red badge on the button counts open **P0** items for the dashboard year.
Task buttons can jump to a quarter cell, refresh a ticker, or open **Analysis**
or **Insight**.

## Portfolio insight

Click **🧠 Insight** for a scored summary of the portfolio's dividend health.
It is computed on demand from data already in the tracker: no network request,
no language model, and nothing is written back to ChromaDB or to disk.

Choose a year (or **All years**) and move between four tabs:

| Tab | Shows |
| --- | --- |
| **Overview** | Overall 0–100 score, confidence, component bars, and plain-language strengths, risks and watchlist. |
| **Income** | Total income, payouts, average yield, projected next year income, income by year, and a concentration donut. |
| **Holdings** | Per-stock income share, score, stability, dividend CAGR, payment streak, a five-year trend, and flags such as `cut`. |
| **Method** | Weights, formula notes, inputs, deferred signals, and the disclaimer. |

The overall score blends three components, renormalised over whichever ones are
available:

- **Income resilience** (40%) — stability of each stock's annual dividend, how
  many consecutive years it has paid, and a penalty for cuts larger than 15%.
- **Dividend growth** (30%) — income-weighted dividend CAGR, blended with the
  projected next-year change.
- **Diversification** (30%) — how evenly income is spread, capped by the number
  of income sources and penalised when the top three dominate.
- **Yield vs own history** (15%, when data allows) — today's yield compared with
  the median from refresh snapshots (needs at least five stored snapshots per
  holding).

Scores describe the recorded data; they are not forecasts, ratings, or advice.
Selecting a past year scores only the dividend history up to that year. If a
period has no recorded income, the overall score is withheld and the popup
explains what is missing.

Signals that need price history or external data (valuation, technicals,
young-consumer exposure) are listed as deferred instead of being scored as
zero. Click **↻ Recompute** after editing holdings.

## Delete a stock

Click the **🗑** button on a row. The stock is removed from the tracker and
from the local database.

Deletes are protected across windows:

- The delete is written as a tombstone before the row disappears.
- A refresh that was already in flight cannot re-save the old row.
- Another browser tab that still has the stock cannot resurrect it with a stale
  copy.
- Re-adding the ticker later creates a genuinely new holding and clears the
  tombstone.

Deleting a stock does not affect your broker account or any external service.

## Multiple windows and tabs

You can keep the tracker open in more than one tab. The app polls a revision
counter and merges changes:

- a quantity edited in one tab appears in the other;
- a delete in one tab removes the row in the other;
- a stale tab cannot overwrite a newer edit or undo a delete.

If you are typing in a quantity cell when a remote change arrives, the update
is applied after you leave the cell.

## Backup and restore

The app writes a JSON mirror to `data/portfolio.json` after every change. The
authoritative store is `chroma_db/`.

To back up safely:

1. Stop the server.
2. Copy `data/` and `chroma_db/` to another location.
3. Restart.

To restore, stop the server, replace both directories, and start again. If
ChromaDB is empty but `data/portfolio.json` exists, the app restores the
holdings automatically on startup.

See [CHROMADB.md](CHROMADB.md) for details and reset instructions.

## FAQ

### Why is monthly income blank in the all-years view?

Monthly income is only meaningful for a specific year. Select a year to see the
average monthly figure.

### How is projected next year income calculated?

For a selected year the app shows three scenarios (see also **Analysis** and
**Insight → Income**):

| Scenario | Rule |
| --- | --- |
| **Low (conservative)** | `Σ` average of Q1–Q4 quantities × total dividend per share for the year |
| **Base** | `Σ max(Q1, Q2, Q3, Q4) × total dividend` — headline card and `projectedNextYearIncome` |
| **Growth** | Peak quantity × dividend grown by historical per-share CAGR (capped at ±15% a year; flat dividend when fewer than three years of history) |

All scenarios assume you hold the stated quantity for the whole next year.
The percentage badge on the base card compares that scenario with the selected
year's annual income. Select a specific year to see projections; the all-years
view has no single base year.

### Why does a stock show a red dot?

The last upstream fetch failed. The error banner above the table explains why,
and the row's tooltip has the same message. The stock and your quantities remain
saved; use **Retry** in the banner or the row's **🔄** button later.

### Why is a new year empty?

Quarter quantities are not inherited for years that never rolled over. When a
new calendar year is added, the tracker copies the quantity held in the previous
year into it (2027 inherits from 2026, 2028 from 2027, and so on), so the new
year starts with the shares you held last year rather than zero. Enter the
quantity you actually held in each quarter of the new year if it differs. This
prevents old holdings from inflating the new year's income while still giving a
sensible starting point.

### Does the app place trades or connect to my broker?

No. It only reads public dividend data and stores your manual quantities. It has
no brokerage integration.

### Can I use it for non-NSE stocks?

The data source and ticker validation are NSE-specific. Other exchanges are not
supported.

### Is my portfolio uploaded anywhere?

No. Your holdings and quantities stay in local files. The only outbound
requests are dividend-page fetches from stockanalysis.com. ChromaDB telemetry
is disabled by default.

### Can I run it on a phone?

The web UI is responsive. On a narrow screen the summary cards stack, the
controls wrap, and the table scrolls sideways with the stock column pinned and
the header stuck to the top; a swipe hint appears above the table. The server
must still run on a computer that can reach stockanalysis.com. Exposing it to
your local network is possible with `HOST=0.0.0.0`, but there is no
authentication. Use a trusted network only.
