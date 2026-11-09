"""Portfolio storage endpoints (ChromaDB-backed)."""

from __future__ import annotations

import db
from flask import Blueprint, jsonify, request
from market import SYMBOL_RE

from .common import storage_error

bp = Blueprint("portfolio", __name__)


@bp.get("/api/revision")
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
        return storage_error(exc)


@bp.get("/api/portfolio")
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
        return storage_error(exc)


@bp.put("/api/portfolio/<ticker>")
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
        dividend_changes = saved.pop("dividendChanges", None) if isinstance(saved, dict) else None
        payload = {
            "ok": True,
            "holding": saved,
            "revision": info.get("revision", 0),
            "storage": info,
        }
        if dividend_changes:
            payload["dividendChanges"] = dividend_changes
        return jsonify(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return storage_error(exc, "write")


@bp.delete("/api/portfolio/<ticker>")
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
        return storage_error(exc, "delete")


@bp.post("/api/portfolio/import")
@bp.post("/api/portfolio")
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
        return storage_error(exc, "import")
