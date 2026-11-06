"""HTML pages."""

from __future__ import annotations

import db
from flask import Blueprint, redirect, render_template

bp = Blueprint("pages", __name__)


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
    return render_template("index.html", initial_state=initial_state)
