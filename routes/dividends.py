"""Dividend data fetched from the upstream source."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from market import SYMBOL_RE

bp = Blueprint("dividends", __name__)


@bp.get("/api/dividends")
def api_dividends():
    # Resolve the fetcher through the server module so callers and tests that
    # replace `server.get_dividends` keep working after the blueprint split.
    import server

    symbol = request.args.get("symbol", "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        return jsonify({"error": "Invalid NSE symbol"}), 400
    try:
        return jsonify(server.get_dividends(symbol))
    except LookupError as exc:
        return jsonify({"error": str(exc), "symbol": symbol}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc), "symbol": symbol}), 400
    except Exception as exc:
        return jsonify({"error": "Upstream fetch failed: " + str(exc), "symbol": symbol}), 502
