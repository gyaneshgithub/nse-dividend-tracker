# ChromaDB setup and storage

The NSE Dividend Tracker uses **embedded ChromaDB**. Chroma runs inside the
Python process and persists to a local directory. You do not need to install a
database server, start a service, or open a port.

## Installation

ChromaDB is a pinned runtime dependency:

```text
chromadb==1.5.9
```

`python run.py` installs it automatically on first run. For a manual setup:

```bash
python -m pip install -r requirements.txt
```

Verify it:

```bash
python scripts/check_setup.py
```

The check creates a throwaway database in a temporary directory, so it does not
touch your real portfolio.

## How the database is created

On first use, `db.init_storage()`:

1. creates the database directory (`chroma_db/` by default);
2. opens a `PersistentClient`;
3. creates three collections if they do not exist;
4. restores holdings from `data/portfolio.json` when the database is empty;
5. backfills per-year dividend totals for older databases;
6. ensures the revision counter is non-zero.

The directory is created automatically. You do not need to run a schema
migration by hand.

## Collections

| Collection | Contents |
| --- | --- |
| `holdings` | One record per stock, keyed by ticker. Stores quantities, dividend history, price, status, and timestamps as metadata plus a searchable document. |
| `payouts` | One record per dividend payment, keyed by `TICKER:date:index`. Stores year, quarter, amount, quantity, and income. Used for analysis and semantic search. |
| `meta` | Two records: `revision` (monotonic write counter) and `tombstones` (deleted tickers with deletion timestamps). |

The app supplies its own deterministic embeddings, so ChromaDB does not need to
download an embedding model. This keeps first-run setup offline-friendly and
fast.

### Why both ChromaDB and JSON?

ChromaDB is the authoritative store and supports search. `data/portfolio.json`
is a human-readable mirror that makes recovery simple: if ChromaDB is empty, the
app restores from the JSON file. The mirror is rewritten after every successful
write.

## Paths

Defaults:

```text
chroma_db/              ChromaDB
data/portfolio.json     JSON mirror
```

Override them in `.env`:

```dotenv
DIVIDEND_DB_DIR=./chroma_db
DIVIDEND_DATA_DIR=./data
```

Relative paths resolve from the project root, not the current shell directory.
Absolute paths are used as-is:

```dotenv
DIVIDEND_DB_DIR=/Volumes/Backup/dividend-chroma
DIVIDEND_DATA_DIR=/Volumes/Backup/dividend-data
```

Keep both directories on a local disk. Network shares and cloud-sync folders
can cause locking or corruption with embedded databases.

## Backup

Stop the server first so ChromaDB is not writing.

```bash
# macOS / Linux
cp -R data /path/to/backup/dividend-data
cp -R chroma_db /path/to/backup/dividend-chroma
```

```bat
:: Windows
xcopy data C:\backup\dividend-data /E /I
xcopy chroma_db C:\backup\dividend-chroma /E /I
```

Back up both. The JSON mirror alone can restore holdings, but ChromaDB also
contains the revision and tombstone state.

A simple schedule is enough: copy both directories after you finish editing for
the day.

## Restore

1. Stop the server.
2. Replace `data/` and `chroma_db/` with the backup copies.
3. Start the server.

If you only have `data/portfolio.json`:

1. Stop the server.
2. Delete or rename `chroma_db/`.
3. Start the server. It creates a fresh database and restores the JSON mirror.

Tombstones are not in the JSON mirror. After a JSON-only restore, a previously
deleted ticker could be re-added by an old browser tab. Close other tabs before
restoring this way.

## Move to another machine

Copy the whole project **without** `.venv/`, then copy `data/` and `chroma_db/`
separately. On the new machine:

```bash
python run.py
```

The launcher builds a fresh virtual environment for that operating system.
ChromaDB files are portable across platforms for the same ChromaDB version.

## Reset the database

> This permanently deletes all holdings and quantities.

1. Stop the server.
2. Back up `data/portfolio.json` if you might want it later.
3. Delete `chroma_db/` (and optionally `data/portfolio.json`).
4. Start the server.

```bash
rm -rf chroma_db data/portfolio.json      # macOS / Linux
```

```bat
rmdir /s /q chroma_db
del data\portfolio.json
```

If you keep `data/portfolio.json`, the next start restores it into the new
database.

## Privacy and telemetry

ChromaDB may collect anonymized telemetry by default. This project sets:

```dotenv
ANONYMIZED_TELEMETRY=False
```

The setting is applied in `db.py` and `server.py`, and CI uses the same value.
You can opt in by changing it, but the project does not need telemetry.

Your holdings never leave your machine. The only network calls are dividend
page fetches from stockanalysis.com.

## Inspecting the database

With the server stopped:

```bash
python - <<'PY'
import chromadb

client = chromadb.PersistentClient(path="chroma_db")
for name in ("holdings", "payouts", "meta"):
    collection = client.get_or_create_collection(name)
    print(name, collection.count())
PY
```

Do not edit records by hand. Use the app's API so the revision counter, payout
index, and JSON mirror stay consistent.

## Troubleshooting

### `ChromaDB storage unavailable`

- Install dependencies: `python -m pip install -r requirements.txt`.
- Run `python scripts/check_setup.py`.
- Check that `DIVIDEND_DB_DIR` is a writable directory, not a file.
- Make sure another process is not using the same database.

### `database is locked`

Two processes are using the same `chroma_db/`. Stop the native app before
starting the Docker container (or vice versa). Do not run two servers against
one database.

### Slow first start

The first ChromaDB import can take a few seconds while native libraries load.
Later starts are faster. A very large payout history also takes longer to index.

### Rebuilding after a schema change

This project does not require manual migrations. If you switch between
incompatible ChromaDB versions, restore from `data/portfolio.json` into a fresh
`chroma_db/` directory.

### Linux: `libgomp.so.1` missing

Install the OpenMP runtime used by `onnxruntime`:

```bash
sudo apt-get update && sudo apt-get install -y libgomp1
```

The provided Docker image already includes it.
