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

## The dashboard

The four cards at the top summarize the selected year:

| Card | Meaning |
| --- | --- |
| **Holdings** | Number of stocks in the tracker. |
| **Annual income** | Sum of `dividend per share × quantity` for every payout in the year. |
| **Monthly income** | Annual income ÷ 12. This is an average, not a payment schedule. |
| **Avg. yield (TTM)** | Average trailing-12-month dividend yield across stocks with a known price. |

The **Year** selector controls the table and the income cards. Choose
**All years** to see total income and total dividend per share across all
recorded history. Quarter columns are hidden in the all-years view.

A new calendar year appears automatically on 1 January. It starts with blank
quantities; earlier years are never changed.

## Add stocks

1. Type one or more NSE tickers in the add box, separated by commas:
   `RELIANCE, TCS, HDFCBANK`.
2. Set the starting quantity (default `1`).
3. Click **＋ Add**.

The app fetches the company name, current price, and dividend history. A
green dot means the refresh succeeded; red means it failed and the error is in
the row's tooltip.

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

### Why does a stock show a red dot?

The last upstream fetch failed. Hover the dot to see the error. The stock and
your quantities remain saved. Try **Refresh** later.

### Why is a new year empty?

Quarter quantities are deliberately not inherited across years. Enter the
quantity you held in each quarter of the new year. This prevents old holdings
from inflating the new year's income.

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

The web UI is responsive, but the server must run on a computer that can reach
stockanalysis.com. Exposing it to your local network is possible with
`HOST=0.0.0.0`, but there is no authentication. Use a trusted network only.
