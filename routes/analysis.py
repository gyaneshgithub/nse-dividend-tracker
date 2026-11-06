"""Analysis, portfolio insight and semantic search."""

from __future__ import annotations

import db
import insight
from flask import Blueprint, jsonify, request

from .common import storage_error

bp = Blueprint("analysis", __name__)


@bp.get("/api/analysis")
def api_analysis():
    year = request.args.get("year", "all")
    try:
        return jsonify(db.analysis(year))
    except Exception as exc:
        return storage_error(exc)


@bp.get("/api/insight")
def api_insight():
    """Deterministic portfolio insight. Read-only: nothing is persisted."""
    try:
        return jsonify(insight.build(request.args.get("year", "all")))
    except Exception as exc:
        return storage_error(exc)


@bp.get("/api/search")
def api_search():
    query = request.args.get("q", "")
    limit = request.args.get("limit", "10")
    try:
        return jsonify(
            {"query": query, "results": db.search_payouts(query, limit)}
        )
    except Exception as exc:
        return storage_error(exc)
