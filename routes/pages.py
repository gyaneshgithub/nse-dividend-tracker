"""HTML pages."""

from __future__ import annotations

from pathlib import Path

import db
from flask import Blueprint, redirect, render_template

bp = Blueprint("pages", __name__)
_ROOT = Path(__file__).resolve().parents[1]


def _static_mtime(filename: str) -> int:
    try:
        return int((_ROOT / "static" / filename).stat().st_mtime)
    except OSError:
        return 0


@bp.get("/")
def root():
    return redirect("/Dividend%20tracker")


@bp.get("/Dividend tracker")
@bp.get("/Dividend tracker/")
@bp.get("/Dividend Tracker")
@bp.get("/Dividend Tracker/")
@bp.get("/dividend-tracker")
@bp.get("/dividend-tracker/")
@bp.get("/dividend-tracker.html")
@bp.get("/tracker")
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
    return render_template(
        "index.html",
        initial_state=initial_state,
        asset_css_ver=_static_mtime("app.css"),
        asset_js_ver=_static_mtime("app.js"),
    )
