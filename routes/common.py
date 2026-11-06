"""Helpers shared by the route blueprints."""

from __future__ import annotations

import db
from flask import jsonify


def storage_info():
    """Storage status, or a best-effort payload when ChromaDB is unavailable."""
    try:
        return db.storage_info()
    except Exception as exc:
        return {"backend": "unavailable", "error": str(exc)}


def storage_error(exc, action="read"):
    """Map a storage exception to the JSON response used by every endpoint."""
    if isinstance(exc, db.StorageUnavailable):
        return jsonify({"error": str(exc), "backend": "chromadb"}), 503
    return jsonify({"error": f"Storage {action} failed: {exc}"}), 500
