#!/usr/bin/env python3
"""ChromaDB persistence + analysis for the Dividend Tracker.

Storage model
-------------
holdings : one record per stock (quarters, dividends, per-year dividend totals,
           price, metadata)
payouts  : one record per dividend payment, enriched with the quantity held
           in that quarter and the resulting income. This collection powers
           the analysis tables and the semantic search.

A JSON mirror is written to data/portfolio.json after every change and is
used to restore Chroma automatically if the vector store is ever empty.

Embeddings are deterministic local hashing vectors (384 dimensions), so no
model download or network access is needed for search.
"""

import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone

# Keep telemetry opt-out effective even when server.py is launched directly.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

try:
    import chromadb
except ImportError:  # pragma: no cover - handled gracefully by the server
    chromadb = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_path(name, default):
    """Resolve a configurable storage path relative to the project directory."""
    value = os.path.expanduser(os.environ.get(name) or default)
    if not os.path.isabs(value):
        value = os.path.join(BASE_DIR, value)
    return os.path.abspath(value)


DB_DIR = _env_path("DIVIDEND_DB_DIR", "chroma_db")
DATA_DIR = _env_path("DIVIDEND_DATA_DIR", "data")
BACKUP_FILE = os.path.join(DATA_DIR, "portfolio.json")
EMBED_DIM = 384
SYMBOL_RE = re.compile(r"^(?=.{2,15}$)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_lock = threading.RLock()
_client = None
_holdings_col = None
_payouts_col = None
_meta_col = None


def reset_connection():
    """Drop cached Chroma clients and collections.

    Used by tests and setup checks that point the module at a temporary
    database directory. Production code normally keeps one client for the
    lifetime of the process.
    """
    global _client, _holdings_col, _payouts_col, _meta_col
    with _lock:
        if _client is not None:
            try:
                # Release SQLite handles immediately; Windows otherwise keeps
                # temporary test directories locked after the test finishes.
                _client.close()
            except Exception:
                pass
        _client = None
        _holdings_col = None
        _payouts_col = None
        _meta_col = None


class StorageUnavailable(RuntimeError):
    """Raised when ChromaDB is not installed or cannot be opened."""


# ---------------------------------------------------------------------------
# embeddings (local, deterministic, no downloads)
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _embed(text):
    vec = [0.0] * EMBED_DIM
    for token in _TOKEN_RE.findall(str(text).lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        idx = h % EMBED_DIM
        vec[idx] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# normalisation helpers
# ---------------------------------------------------------------------------
def quarter_key(date):
    return f"{int(date[:4])}-Q{(int(date[5:7]) - 1) // 3 + 1}"


def _normalize_quarters(raw):
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key = str(key)
            if not QUARTER_RE.fullmatch(key):
                continue
            try:
                out[key] = max(0, int(float(value)))
            except (TypeError, ValueError):
                continue
    return out


def _normalize_dividends(raw):
    out = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            date = str(item.get("date") or "")
            try:
                amount = float(item.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            if DATE_RE.fullmatch(date) and amount > 0:
                out.append({"date": date, "amount": round(amount, 4)})
    out.sort(key=lambda d: d["date"])
    return out


def _year_totals(dividends):
    """Total dividend per share for each calendar year.

    This is the Q1+Q2+Q3+Q4 sum of the per-share dividend amounts. It is
    independent of the quantity held, unlike the income calculation.
    """
    totals = {}
    for dividend in dividends:
        year = dividend["date"][:4]
        totals[year] = round(totals.get(year, 0.0) + dividend["amount"], 4)
    return dict(sorted(totals.items()))


def _normalize_quarter_times(raw):
    """Timestamps for quantities that were changed by hand.

    Only explicit edits are recorded. A quarter without a timestamp keeps the
    value that the holding-level updatedAt merge selected, which means a
    manual edit is never lost just because another window refreshed prices.
    """
    out = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key = str(key)
            if QUARTER_RE.fullmatch(key) and value:
                out[key] = str(value)[:40]
    return out


def normalize_holding(raw):
    if not isinstance(raw, dict):
        raise ValueError("Holding must be a JSON object")
    ticker = str(raw.get("ticker") or "").strip().upper()
    if not SYMBOL_RE.fullmatch(ticker):
        raise ValueError("Invalid NSE ticker")
    try:
        shares = max(0, int(float(raw.get("shares") or 0)))
    except (TypeError, ValueError):
        shares = 0
    price = raw.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    dividends = _normalize_dividends(raw.get("dividends"))
    return {
        "ticker": ticker,
        "name": str(raw.get("name") or ticker)[:200],
        "shares": shares,
        "quarters": _normalize_quarters(raw.get("quarters")),
        "quarterUpdatedAt": _normalize_quarter_times(raw.get("quarterUpdatedAt")),
        "dividends": dividends,
        "yearTotals": _year_totals(dividends),
        "price": price,
        "status": str(raw.get("status") or "ok")[:20],
        "lastError": str(raw.get("lastError") or "")[:500] or None,
        "lastFetched": str(raw.get("lastFetched") or "")[:40] or None,
        "updatedAt": str(raw.get("updatedAt") or "")[:40]
        or datetime.now(timezone.utc).isoformat(),
    }


def _parse_time(value):
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def _quarter_time(holding, key):
    raw = (holding.get("quarterUpdatedAt") or {}).get(key)
    return _parse_time(raw) if raw else None


def _merge_holding(existing, incoming):
    """Merge a write with the stored record without losing manual edits.

    Non-quarter fields follow the holding-level updatedAt (last writer wins).
    Quarter quantities use their own timestamp when a human edited them, so
    two windows changing different quarters both keep their changes.
    """
    existing = normalize_holding(existing)
    incoming = normalize_holding(incoming)
    existing_time = _parse_time(existing.get("updatedAt"))
    incoming_time = _parse_time(incoming.get("updatedAt"))
    if incoming_time >= existing_time:
        winner, loser = incoming, existing
    else:
        winner, loser = existing, incoming

    merged = dict(loser)
    merged.update(winner)

    quarters = {}
    quarter_times = {}
    for key in sorted(set(existing["quarters"]) | set(incoming["quarters"])):
        existing_q_time = _quarter_time(existing, key)
        incoming_q_time = _quarter_time(incoming, key)
        if existing_q_time is not None and incoming_q_time is not None:
            picked = incoming if incoming_q_time >= existing_q_time else existing
        elif existing_q_time is not None:
            picked = existing
        elif incoming_q_time is not None:
            picked = incoming
        else:
            picked = winner if key in winner["quarters"] else loser

        if key in picked["quarters"]:
            quarters[key] = picked["quarters"][key]
        elif key in winner["quarters"]:
            quarters[key] = winner["quarters"][key]
        elif key in loser["quarters"]:
            quarters[key] = loser["quarters"][key]

        if existing_q_time is not None or incoming_q_time is not None:
            if existing_q_time is not None and (
                incoming_q_time is None or existing_q_time >= incoming_q_time
            ):
                quarter_times[key] = existing["quarterUpdatedAt"][key]
            else:
                quarter_times[key] = incoming["quarterUpdatedAt"][key]

    merged["quarters"] = quarters
    merged["quarterUpdatedAt"] = quarter_times

    # An empty list (for example a just-added, still-pending stock) must not
    # erase a dividend history that is already stored.
    if not incoming["dividends"] and existing["dividends"]:
        merged["dividends"] = existing["dividends"]
    elif not existing["dividends"] and incoming["dividends"]:
        merged["dividends"] = incoming["dividends"]
    merged["yearTotals"] = _year_totals(merged["dividends"])

    merged["updatedAt"] = max(
        (holding["updatedAt"] for holding in (existing, incoming) if holding.get("updatedAt")),
        key=_parse_time,
        default=datetime.now(timezone.utc).isoformat(),
    )
    return merged


def _qty_for_quarter(holding, key):
    value = holding["quarters"].get(key)
    if value is not None:
        return max(0, int(value))
    # Missing means no quantity was recorded for this specific year/quarter.
    # Never inherit the base shares or another quarter's value.
    return 0


def _yield_pct(holding):
    price = holding.get("price")
    if not price:
        return None
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=365)).isoformat()
    ttm = sum(d["amount"] for d in holding.get("dividends", []) if d["date"] >= cutoff)
    if ttm <= 0:
        return None
    return round(ttm / float(price) * 100, 2)


# ---------------------------------------------------------------------------
# Chroma connection
# ---------------------------------------------------------------------------
def _get_collections():
    global _client, _holdings_col, _payouts_col
    if chromadb is None:
        raise StorageUnavailable(
            "ChromaDB is not installed. Run: python -m pip install -r requirements.txt"
        )
    with _lock:
        if _client is None:
            os.makedirs(DB_DIR, exist_ok=True)
            _client = chromadb.PersistentClient(path=DB_DIR)
        if _holdings_col is None:
            _holdings_col = _client.get_or_create_collection(
                "holdings", metadata={"hnsw:space": "cosine"}
            )
        if _payouts_col is None:
            _payouts_col = _client.get_or_create_collection(
                "payouts", metadata={"hnsw:space": "cosine"}
            )
        return _holdings_col, _payouts_col


def _get_meta_col():
    """Tiny collection that stores the portfolio revision counter."""
    global _client, _meta_col
    if chromadb is None:
        raise StorageUnavailable(
            "ChromaDB is not installed. Run: python -m pip install -r requirements.txt"
        )
    with _lock:
        if _client is None:
            os.makedirs(DB_DIR, exist_ok=True)
            _client = chromadb.PersistentClient(path=DB_DIR)
        if _meta_col is None:
            _meta_col = _client.get_or_create_collection("meta")
        return _meta_col


def _read_revision_locked():
    col = _get_meta_col()
    try:
        data = col.get(ids=["revision"], include=["metadatas"])
        metadatas = data.get("metadatas") or []
        if metadatas:
            return max(0, int(metadatas[0].get("value") or 0))
    except Exception as exc:
        print(f"[db] could not read revision: {exc}")
    return 0


def _read_tombstones_locked():
    """Deleted tickers -> deletion timestamp.

    A tombstone makes a delete authoritative across windows: a stale window
    that still has the holding in local storage cannot re-create it with an
    older updatedAt. Re-adding the ticker with a newer timestamp clears it.

    Read failures are fatal: silently treating them as "no tombstones" would
    let a stale write resurrect a deleted holding.
    """
    try:
        col = _get_meta_col()
        data = col.get(ids=["tombstones"], include=["metadatas"])
        metadatas = data.get("metadatas") or []
        if metadatas:
            parsed = json.loads(metadatas[0].get("value") or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("tombstone metadata is not an object")
            return {str(k): str(v) for k, v in parsed.items()}
        return {}
    except Exception as exc:
        raise StorageUnavailable(
            f"Could not read delete tombstones: {exc}"
        ) from exc


def _write_tombstones_locked(tombstones):
    col = _get_meta_col()
    col.upsert(
        ids=["tombstones"],
        metadatas=[{"value": json.dumps(tombstones, separators=(",", ":"))}],
        embeddings=[[1.0]],
    )


def revision():
    """Monotonic counter bumped on every portfolio write."""
    with _lock:
        return _read_revision_locked()


def _bump_revision_locked():
    col = _get_meta_col()
    value = _read_revision_locked() + 1
    # Chroma requires exactly one embeddable field, so a constant vector is
    # supplied to avoid invoking (or downloading) any embedding model.
    col.upsert(ids=["revision"], metadatas=[{"value": value}], embeddings=[[1.0]])
    return value


# ---------------------------------------------------------------------------
# documents / metadata
# ---------------------------------------------------------------------------
def _holding_document(h):
    quarters = ", ".join(f"{k}:{v}" for k, v in sorted(h["quarters"].items())) or "none"
    return (
        f"{h['ticker']} {h['name']} | shares {h['shares']} | "
        f"price {h['price'] or 0} INR | quarters {quarters}"
    )


def _holding_metadata(h):
    return {
        "ticker": h["ticker"],
        "name": h["name"],
        "shares": int(h["shares"]),
        "price": float(h["price"]) if h["price"] is not None else 0.0,
        "status": h["status"],
        "last_fetched": h["lastFetched"] or "",
        "last_error": h["lastError"] or "",
        "updated_at": h["updatedAt"] or datetime.now(timezone.utc).isoformat(),
        "quarters_json": json.dumps(h["quarters"], separators=(",", ":")),
        "quarter_times_json": json.dumps(
            h.get("quarterUpdatedAt") or {}, separators=(",", ":")
        ),
        "dividends_json": json.dumps(h["dividends"], separators=(",", ":")),
        "year_totals_json": json.dumps(
            h.get("yearTotals") or _year_totals(h["dividends"]),
            separators=(",", ":"),
        ),
    }


def _payout_records(h):
    records = []
    for index, dividend in enumerate(h["dividends"]):
        key = quarter_key(dividend["date"])
        qty = _qty_for_quarter(h, key)
        income = round(dividend["amount"] * qty, 2)
        document = (
            f"{h['ticker']} {h['name']} paid a dividend of "
            f"{dividend['amount']:.2f} INR per share on {dividend['date']} "
            f"for {key.replace('-', ' ')}. Quantity held: {qty}. "
            f"Income: {income:.2f} INR."
        )
        metadata = {
            "ticker": h["ticker"],
            "name": h["name"],
            "date": dividend["date"],
            "year": int(dividend["date"][:4]),
            "quarter": key[-2:],
            "quarter_key": key,
            "amount": float(dividend["amount"]),
            "quantity": int(qty),
            "income": float(income),
        }
        records.append((f"{h['ticker']}:{dividend['date']}:{index}", document, metadata))
    return records


# ---------------------------------------------------------------------------
# backup mirror
# ---------------------------------------------------------------------------
def _write_backup():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        payload = {
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "backend": "chromadb",
            "revision": revision(),
            "holdings": load_holdings(),
        }
        tmp = BACKUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, BACKUP_FILE)
    except Exception as exc:  # backup must never break a save
        print(f"[db] backup failed: {exc}")


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------
def _save_holding_locked(raw, write_backup=True, bump_revision=True):
    holding = normalize_holding(raw)
    holdings, payouts = _get_collections()
    document = _holding_document(holding)
    holdings.upsert(
        ids=[holding["ticker"]],
        documents=[document],
        metadatas=[_holding_metadata(holding)],
        embeddings=[_embed(document)],
    )
    # Rebuild this stock's payout index so analysis always matches the
    # latest quantities and dividend history. A failed delete must surface:
    # silently keeping old payout rows corrupts analysis and search.
    payouts.delete(where={"ticker": holding["ticker"]})
    records = _payout_records(holding)
    if records:
        payouts.upsert(
            ids=[r[0] for r in records],
            documents=[r[1] for r in records],
            metadatas=[r[2] for r in records],
            embeddings=[_embed(r[1]) for r in records],
        )
    if bump_revision:
        _bump_revision_locked()
    if write_backup:
        _write_backup()
    return holding


def save_holding(raw):
    with _lock:
        incoming = normalize_holding(raw)
        tombstones = _read_tombstones_locked()
        deleted_at = tombstones.get(incoming["ticker"])
        if deleted_at:
            # Only a genuinely new add (updatedAt after the delete) may
            # revive the ticker. A stale window still holding the old copy
            # must not undo the delete.
            incoming_time = _parse_time(raw.get("updatedAt"))
            if incoming_time <= _parse_time(deleted_at):
                return None
            tombstones.pop(incoming["ticker"], None)
            _write_tombstones_locked(tombstones)
        existing = _load_holding_locked(incoming["ticker"])
        merged = _merge_holding(existing, incoming) if existing else incoming
        return _save_holding_locked(merged)


def import_holdings(items):
    if not isinstance(items, list):
        raise ValueError("holdings must be a list")
    saved = []
    with _lock:
        tombstones = _read_tombstones_locked()
        cleared = False
        for item in items:
            try:
                incoming = normalize_holding(item)
                existing = _load_holding_locked(incoming["ticker"])
                merged = _merge_holding(existing, incoming) if existing else incoming
                saved.append(
                    _save_holding_locked(
                        merged, write_backup=False, bump_revision=False
                    )
                )
                # An explicit bulk import is a deliberate re-add.
                if incoming["ticker"] in tombstones:
                    tombstones.pop(incoming["ticker"], None)
                    cleared = True
            except ValueError as exc:
                print(f"[db] skipped invalid holding: {exc}")
        if cleared:
            _write_tombstones_locked(tombstones)
        if saved:
            _bump_revision_locked()
        _write_backup()
    return saved


def delete_holding(ticker):
    ticker = str(ticker or "").strip().upper()
    if not SYMBOL_RE.fullmatch(ticker):
        raise ValueError("Invalid NSE ticker")
    with _lock:
        # Read the tombstone state before deleting anything, so a storage
        # failure aborts the operation instead of leaving a partial delete.
        tombstones = _read_tombstones_locked()
        holdings, payouts = _get_collections()
        holdings.delete(ids=[ticker])
        payouts.delete(where={"ticker": ticker})
        tombstones[ticker] = datetime.now(timezone.utc).isoformat()
        _write_tombstones_locked(tombstones)
        _bump_revision_locked()
        _write_backup()
    return True


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
def _holding_from_metadata(meta):
    dividends = json.loads(meta.get("dividends_json") or "[]")
    return {
        "ticker": meta["ticker"],
        "name": meta.get("name") or meta["ticker"],
        "shares": int(meta.get("shares") or 0),
        "quarters": json.loads(meta.get("quarters_json") or "{}"),
        "quarterUpdatedAt": json.loads(meta.get("quarter_times_json") or "{}"),
        "dividends": dividends,
        "yearTotals": _year_totals(dividends),
        "price": float(meta["price"]) if meta.get("price") else None,
        "status": meta.get("status") or "ok",
        "lastError": meta.get("last_error") or None,
        "lastFetched": meta.get("last_fetched") or None,
        "updatedAt": meta.get("updated_at") or None,
    }


def _load_holding_locked(ticker):
    holdings, _ = _get_collections()
    # Read failures must propagate. Treating them as "not found" would make
    # the next save replace a record without merging its stored quantities.
    data = holdings.get(ids=[ticker], include=["metadatas"])
    metadatas = data.get("metadatas") or []
    if metadatas:
        return _holding_from_metadata(metadatas[0])
    return None


def load_holdings():
    with _lock:
        holdings, _ = _get_collections()
        if holdings.count() == 0:
            return []
        tombstones = _read_tombstones_locked()
        data = holdings.get(include=["metadatas"])
        out = []
        for meta in data.get("metadatas") or []:
            try:
                holding = _holding_from_metadata(meta)
                if holding["ticker"] in tombstones:
                    continue
                out.append(holding)
            except Exception as exc:
                print(f"[db] skipped unreadable holding: {exc}")
        out.sort(key=lambda h: h["ticker"])
        return out


def _payout_metadatas():
    with _lock:
        _, payouts = _get_collections()
        if payouts.count() == 0:
            return []
        data = payouts.get(include=["metadatas"])
        return data.get("metadatas") or []


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------
GROWTH_CAGR_CAP = 0.15
GROWTH_MIN_YEARS = 3


def _avg_quarter_qty(holding, year):
    total = sum(_qty_for_quarter(holding, f"{year}-Q{q}") for q in (1, 2, 3, 4))
    return total / 4.0


def _peak_quarter_qty(holding, year):
    return max(_qty_for_quarter(holding, f"{year}-Q{q}") for q in (1, 2, 3, 4))


def _dps_series_until(holding, year):
    """Annual dividend per share, oldest first, optionally capped at `year`."""
    totals = holding.get("yearTotals") or {}
    series = []
    for key, value in totals.items():
        try:
            series_year = int(key)
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            series.append((series_year, amount))
    series.sort()
    if year is not None:
        series = [(series_year, amount) for series_year, amount in series if series_year <= year]
    return series


def _dps_cagr(series):
    if len(series) < 2:
        return None
    first_year, first = series[0]
    last_year, last = series[-1]
    span = last_year - first_year
    if span <= 0 or first <= 0:
        return None
    return (last / first) ** (1.0 / span) - 1.0


def _clamp_cagr(cagr):
    if cagr is None:
        return None
    return max(-GROWTH_CAGR_CAP, min(GROWTH_CAGR_CAP, cagr))


def _projected_income_for_holding(holding, year, scenario):
    dps = float((holding.get("yearTotals") or {}).get(str(year)) or 0)
    if scenario == "conservative":
        return _avg_quarter_qty(holding, year) * dps
    if scenario == "base":
        return _peak_quarter_qty(holding, year) * dps
    if scenario == "growth":
        qty = _peak_quarter_qty(holding, year)
        series = _dps_series_until(holding, year)
        cagr = _dps_cagr(series)
        if len(series) >= GROWTH_MIN_YEARS and cagr is not None:
            dps *= 1.0 + _clamp_cagr(cagr)
        return qty * dps
    raise ValueError(f"unknown projection scenario: {scenario}")


def _scenario_income(holdings, year, scenario):
    if year is None:
        return None
    return round(sum(_projected_income_for_holding(h, year, scenario) for h in holdings), 2)


def _projection_increase_pct(projected, total_income):
    if projected is None or total_income <= 0:
        return None
    return round((projected - total_income) / total_income * 100, 2)


def _build_projections(holdings, year, total_income):
    if year is None:
        return None

    def band(scenario):
        income = _scenario_income(holdings, year, scenario)
        return {
            "income": income,
            "increasePct": _projection_increase_pct(income, total_income),
        }

    cap_pct = int(GROWTH_CAGR_CAP * 100)
    return {
        "conservative": band("conservative"),
        "base": band("base"),
        "growth": band("growth"),
        "notes": [
            f"Conservative: average quarterly quantity × {year} total dividend per share.",
            "Base: highest quarterly quantity × same dividend (headline projection).",
            f"Growth: highest quantity × dividend grown by per-share CAGR "
            f"(±{cap_pct}% cap; flat when fewer than {GROWTH_MIN_YEARS} payout years).",
        ],
    }


def _holding_income_for_year(holding, year):
    total = 0.0
    for dividend in holding.get("dividends") or []:
        date = str(dividend.get("date") or "")
        if len(date) < 4:
            continue
        try:
            div_year = int(date[:4])
        except ValueError:
            continue
        if div_year != year:
            continue
        key = quarter_key(date)
        total += float(dividend.get("amount") or 0) * _qty_for_quarter(holding, key)
    return round(total, 2)


def _holding_income_at_prev_quarters(holding, year):
    prev = year - 1
    total = 0.0
    for dividend in holding.get("dividends") or []:
        date = str(dividend.get("date") or "")
        if len(date) < 4:
            continue
        try:
            div_year = int(date[:4])
        except ValueError:
            continue
        if div_year != year:
            continue
        key = quarter_key(date)
        quarter = int(key.split("-Q", 1)[1])
        prev_key = f"{prev}-Q{quarter}"
        total += float(dividend.get("amount") or 0) * _qty_for_quarter(holding, prev_key)
    return round(total, 2)


def _income_bridge(holdings, year):
    if year is None:
        return None
    prev = year - 1
    total_prev = 0.0
    total_curr = 0.0
    qty_effect = 0.0
    dps_effect = 0.0
    composition = 0.0

    for holding in holdings:
        inc_prev = _holding_income_for_year(holding, prev)
        inc_curr = _holding_income_for_year(holding, year)
        total_prev += inc_prev
        total_curr += inc_curr

        if inc_prev <= 0 and inc_curr <= 0:
            continue
        if inc_prev <= 0 < inc_curr:
            composition += inc_curr
            continue
        if inc_prev > 0 >= inc_curr:
            composition -= inc_prev
            continue

        at_prev_qty = _holding_income_at_prev_quarters(holding, year)
        qty_effect += inc_curr - at_prev_qty
        dps_effect += at_prev_qty - inc_prev

    delta = round(total_curr - total_prev, 2)
    return {
        "previousYear": prev,
        "currentYear": year,
        "previousIncome": round(total_prev, 2),
        "currentIncome": round(total_curr, 2),
        "delta": delta,
        "deltaPct": round(delta / total_prev * 100, 2) if total_prev > 0 else None,
        "components": [
            {"key": "quantity", "label": "Share quantities", "amount": round(qty_effect, 2)},
            {"key": "dps", "label": "Dividend per share", "amount": round(dps_effect, 2)},
            {"key": "composition", "label": "New or removed stocks", "amount": round(composition, 2)},
        ],
    }


def _dps_yoy_pct(holding, year):
    totals = holding.get("yearTotals") or {}
    current = float(totals.get(str(year)) or 0)
    previous = float(totals.get(str(year - 1)) or 0)
    if previous <= 0 or current <= 0:
        return None
    return round((current - previous) / previous * 100, 2)


def _dps_cagr_pct(holding, year):
    series = _dps_series_until(holding, year)
    if len(series) < 2:
        return None
    series = series[-5:]
    cagr = _dps_cagr(series)
    if cagr is None:
        return None
    return round(cagr * 100, 2)


def _growth_by_ticker(holdings, year_value):
    if year_value is None:
        return None
    out = {}
    for holding in holdings:
        ticker = holding["ticker"]
        series = _dps_series_until(holding, year_value)[-5:]
        out[ticker] = {
            "dpsYoyPct": _dps_yoy_pct(holding, year_value),
            "dpsCagrPct": _dps_cagr_pct(holding, year_value),
            "dpsTrend": [{"year": y, "amount": amount} for y, amount in series],
        }
    return out


def _parse_analysis_period(year):
    text = str(year or "all").strip()
    lower = text.lower()
    if lower in ("all", "", "none"):
        return {"mode": "all", "label": "all", "calendarYear": None, "fyStart": None}
    if lower.startswith("fy:"):
        try:
            start = int(text.split(":", 1)[1])
        except (TypeError, ValueError):
            return {"mode": "all", "label": "all", "calendarYear": None, "fyStart": None}
        end = start + 1
        return {
            "mode": "fy",
            "label": f"{start}-{str(end)[-2:]}",
            "calendarYear": None,
            "fyStart": start,
        }
    try:
        calendar = int(text)
        return {"mode": "calendar", "label": str(calendar), "calendarYear": calendar, "fyStart": None}
    except (TypeError, ValueError):
        return {"mode": "all", "label": "all", "calendarYear": None, "fyStart": None}


def _date_in_fy(date, fy_start):
    if len(date) < 7:
        return False
    month = int(date[5:7])
    year = int(date[:4])
    if month >= 4:
        return year == fy_start
    return year == fy_start + 1


def _filter_metas(all_metas, period):
    if period["mode"] == "all":
        return all_metas
    if period["mode"] == "calendar":
        year = period["calendarYear"]
        return [m for m in all_metas if int(m.get("year") or 0) == year]
    start = period["fyStart"]
    return [m for m in all_metas if _date_in_fy(str(m.get("date") or ""), start)]


def _cashflow_by_month(metas):
    income = {}
    payouts = {}
    for meta in metas:
        date = str(meta.get("date") or "")
        if len(date) < 7:
            continue
        month = date[:7]
        income[month] = income.get(month, 0.0) + float(meta.get("income") or 0)
        payouts[month] = payouts.get(month, 0) + 1
    return [
        {
            "month": month,
            "income": round(income[month], 2),
            "payouts": payouts[month],
        }
        for month in sorted(income.keys())
    ]


def analysis(year="all"):
    period = _parse_analysis_period(year)
    calendar_year = period["calendarYear"]

    all_metas = _payout_metadatas()
    metas = _filter_metas(all_metas, period)

    total_income = round(sum(float(m.get("income") or 0) for m in metas), 2)

    years_map = {}
    for meta in all_metas:
        y = int(meta.get("year") or 0)
        if not y:
            continue
        record = years_map.setdefault(y, {"year": y, "income": 0.0, "payouts": 0})
        record["income"] += float(meta.get("income") or 0)
        record["payouts"] += 1
    years = sorted(years_map.values(), key=lambda r: r["year"], reverse=True)
    for record in years:
        record["income"] = round(record["income"], 2)

    stock_map = {}
    for meta in metas:
        ticker = str(meta.get("ticker") or "?")
        record = stock_map.setdefault(
            ticker,
            {
                "ticker": ticker,
                "name": meta.get("name") or ticker,
                "income": 0.0,
                "payouts": 0,
            },
        )
        record["income"] += float(meta.get("income") or 0)
        record["payouts"] += 1

    holdings = load_holdings()
    holdings_map = {h["ticker"]: h for h in holdings}
    for record in stock_map.values():
        holding = holdings_map.get(record["ticker"])
        record["yieldPct"] = _yield_pct(holding) if holding else None
        record["income"] = round(record["income"], 2)
        record["sharePct"] = (
            round(record["income"] / total_income * 100, 1) if total_income > 0 else 0.0
        )

    by_stock = sorted(stock_map.values(), key=lambda r: r["income"], reverse=True)

    quarter_map = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    for meta in metas:
        try:
            q = int(str(meta.get("quarter") or "Q0")[1:])
        except (TypeError, ValueError):
            q = 0
        if q in quarter_map:
            quarter_map[q] += float(meta.get("income") or 0)
    by_quarter = [
        {"quarter": f"Q{q}", "income": round(quarter_map[q], 2)} for q in (1, 2, 3, 4)
    ]

    top_metas = sorted(metas, key=lambda m: float(m.get("income") or 0), reverse=True)[:5]
    top_payouts = [
        {
            "ticker": m.get("ticker"),
            "name": m.get("name"),
            "date": m.get("date"),
            "quarter": m.get("quarter_key"),
            "amount": float(m.get("amount") or 0),
            "quantity": int(m.get("quantity") or 0),
            "income": float(m.get("income") or 0),
        }
        for m in top_metas
    ]

    yields = [r["yieldPct"] for r in by_stock if r["yieldPct"] is not None]
    avg_yield = round(sum(yields) / len(yields), 2) if yields else None

    projections = (
        _build_projections(holdings, calendar_year, total_income)
        if period["mode"] == "calendar"
        else None
    )
    projected = None if projections is None else projections["base"]["income"]
    projected_increase = None if projections is None else projections["base"]["increasePct"]

    response_year = "all" if period["mode"] == "all" else (
        calendar_year if period["mode"] == "calendar" else period["label"]
    )

    return {
        "year": response_year,
        "period": period["mode"],
        "fyStart": period["fyStart"],
        "currency": "INR",
        "backend": "chromadb",
        "totalIncome": total_income,
        "projectedNextYearIncome": projected,
        "projectedIncreasePct": projected_increase,
        "projections": projections,
        "incomeBridge": _income_bridge(holdings, calendar_year),
        "growthByTicker": _growth_by_ticker(holdings, calendar_year),
        "cashflowByMonth": _cashflow_by_month(metas),
        "totalPayouts": len(metas),
        "holdingsCount": len(holdings),
        "avgYieldPct": avg_yield,
        "years": years,
        "byStock": by_stock,
        "byQuarter": by_quarter,
        "topPayouts": top_payouts,
    }


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def search_payouts(query, limit=10):
    text = str(query or "").strip()
    if not text:
        return []
    try:
        limit = max(1, min(int(limit or 10), 25))
    except (TypeError, ValueError):
        limit = 10

    with _lock:
        _, payouts = _get_collections()
        if payouts.count() == 0:
            return []
        try:
            result = payouts.query(
                query_embeddings=[_embed(text)],
                n_results=limit,
                include=["metadatas", "documents", "distances"],
            )
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
        except Exception as exc:
            print(f"[db] vector query failed, falling back to keyword search: {exc}")
            data = payouts.get(
                where_document={"$contains": text},
                include=["metadatas"],
                limit=limit,
            )
            metadatas = data.get("metadatas") or []
            distances = [None] * len(metadatas)

    out = []
    for meta, distance in zip(metadatas, distances):
        score = None
        if distance is not None:
            score = round(max(0.0, min(1.0, 1.0 - float(distance))), 3)
        out.append(
            {
                "ticker": meta.get("ticker"),
                "name": meta.get("name"),
                "date": meta.get("date"),
                "quarter": meta.get("quarter_key"),
                "amount": float(meta.get("amount") or 0),
                "quantity": int(meta.get("quantity") or 0),
                "income": float(meta.get("income") or 0),
                "score": score,
            }
        )
    return out


# ---------------------------------------------------------------------------
# startup / info
# ---------------------------------------------------------------------------
def init_storage():
    """Open the database and restore from the JSON mirror if it is empty."""
    with _lock:
        holdings, _ = _get_collections()
        if holdings.count() == 0 and os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                items = payload.get("holdings") or []
                for item in items:
                    _save_holding_locked(
                        item, write_backup=False, bump_revision=False
                    )
                if items:
                    _bump_revision_locked()
                    print(f"[db] restored {len(items)} holding(s) from backup")
            except Exception as exc:
                print(f"[db] backup restore failed: {exc}")
        # Backfill the stored per-year TOTAL DIVIDEND totals for databases
        # created before that field existed.
        migrated = False
        try:
            data = holdings.get(include=["metadatas"])
            for meta in data.get("metadatas") or []:
                try:
                    holding = _holding_from_metadata(meta)
                    expected = _year_totals(holding["dividends"])
                    try:
                        stored = json.loads(meta.get("year_totals_json") or "{}")
                    except (TypeError, ValueError):
                        stored = {}
                    if stored != expected:
                        _save_holding_locked(
                            holding, write_backup=False, bump_revision=False
                        )
                        migrated = True
                except Exception as exc:
                    print(f"[db] could not migrate year totals: {exc}")
        except Exception as exc:
            print(f"[db] year-total migration failed: {exc}")
        if migrated:
            _bump_revision_locked()
            _write_backup()
        # Existing databases created before revision tracking start at 0;
        # give connected clients a non-zero revision to observe.
        if holdings.count() and _read_revision_locked() == 0:
            _bump_revision_locked()
        info = storage_info()
        print(
            f"[db] ChromaDB {info['version']} at {info['path']} "
            f"({info['holdings']} holdings, {info['payouts']} payouts)"
        )
        return info


def storage_info():
    with _lock:
        holdings, payouts = _get_collections()
        return {
            "backend": "chromadb",
            "version": getattr(chromadb, "__version__", "unknown"),
            "path": DB_DIR,
            "revision": _read_revision_locked(),
            "holdings": holdings.count(),
            "payouts": payouts.count(),
            "deleted": len(_read_tombstones_locked()),
            "backup": BACKUP_FILE if os.path.exists(BACKUP_FILE) else None,
        }
