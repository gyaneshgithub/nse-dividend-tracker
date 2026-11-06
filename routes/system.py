"""Health and storage status."""

from __future__ import annotations

import db
from flask import Blueprint, jsonify

from .common import storage_error, storage_info

bp = Blueprint("system", __name__)


@bp.get("/api/health")
def api_health():
    return jsonify({"ok": True, "storage": storage_info()})


@bp.get("/api/storage")
def api_storage():
    try:
        return jsonify(db.storage_info())
    except Exception as exc:
        return storage_error(exc)
