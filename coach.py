#!/usr/bin/env python3
"""Deterministic portfolio coach for the Dividend Tracker.

The coach turns local holdings and analysis into a read-only briefing: tasks to
complete, upcoming dividend-related events, and plain-language context. It does
not call the network, use a language model, or write to storage.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import agent
import db
import forecast
import insight
from insight import DISCLAIMER, _holding_insight

RULESET = "v1"
RULESET_VERSION = 5
STALE_FETCH_DAYS = int(os.environ.get("COACH_STALE_FETCH_DAYS", "7") or "7")
SCENARIO_SPREAD_PCT = 15.0
MAX_EVENTS = 30


def _parse_year(year):
    text = str(year or "all").strip().lower()
    if text in ("all", "", "none"):
        return None, "all recorded years"
    if text.startswith("fy:"):
        try:
            start = int(text.split(":", 1)[1])
        except (TypeError, ValueError):
            return None, "all recorded years"
        return text, f"FY {start}-{str(start + 1)[-2:]}"
    try:
        y = int(text)
        return y, str(y)
    except (TypeError, ValueError):
        return None, "all recorded years"


def _dividend_in_coach_year(date: str, year_value) -> bool:
    if year_value is None:
        return True
    if str(year_value).startswith("fy:"):
        try:
            start = int(str(year_value).split(":", 1)[1])
        except (TypeError, ValueError):
            return True
        return db._date_in_fy(date, start)
    try:
        return int(date[:4]) == int(year_value)
    except (TypeError, ValueError):
        return True


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _briefing(holdings_count: int, year_label: str, counts: dict) -> dict:
    if holdings_count == 0:
        return {
            "headline": "No holdings yet",
            "summary": (
                "Add NSE stocks and enter quarterly quantities to generate a "
                "portfolio briefing and action list."
            ),
        }
    total = counts.get("total") or 0
    urgent = counts.get("p0") or 0
    if urgent > 0:
        return {
            "headline": f"{urgent} urgent item{'s' if urgent != 1 else ''} need attention",
            "summary": (
                f"{total} suggested action{'s' if total != 1 else ''} for {year_label}. "
                "Start with missing quantities and failed refreshes."
            ),
        }
    if total > 0:
        return {
            "headline": "Portfolio briefing",
            "summary": (
                f"{total} optional follow-up{'s' if total != 1 else ''} for {year_label}."
            ),
        }
    return {
        "headline": "All clear",
        "summary": (
            f"No action items for {year_label}. Quantities and dividend fetches look "
            "complete for the current rules."
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


def _missing_quantity_tasks(holdings, year_value):
    tasks = []
    seen = set()
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        for div in holding.get("dividends") or []:
            date = str(div.get("date") or "")
            if len(date) < 7:
                continue
            try:
                div_year = int(date[:4])
            except ValueError:
                continue
            if year_value is not None and not _dividend_in_coach_year(date, year_value):
                continue
            key = db.quarter_key(date)
            if db._qty_for_quarter(holding, key) > 0:
                continue
            dedupe = (ticker, key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            quarter = key.split("-", 1)[1]
            tasks.append(
                {
                    "id": f"missing-qty:{ticker}:{key}",
                    "priority": "p0",
                    "type": "missing-quantity",
                    "ticker": ticker,
                    "year": div_year,
                    "quarter": key,
                    "message": (
                        f"Enter {quarter} quantity for {ticker} — a dividend is recorded "
                        f"but income for that quarter is zero."
                    ),
                    "action": "focus-quarter",
                }
            )
    return tasks


def _fetch_tasks(holdings):
    now = datetime.now(timezone.utc)
    tasks = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        status = str(holding.get("status") or "ok")
        if status != "ok":
            tasks.append(
                {
                    "id": f"refresh:{ticker}",
                    "priority": "p0",
                    "type": "fetch-failed",
                    "ticker": ticker,
                    "message": (
                        f"Refresh {ticker}: the last dividend fetch failed "
                        f"({holding.get('lastError') or 'error'})."
                    ),
                    "action": "refresh",
                }
            )
            continue
        fetched = _parse_time(holding.get("lastFetched"))
        if fetched and (now - fetched) > timedelta(days=max(1, STALE_FETCH_DAYS)):
            age = (now - fetched).days
            tasks.append(
                {
                    "id": f"stale:{ticker}",
                    "priority": "p0",
                    "type": "stale-fetch",
                    "ticker": ticker,
                    "message": (
                        f"Refresh {ticker}: dividend data is {age} days old "
                        f"(stale after {STALE_FETCH_DAYS} days)."
                    ),
                    "action": "refresh",
                }
            )
    return tasks


def _cut_tasks(holdings, year_value, share):
    tasks = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        insight_year = year_value if isinstance(year_value, int) else None
        row = _holding_insight(holding, share.get(ticker, 0), insight_year)
        for cut in row.get("cuts") or []:
            tasks.append(
                {
                    "id": f"cut:{ticker}:{cut['year']}",
                    "priority": "p1",
                    "type": "dividend-cut",
                    "ticker": ticker,
                    "year": cut["year"],
                    "message": (
                        f"Review {ticker} dividend cut in {cut['year']} "
                        f"({cut['dropPct']:+.1f}% per share)."
                    ),
                    "action": "open-insight",
                }
            )
    return tasks


def _concentration_tasks(stats):
    top1 = stats.get("top1Pct")
    if top1 is None or top1 <= 40:
        return []
    return [
        {
            "id": "concentration:top1",
            "priority": "p1",
            "type": "concentration",
            "message": (
                f"Income concentration is high: one holding supplies {top1:.1f}% "
                "of this period's dividend income."
            ),
            "action": "open-insight",
        }
    ]


def _scenario_spread_task(projections):
    if not projections:
        return []
    conservative = projections.get("conservative") or {}
    growth = projections.get("growth") or {}
    low = float(conservative.get("income") or 0)
    high = float(growth.get("income") or 0)
    if low <= 0:
        return []
    spread = abs(high - low) / low * 100.0
    if spread < SCENARIO_SPREAD_PCT:
        return []
    return [
        {
            "id": "projection:spread",
            "priority": "p2",
            "type": "projection-spread",
            "message": (
                f"Low vs growth next-year scenarios differ by {spread:.0f}% — "
                "review quantity and CAGR assumptions."
            ),
            "action": "open-analysis",
        }
    ]


def _build_events(holdings, year_value):
    events = []
    now = datetime.now(timezone.utc).isoformat()
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        months = []
        for div in holding.get("dividends") or []:
            date = str(div.get("date") or "")
            if len(date) < 7:
                continue
            if year_value is not None and not _dividend_in_coach_year(date, year_value):
                continue
            months.append(int(date[5:7]))
            if len(events) < MAX_EVENTS:
                events.append(
                    {
                        "id": f"payout:{ticker}:{date}",
                        "type": "dividend-paid",
                        "ticker": ticker,
                        "at": date,
                        "message": f"{ticker} paid ₹{float(div.get('amount') or 0):.2f}/share on {date}.",
                    }
                )
        if months:
            common = max(set(months), key=months.count)
            events.append(
                {
                    "id": f"pattern:{ticker}",
                    "type": "payment-pattern",
                    "ticker": ticker,
                    "at": now,
                    "message": (
                        f"{ticker} often pays around month {common} "
                        f"({['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][common - 1]})."
                    ),
                }
            )
    return events[:MAX_EVENTS]


def _sort_tasks(tasks):
    order = {"p0": 0, "p1": 1, "p2": 2}

    def key(task):
        return (order.get(str(task.get("priority", "")).lower(), 9), task.get("ticker") or "", task.get("id") or "")

    return sorted(tasks, key=key)


def build(year="all"):
    """Return the coach payload for one year. Read-only."""
    year_value, year_label = _parse_year(year)
    holdings = db.load_holdings()
    analysis_year = "all" if year_value is None else year_value
    result = db.analysis(analysis_year)
    insight_data = insight.build(year)
    stats = insight_data.get("stats") or {}

    share = {row["ticker"]: row.get("sharePct") or 0 for row in result.get("byStock", [])}

    tasks = []
    tasks.extend(_missing_quantity_tasks(holdings, year_value))
    tasks.extend(_fetch_tasks(holdings))
    tasks.extend(_cut_tasks(holdings, year_value, share))
    tasks.extend(_concentration_tasks(stats))
    tasks.extend(_scenario_spread_task(result.get("projections")))
    tasks.extend(agent.reconcile_tasks(holdings, year_value))
    tasks.extend(agent.refresh_diff_tasks(holdings, year_value))
    tasks.extend(agent.income_attribution_tasks(holdings, year_value, result))
    tasks.extend(agent.calendar_block_tasks(holdings, year_value if isinstance(year_value, int) else None))
    current_year = datetime.now(timezone.utc).year
    if isinstance(year_value, int) and year_value == current_year:
        tasks.extend(forecast.autofill_plan(holdings, year_value).get("tasks") or [])
    tasks = _sort_tasks(tasks)
    tasks.extend(agent.export_readiness_tasks(holdings, year_value, tasks))
    if isinstance(year_value, str) and str(year_value).startswith("fy:"):
        tasks.extend(agent.tax_playbook(year_value).get("tasks") or [])
    tasks = _sort_tasks(tasks)

    events = _build_events(holdings, year_value)
    cal_year = year_value if isinstance(year_value, int) else None
    events.extend(agent.upcoming_calendar_events(holdings, cal_year))
    counts = _task_counts(tasks)

    cashflow = forecast.build(12) if holdings else None
    briefing = _briefing(len(holdings), year_label, counts)
    if cashflow:
        base_total = (cashflow.get("scenarios") or {}).get("base", {}).get("total")
        if base_total:
            briefing = {
                **briefing,
                "summary": (
                    briefing["summary"]
                    + f" Rolling 12-month cashflow estimate (base): ₹{base_total:,.0f}."
                ),
            }

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "year": "all" if year_value is None else year_value,
        "yearLabel": year_label,
        "ruleset": {"id": RULESET, "version": RULESET_VERSION},
        "briefing": briefing,
        "tasks": tasks,
        "events": events,
        "counts": counts,
        "forecast": cashflow,
        "disclaimer": DISCLAIMER,
    }
