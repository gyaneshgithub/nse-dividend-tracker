"""Agent endpoints: export, import preview/apply, calendar, tax playbook."""

from __future__ import annotations

from datetime import datetime

import agent
import db
import forecast
from flask import Blueprint, Response, jsonify, request

from .common import storage_error

bp = Blueprint("agent", __name__)


@bp.get("/api/export")
def api_export():
    year = request.args.get("year", "all")
    if str(year).lower() in ("all", "", "none"):
        return jsonify({"error": "Choose a specific year or FY (e.g. 2025 or fy:2025)"}), 400
    try:
        body = agent.export_csv(year)
        label = agent.period_storage_key(year) or str(year)
        filename = f"dividend-tracker-{label.replace(':', '-')}.csv"
        return Response(
            body,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return storage_error(exc)


@bp.post("/api/import/preview")
def api_import_preview():
    payload = request.get_json(silent=True) or {}
    try:
        year = int(payload.get("year"))
    except (TypeError, ValueError):
        return jsonify({"error": "year is required (integer)"}), 400
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return jsonify({"error": "rows must be a list"}), 400
    mode = str(payload.get("mode") or "add")
    try:
        holdings = db.load_holdings()
        plan = agent.import_plan(holdings, rows, year, mode=mode)
        return jsonify(plan)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return storage_error(exc)


@bp.post("/api/import/apply")
def api_import_apply():
    payload = request.get_json(silent=True) or {}
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return jsonify({"error": "plan must be an object from /api/import/preview"}), 400
    try:
        holdings = db.load_holdings()
        saved = agent.apply_import_plan(holdings, plan)
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
    except Exception as exc:
        return storage_error(exc, "import")


@bp.get("/api/agent/tax")
def api_tax_playbook():
    year = request.args.get("year", "fy:2025")
    try:
        return jsonify(agent.tax_playbook(year))
    except Exception as exc:
        return storage_error(exc)


@bp.get("/api/forecast")
def api_forecast():
    try:
        months = int(request.args.get("months", forecast.DEFAULT_HORIZON))
    except (TypeError, ValueError):
        months = forecast.DEFAULT_HORIZON
    try:
        return jsonify(forecast.build(months))
    except Exception as exc:
        return storage_error(exc)


@bp.get("/api/agent/autofill/preview")
def api_autofill_preview():
    try:
        year = int(request.args.get("year", datetime.now().year))
    except (TypeError, ValueError):
        return jsonify({"error": "year must be an integer"}), 400
    try:
        return jsonify(forecast.autofill_plan(db.load_holdings(), year))
    except Exception as exc:
        return storage_error(exc)


@bp.post("/api/agent/autofill/apply")
def api_autofill_apply():
    payload = request.get_json(silent=True) or {}
    try:
        year = int(payload.get("year"))
    except (TypeError, ValueError):
        return jsonify({"error": "year is required"}), 400
    tickers = payload.get("tickers")
    if tickers is not None and not isinstance(tickers, list):
        return jsonify({"error": "tickers must be a list when provided"}), 400
    want = {str(t).strip().upper() for t in tickers} if tickers else None
    try:
        saved = []
        for holding in db.load_holdings():
            ticker = holding.get("ticker") or ""
            if want is not None and ticker not in want:
                continue
            updated = forecast.apply_autofill_row(holding, year)
            if updated:
                saved.append(updated)
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
    except Exception as exc:
        return storage_error(exc, "write")


@bp.get("/api/agent/calendar.ics")
def api_calendar_ics():
    year = request.args.get("year")
    year_value = None
    if year not in (None, "", "all"):
        try:
            year_value = int(year)
        except (TypeError, ValueError):
            return jsonify({"error": "year must be a calendar year"}), 400
    try:
        holdings = db.load_holdings()
        body = agent.calendar_ics(holdings, year_value)
        label = year or "rolling"
        return Response(
            body,
            mimetype="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dividend-calendar-{label}.ics"'},
        )
    except Exception as exc:
        return storage_error(exc)
