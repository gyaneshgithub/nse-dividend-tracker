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
    Errors add a stable code and a user-facing hint:
    -> { error, code, hint, symbol } with 400 (invalid symbol), 404 (no page or
       no history), 429 (rate limited) or 502 (network/upstream)

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
GET /api/insight?year=2026|all     -> scores, narrative and chart data (read-only,
                                      never stored)
GET /api/search?q=...&limit=10     -> semantic search over indexed payouts
GET /api/storage                   -> ChromaDB status, counts and revision
GET /api/health                    -> health + storage status

Every write bumps a persistent revision counter. The browser polls
/api/revision every few seconds and merges changes from other windows.

This module is the app composition root: it creates the Flask app, installs
request guards, security headers and error handlers, registers the blueprints
from `routes/`, and starts the server. UI assets live in `templates/` (HTML)
and `static/` (CSS/JS) and need no build step.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Keep ChromaDB telemetry disabled unless the user explicitly opts in.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import db  # noqa: E402  (must load .env before db resolves its paths)
from market import get_dividends  # noqa: E402,F401  (re-exported for the dividends route)
from routes import register_blueprints  # noqa: E402

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.json.sort_keys = False

register_blueprints(app)


# ---------------------------------------------------------------------------
# request guards and response headers
# ---------------------------------------------------------------------------
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
