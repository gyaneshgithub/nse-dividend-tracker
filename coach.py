#!/usr/bin/env python3
"""Deterministic portfolio coach for the Dividend Tracker.

The coach turns local holdings and analysis into a read-only briefing: tasks to
complete, upcoming dividend-related events, and plain-language context. It does
not call the network, use a language model, or write to storage.

PR1 ships the response contract and routing; task and event rules are filled in
in later phases. ``build()`` always returns stable keys so the UI and tests can
depend on the shape today.
"""

from __future__ import annotations

from datetime import datetime, timezone

import db
from insight import DISCLAIMER

RULESET = "stub"
RULESET_VERSION = 1


def _parse_year(year):
    try:
        year_value = None if str(year).lower() in ("all", "", "none") else int(year)
    except (TypeError, ValueError):
        year_value = None
    year_label = "all recorded years" if year_value is None else str(year_value)
    return year_value, year_label


def _briefing(holdings_count: int, year_label: str) -> dict:
    if holdings_count == 0:
        return {
            "headline": "No holdings yet",
            "summary": (
                "Add NSE stocks and enter quarterly quantities to generate a "
                "portfolio briefing and action list."
            ),
        }
    return {
        "headline": "Briefing ready",
        "summary": (
            f"{holdings_count} holding{'s' if holdings_count != 1 else ''} tracked "
            f"for {year_label}. Automated tasks and calendar events will appear here "
            "as coach rules roll out."
        ),
    }


def _task_counts(tasks: list) -> dict:
    by_priority = {"p0": 0, "p1": 0, "p2": 0}
    for task in tasks:
        key = str(task.get("priority", "")).lower()
        if key in by_priority:
            by_priority[key] += 1
    return {
        "total": len(tasks),
        **by_priority,
    }


def build(year="all"):
    """Return the coach payload for one year. Read-only."""
    year_value, year_label = _parse_year(year)
    holdings = db.load_holdings()
    # Warm analysis so later rules can reuse the same aggregates as insight.
    db.analysis("all" if year_value is None else year_value)

    tasks: list = []
    events: list = []

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "year": "all" if year_value is None else year_value,
        "yearLabel": year_label,
        "ruleset": {"id": RULESET, "version": RULESET_VERSION},
        "briefing": _briefing(len(holdings), year_label),
        "tasks": tasks,
        "events": events,
        "counts": _task_counts(tasks),
        "disclaimer": DISCLAIMER,
    }
