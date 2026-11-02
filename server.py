#!/usr/bin/env python3
"""Dividend Tracker — Python/Flask backend.

Canonical app URL
-----------------
http://localhost:8901/Dividend%20tracker

Aliases: /Dividend tracker, /dividend-tracker, /dividend-tracker.html, /tracker

Dividend data
-------------
GET /api/dividends?symbol=RELIANCE
    -> { symbol, name, price, currency, annualDividend, yieldPct, dividends: [{date, amount}] }

Portfolio storage (ChromaDB)
----------------------------
GET    /api/portfolio              -> { holdings, revision, storage }
                                      (each holding includes yearTotals:
                                       per-share TOTAL DIVIDEND by year)
GET    /api/revision               -> { revision, holdings, payouts }
PUT    /api/portfolio/<TICKER>     -> upsert one holding (merged safely)
DELETE /api/portfolio/<TICKER>     -> remove one holding
POST   /api/portfolio/import       -> { holdings: [...] } bulk upsert

Analysis
--------
GET /api/analysis?year=2026|all    -> income by year / stock / quarter
GET /api/search?q=...&limit=10     -> semantic search over indexed payouts
GET /api/storage                   -> ChromaDB status, counts and revision
GET /api/health                    -> health + storage status

Every write bumps a persistent revision counter. The browser polls
/api/revision every few seconds and merges changes from other windows.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Keep ChromaDB telemetry disabled unless the user explicitly opts in.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import db  # noqa: E402  (must load .env before db resolves its paths)
from market import SYMBOL_RE, get_dividends  # noqa: E402

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.json.sort_keys = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _storage_info():
    try:
        return db.storage_info()
    except Exception as exc:
        return {"backend": "unavailable", "error": str(exc)}


def _storage_error(exc, action="read"):
    if isinstance(exc, db.StorageUnavailable):
        return jsonify({"error": str(exc), "backend": "chromadb"}), 503
    return jsonify({"error": f"Storage {action} failed: {exc}"}), 500


@app.before_request
def _reject_cross_origin_writes():
    """Block browser writes from other origins.

    The API has no authentication, so a wildcard CORS policy would let any
    website read or modify a local portfolio. Same-origin requests and
    non-browser clients (no Origin header) still work.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return jsonify({"error": "Cross-origin writes are not allowed"}), 403
    return None


@app.after_request
def _response_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# app page (Python-rendered, with initial state injected)
# ---------------------------------------------------------------------------
@app.get("/")
def root():
    return redirect("/Dividend%20tracker")


@app.get("/Dividend tracker")
@app.get("/Dividend tracker/")
@app.get("/Dividend Tracker")
@app.get("/Dividend Tracker/")
@app.get("/dividend-tracker")
@app.get("/dividend-tracker/")
@app.get("/dividend-tracker.html")
@app.get("/tracker")
def index():
    try:
        holdings = db.load_holdings()
        revision = db.revision()
        storage = db.storage_info()
    except Exception as exc:
        holdings = []
        revision = 0
        storage = {"backend": "unavailable", "error": str(exc)}
    initial_state = {
        "holdings": holdings,
        "revision": revision,
        "storage": storage,
    }
    return render_template("index.html", initial_state=initial_state)


# ---------------------------------------------------------------------------
# dividend data
# ---------------------------------------------------------------------------
@app.get("/api/dividends")
def api_dividends():
    symbol = request.args.get("symbol", "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        return jsonify({"error": "Invalid NSE symbol"}), 400
    try:
        return jsonify(get_dividends(symbol))
    except LookupError as exc:
        return jsonify({"error": str(exc), "symbol": symbol}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc), "symbol": symbol}), 400
    except Exception as exc:
        return jsonify({"error": "Upstream fetch failed: " + str(exc), "symbol": symbol}), 502


# ---------------------------------------------------------------------------
# portfolio storage
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    payload = {"ok": True}
    payload["storage"] = _storage_info()
    return jsonify(payload)


@app.get("/api/revision")
def api_revision():
    try:
        info = db.storage_info()
        return jsonify(
            {
                "revision": info.get("revision", 0),
                "holdings": info.get("holdings", 0),
                "payouts": info.get("payouts", 0),
            }
        )
    except Exception as exc:
        return _storage_error(exc)


@app.get("/api/portfolio")
def api_portfolio():
    try:
        info = db.storage_info()
        return jsonify(
            {
                "holdings": db.load_holdings(),
                "revision": info.get("revision", 0),
                "storage": info,
            }
        )
    except Exception as exc:
        return _storage_error(exc)


@app.put("/api/portfolio/<ticker>")
def api_save_holding(ticker):
    ticker = str(ticker or "").strip().upper()
    if not SYMBOL_RE.fullmatch(ticker):
        return jsonify({"error": "Invalid NSE ticker"}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400
    payload["ticker"] = ticker
    try:
        saved = db.save_holding(payload)
        info = db.storage_info()
        if saved is None:
            return jsonify(
                {
                    "ok": True,
                    "ignored": True,
                    "reason": "This holding was deleted; a stale copy was ignored.",
                    "revision": info.get("revision", 0),
                    "storage": info,
                }
            )
        return jsonify(
            {
                "ok": True,
                "holding": saved,
                "revision": info.get("revision", 0),
                "storage": info,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _storage_error(exc, "write")


@app.delete("/api/portfolio/<ticker>")
def api_delete_holding(ticker):
    ticker = str(ticker or "").strip().upper()
    if not SYMBOL_RE.fullmatch(ticker):
        return jsonify({"error": "Invalid NSE ticker"}), 400
    try:
        db.delete_holding(ticker)
        info = db.storage_info()
        return jsonify(
            {
                "ok": True,
                "ticker": ticker,
                "revision": info.get("revision", 0),
                "storage": info,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _storage_error(exc, "delete")


@app.post("/api/portfolio/import")
@app.post("/api/portfolio")
def api_import_holdings():
    payload = request.get_json(silent=True)
    items = payload.get("holdings") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return jsonify({"error": "holdings must be a list"}), 400
    try:
        saved = db.import_holdings(items)
        info = db.storage_info()
        return jsonify(
            {
                "ok": True,
                "saved": len(saved),
                "holdings": saved,
                "revision": info.get("revision", 0),
                "storage": info,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _storage_error(exc, "import")


# ---------------------------------------------------------------------------
# analysis and search
# ---------------------------------------------------------------------------
@app.get("/api/analysis")
def api_analysis():
    year = request.args.get("year", "all")
    try:
        return jsonify(db.analysis(year))
    except Exception as exc:
        return _storage_error(exc)


@app.get("/api/search")
def api_search():
    query = request.args.get("q", "")
    limit = request.args.get("limit", "10")
    try:
        return jsonify(
            {"query": query, "results": db.search_payouts(query, limit)}
        )
    except Exception as exc:
        return _storage_error(exc)


@app.get("/api/storage")
def api_storage():
    try:
        return jsonify(db.storage_info())
    except Exception as exc:
        return _storage_error(exc)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return (
        "<h1>Not found</h1><p>Try "
        '<a href="/Dividend%20tracker">the Dividend Tracker</a>.</p>',
        404,
    )


@app.errorhandler(405)
def method_not_allowed(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method not allowed"}), 405
    return jsonify({"error": "Method not allowed"}), 405


@app.route(
    "/api/<path:_path>",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)
def api_not_found(_path):
    if request.method == "OPTIONS":
        return ("", 204)
    return jsonify({"error": "Not found"}), 404


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------
def main():
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("PORT", "8901"))
    except ValueError:
        raise SystemExit("[server] PORT must be an integer.")

    # Fail fast: a tracker without persistent storage silently loses holdings.
    try:
        db.init_storage()
    except Exception as exc:
        raise SystemExit(
            "[server] ChromaDB storage unavailable: {}\n"
            "[server] Install dependencies with: python -m pip install -r requirements.txt\n"
            "[server] Then run: python scripts/check_setup.py".format(exc)
        )

    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::1") else host
    print(f"Dividend Tracker running at http://{shown_host}:{port}/Dividend%20tracker")
    if host == "0.0.0.0":
        print("[server] Listening on all interfaces. Only do this on a trusted network.")
    app.run(
        host=host,
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
