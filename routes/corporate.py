"""Upcoming bonus and stock-split notifications.

Read-only: fetches the NSE corporate-actions feed, filters to bonus/split,
and flags actions that match a tracked holding. Failures come back as a
quiet ``ok: false`` payload instead of an error page.
"""

from __future__ import annotations

import corporate
import db
from flask import Blueprint, jsonify, request

bp = Blueprint("corporate", __name__)


@bp.get("/api/corporate-actions")
def api_corporate_actions():
    try:
        days = int(request.args.get("days", corporate.DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = corporate.DEFAULT_DAYS
    days = max(1, min(days, 365))
    try:
        holdings = db.load_holdings()
    except Exception:
        holdings = []
    return jsonify(corporate.upcoming(holdings, days=days))
