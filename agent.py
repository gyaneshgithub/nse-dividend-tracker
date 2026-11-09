#!/usr/bin/env python3
"""Deterministic portfolio agent: reconcile, import plans, export, calendar, tax.

Read-only orchestration except import apply uses db.save_holding. No network.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import db
import insight
from market import SYMBOL_RE

RECONCILE_TOLERANCE_ABS = 50.0
RECONCILE_TOLERANCE_PCT = 2.0
IMPORT_CONFLICT_QTY_DROP_PCT = 50.0
ATTRIBUTION_MIN_DELTA_ABS = 25.0
ATTRIBUTION_DOMINANCE_PCT = 55.0
MAX_ATTRIBUTION_TASKS = 4
ATTRIBUTION_BRIDGE_MIN_ABS = 500.0
CASHFLOW_STRESS_GAP_PCT = 10.0
CASHFLOW_STRESS_SEVERE_PCT = 18.0
CASHFLOW_STRESS_MIN_BASE = 500.0
MAX_CASHFLOW_STRESS_TICKER_TASKS = 3

_DOMINANT_LABELS = {
    "quantity": "share quantities",
    "dps": "dividend per share",
    "composition": "new or removed payouts",
}

_MONTH_NAMES = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def period_storage_key(year) -> str | None:
    if year is None:
        return None
    text = str(year).strip().lower()
    if text in ("all", "", "none"):
        return None
    if text.startswith("fy:"):
        return text
    try:
        return str(int(text))
    except (TypeError, ValueError):
        return None


def _reconcile_entry(holding, key) -> dict | None:
    raw = (holding.get("reconcile") or {}).get(key)
    return raw if isinstance(raw, dict) else None


def _tolerance(computed: float) -> float:
    return max(RECONCILE_TOLERANCE_ABS, abs(computed) * RECONCILE_TOLERANCE_PCT / 100.0)


def reconcile_status(holding, year) -> dict | None:
    key = period_storage_key(year)
    if not key:
        return None
    period = db._parse_analysis_period(key if key.startswith("fy:") else int(key))
    if period["mode"] == "all":
        return None
    computed = db.holding_income_for_period(holding, period)
    if computed <= 0:
        return None
    entry = _reconcile_entry(holding, key)
    actual = None if not entry else entry.get("actual")
    dismissed = (entry or {}).get("dismissedUntil") or ""
    if dismissed:
        try:
            until = datetime.fromisoformat(str(dismissed).replace("Z", "+00:00"))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < until:
                return {
                    "key": key,
                    "computed": computed,
                    "actual": actual,
                    "delta": None if actual is None else round(float(actual) - computed, 2),
                    "state": "dismissed",
                }
        except (TypeError, ValueError):
            pass
    if actual is None:
        return {"key": key, "computed": computed, "actual": None, "delta": None, "state": "missing"}
    delta = round(float(actual) - computed, 2)
    if abs(delta) <= _tolerance(computed):
        return {"key": key, "computed": computed, "actual": float(actual), "delta": delta, "state": "ok"}
    return {"key": key, "computed": computed, "actual": float(actual), "delta": delta, "state": "gap"}


def reconcile_tasks(holdings, year) -> list:
    key = period_storage_key(year)
    if not key:
        return []
    tasks = []
    for holding in holdings:
        status = reconcile_status(holding, year)
        if not status:
            continue
        ticker = holding.get("ticker") or "?"
        state = status["state"]
        if state == "missing":
            tasks.append(
                {
                    "id": f"reconcile-missing:{ticker}:{key}",
                    "priority": "p1",
                    "type": "reconcile-missing",
                    "ticker": ticker,
                    "year": key,
                    "message": (
                        f"Record actual dividend received for {ticker} in {key} "
                        f"(computed ₹{status['computed']:.2f})."
                    ),
                    "action": "open-reconcile",
                    "context": status,
                }
            )
        elif state == "gap":
            tasks.append(
                {
                    "id": f"reconcile-gap:{ticker}:{key}",
                    "priority": "p0",
                    "type": "reconcile-gap",
                    "ticker": ticker,
                    "year": key,
                    "message": (
                        f"Reconcile {ticker} for {key}: actual ₹{status['actual']:.2f} vs "
                        f"computed ₹{status['computed']:.2f} "
                        f"({status['delta']:+.2f})."
                    ),
                    "action": "open-reconcile",
                    "context": status,
                }
            )
    return tasks


def _blocking_types() -> set[str]:
    return {
        "missing-quantity",
        "fetch-failed",
        "stale-fetch",
        "reconcile-gap",
        "import-conflict",
    }


def export_readiness_tasks(holdings, year, existing_tasks: list) -> list:
    key = period_storage_key(year)
    if not key:
        return []
    blockers = [t for t in existing_tasks if t.get("type") in _blocking_types()]
    if blockers:
        return [
            {
                "id": f"export-blocked:{key}",
                "priority": "p1",
                "type": "export-blocked",
                "message": (
                    f"Resolve {len(blockers)} blocker(s) before exporting {key} "
                    f"(missing qty, failed fetch, or reconcile gaps)."
                ),
                "action": "open-export",
                "context": {"year": key, "blockers": len(blockers)},
            }
        ]
    if not holdings:
        return []
    return [
        {
            "id": f"export-ready:{key}",
            "priority": "p2",
            "type": "export-ready",
            "message": f"Portfolio export for {key} is ready (CSV).",
            "action": "run-export",
            "context": {"year": key},
        }
    ]


def parse_import_row(ticker, qtys: list) -> dict | None:
    raw = str(ticker or "").strip().upper().replace(".NS", "")
    if not SYMBOL_RE.fullmatch(raw):
        return None
    cleaned = []
    for value in (qtys or [])[:4]:
        if value is None or value == "":
            cleaned.append(None)
            continue
        try:
            n = max(0, int(float(value)))
        except (TypeError, ValueError):
            cleaned.append(None)
        else:
            cleaned.append(n)
    while len(cleaned) < 4:
        cleaned.append(None)
    return {"ticker": raw, "qtys": cleaned}


def import_plan(holdings, rows: list, year: int, mode: str = "add") -> dict:
    mode = str(mode or "add").strip().lower()
    if mode not in ("add", "update", "upsert"):
        raise ValueError("mode must be add, update, or upsert")
    by_ticker = {h["ticker"]: h for h in holdings}
    plan_rows = []
    tasks = []
    for index, row in enumerate(rows):
        parsed = parse_import_row(row.get("ticker"), row.get("qtys"))
        if not parsed:
            plan_rows.append(
                {
                    "line": index + 1,
                    "ticker": str(row.get("ticker") or ""),
                    "action": "invalid",
                    "reason": "invalid ticker",
                }
            )
            continue
        ticker = parsed["ticker"]
        existing = by_ticker.get(ticker)
        quarter_patch = {
            f"{year}-Q{q + 1}": parsed["qtys"][q]
            for q in range(4)
            if parsed["qtys"][q] is not None
        }
        if existing:
            if mode == "add":
                plan_rows.append(
                    {"line": index + 1, "ticker": ticker, "action": "skip", "reason": "already in tracker"}
                )
                continue
            conflict = False
            for key, new_qty in quarter_patch.items():
                old = existing.get("quarters", {}).get(key)
                if old is not None and int(old) > 0 and new_qty < old:
                    drop = (int(old) - new_qty) / int(old) * 100.0
                    if drop > IMPORT_CONFLICT_QTY_DROP_PCT:
                        conflict = True
            action = "update"
            if conflict:
                action = "conflict"
                tasks.append(
                    {
                        "id": f"import-conflict:{ticker}:{year}",
                        "priority": "p0",
                        "type": "import-conflict",
                        "ticker": ticker,
                        "message": (
                            f"Import would cut {ticker} quantities sharply for {year}; "
                            "review before applying."
                        ),
                        "action": "open-import",
                        "context": {"year": year, "ticker": ticker},
                    }
                )
            plan_rows.append(
                {
                    "line": index + 1,
                    "ticker": ticker,
                    "action": action,
                    "quarters": quarter_patch,
                }
            )
        else:
            if mode == "update":
                plan_rows.append(
                    {"line": index + 1, "ticker": ticker, "action": "skip", "reason": "not in tracker"}
                )
                continue
            plan_rows.append(
                {
                    "line": index + 1,
                    "ticker": ticker,
                    "action": "add",
                    "quarters": quarter_patch,
                }
            )
    return {
        "year": year,
        "mode": mode,
        "rows": plan_rows,
        "tasks": tasks,
        "counts": {
            "add": sum(1 for r in plan_rows if r["action"] == "add"),
            "update": sum(1 for r in plan_rows if r["action"] == "update"),
            "conflict": sum(1 for r in plan_rows if r["action"] == "conflict"),
            "skip": sum(1 for r in plan_rows if r["action"] == "skip"),
            "invalid": sum(1 for r in plan_rows if r["action"] == "invalid"),
        },
    }


def apply_import_plan(holdings, plan: dict) -> list:
    """Apply add/update/conflict rows from a preview plan. Returns saved tickers."""
    by_ticker = {h["ticker"]: h for h in holdings}
    saved = []
    for row in plan.get("rows") or []:
        action = row.get("action")
        if action not in ("add", "update"):
            continue
        ticker = row["ticker"]
        patch = row.get("quarters") or {}
        if action == "add":
            quantities = [q for q in patch.values() if q is not None]
            holding = {
                "ticker": ticker,
                "name": ticker,
                "shares": max(quantities) if quantities else 1,
                "quarters": dict(patch),
                "dividends": [],
                "status": "pending",
            }
        else:
            holding = dict(by_ticker[ticker])
            merged_q = dict(holding.get("quarters") or {})
            merged_q.update(patch)
            holding["quarters"] = merged_q
        out = db.save_holding(holding)
        if out:
            saved.append(out)
            by_ticker[ticker] = out
    return saved


def export_csv(year) -> str:
    key = period_storage_key(year) or str(year)
    period = db._parse_analysis_period(key if key.startswith("fy:") else int(key))
    holdings = db.load_holdings()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "ticker",
            "name",
            "period",
            "Q1_qty",
            "Q2_qty",
            "Q3_qty",
            "Q4_qty",
            "computed_income_inr",
            "actual_received_inr",
            "delta_inr",
            "reconcile_state",
        ]
    )
    cal_year = period.get("calendarYear")
    fy = period.get("fyStart")
    label = period.get("label") or key
    for holding in holdings:
        computed = db.holding_income_for_period(holding, period)
        status = reconcile_status(holding, key) or {}
        quarters = holding.get("quarters") or {}
        if cal_year:
            qkeys = [f"{cal_year}-Q{i}" for i in range(1, 5)]
        elif fy:
            qkeys = []
            for y in (fy, fy + 1):
                qkeys.extend(f"{y}-Q{i}" for i in range(1, 5))
        else:
            qkeys = []
        qtys = [quarters.get(k, "") for k in qkeys[:4]] if cal_year else ["", "", "", ""]
        if cal_year:
            qtys = [quarters.get(f"{cal_year}-Q{i}", "") for i in range(1, 5)]
        writer.writerow(
            [
                holding["ticker"],
                holding.get("name") or holding["ticker"],
                label,
                qtys[0],
                qtys[1],
                qtys[2],
                qtys[3],
                computed,
                status.get("actual", ""),
                status.get("delta", ""),
                status.get("state", ""),
            ]
        )
    return buf.getvalue()


def tax_playbook(year: str = "fy:2025") -> dict:
    key = period_storage_key(year) or "fy:2025"
    if not str(key).startswith("fy:"):
        key = f"fy:{key}"
    period = db._parse_analysis_period(key)
    analysis = db.analysis(key)
    holdings = db.load_holdings()
    gaps = []
    missing = []
    for holding in holdings:
        status = reconcile_status(holding, key)
        if not status:
            continue
        if status["state"] == "gap":
            gaps.append(holding["ticker"])
        elif status["state"] == "missing":
            missing.append(holding["ticker"])
    checklist = []
    checklist.append({"id": "income", "done": analysis.get("totalIncome", 0) > 0, "label": "FY dividend income computed"})
    checklist.append({"id": "reconcile", "done": not gaps and not missing, "label": "Actual receipts recorded and aligned"})
    checklist.append({"id": "export", "done": not gaps, "label": "No reconcile gaps blocking export"})
    tasks = []
    if gaps:
        tasks.append(
            {
                "id": "tax-reconcile-gaps",
                "priority": "p0",
                "type": "tax-incomplete",
                "message": f"Fix reconcile gaps before FY pack: {', '.join(gaps[:5])}.",
                "action": "open-reconcile",
            }
        )
    if missing:
        tasks.append(
            {
                "id": "tax-reconcile-missing",
                "priority": "p1",
                "type": "tax-incomplete",
                "message": f"Enter actual receipts for: {', '.join(missing[:5])}.",
                "action": "open-reconcile",
            }
        )
    if not tasks:
        tasks.append(
            {
                "id": "tax-export-ready",
                "priority": "p2",
                "type": "tax-export-ready",
                "message": f"FY {period.get('label')} pack ready — export CSV for your records.",
                "action": "run-export",
                "context": {"year": key},
            }
        )
    top = sorted(analysis.get("byStock") or [], key=lambda r: r.get("income") or 0, reverse=True)[:5]
    copy_lines = [
        f"NSE dividend income FY {period.get('label')}",
        f"Total (computed from quarterly quantities): ₹{analysis.get('totalIncome', 0):,.2f}",
        "",
        "Top payers:",
    ]
    for row in top:
        copy_lines.append(f"  {row.get('ticker')}: ₹{float(row.get('income') or 0):,.2f}")
    copy_lines.append("")
    copy_lines.append(
        "Local tracker estimate only — verify with broker statements and Form 26AS before filing."
    )
    return {
        "year": key,
        "yearLabel": period.get("label"),
        "totalIncome": analysis.get("totalIncome"),
        "checklist": checklist,
        "tasks": tasks,
        "topPayers": top,
        "copyText": "\n".join(copy_lines),
        "disclaimer": insight.DISCLAIMER,
    }


def upcoming_calendar_events(holdings, year_value) -> list:
    events = []
    now = datetime.now(timezone.utc)
    if year_value is None:
        target_year = now.year
    else:
        try:
            target_year = int(year_value)
        except (TypeError, ValueError):
            target_year = now.year
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        months = []
        amounts = []
        for div in holding.get("dividends") or []:
            date = str(div.get("date") or "")
            if len(date) < 7:
                continue
            try:
                y = int(date[:4])
            except ValueError:
                continue
            if year_value is not None and y != int(year_value):
                continue
            if year_value is None and y < target_year - 2:
                continue
            months.append(int(date[5:7]))
            amounts.append(float(div.get("amount") or 0))
        if not months:
            continue
        common = max(set(months), key=months.count)
        est_dps = max(amounts) if amounts else 0.0
        max_qty = 0
        for key, qty in (holding.get("quarters") or {}).items():
            if str(key).startswith(str(target_year)) or year_value is None:
                max_qty = max(max_qty, int(qty or 0))
        if max_qty <= 0:
            max_qty = max(int(holding.get("shares") or 0), 0)
        est_income = round(est_dps * max_qty, 2)
        month_name = _MONTH_NAMES[common - 1]
        events.append(
            {
                "id": f"upcoming:{ticker}:{common}",
                "type": "upcoming-payout",
                "ticker": ticker,
                "month": common,
                "at": now.isoformat(),
                "message": (
                    f"{ticker} often pays around {month_name} "
                    f"(~₹{est_income:,.2f} at last DPS × qty)."
                ),
                "context": {"estimatedIncome": est_income, "typicalMonth": common},
            }
        )
    return events


def calendar_block_tasks(holdings, year_value) -> list:
    tasks = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        if (holding.get("dividends") or []) and not (holding.get("quarters") or {}):
            tasks.append(
                {
                    "id": f"calendar-blocked:{ticker}",
                    "priority": "p1",
                    "type": "calendar-blocked",
                    "ticker": ticker,
                    "message": f"Enter {ticker} quantities before relying on the payout calendar.",
                    "action": "focus-quarter",
                }
            )
    return tasks


def _dividend_amount_map(dividends) -> dict[str, float]:
    out: dict[str, float] = {}
    for div in dividends or []:
        date = str(div.get("date") or "")
        if len(date) < 10:
            continue
        try:
            out[date] = round(float(div.get("amount") or 0), 4)
        except (TypeError, ValueError):
            continue
    return out


def dividend_refresh_diff(old_dividends, new_dividends) -> list:
    """Detect new ex-dates and per-share amount changes after a refresh."""
    old = _dividend_amount_map(old_dividends)
    new = _dividend_amount_map(new_dividends)
    if not new:
        return []
    changes = []
    for date in sorted(new.keys()):
        amount = new[date]
        if amount <= 0:
            continue
        if date not in old:
            changes.append({"type": "new-payout", "date": date, "amount": amount})
            continue
        prev = old[date]
        if abs(prev - amount) > 0.001:
            changes.append(
                {
                    "type": "dps-change",
                    "date": date,
                    "amount": amount,
                    "previous": prev,
                }
            )
    return changes


def _alert_message(ticker: str, change: dict) -> str:
    if change["type"] == "new-payout":
        return (
            f"New payout on {ticker}: ₹{change['amount']:.2f}/share on {change['date']} — "
            "confirm the quarter quantity."
        )
    prev = float(change.get("previous") or 0)
    amount = float(change.get("amount") or 0)
    drop = ((amount - prev) / prev * 100.0) if prev > 0 else 0.0
    return (
        f"{ticker} dividend on {change['date']} changed to ₹{amount:.2f}/share "
        f"(was ₹{prev:.2f}, {drop:+.1f}%)."
    )


def apply_dividend_alerts(holding: dict, changes: list, prior_alerts: list | None = None) -> dict:
    if not changes:
        return holding
    merged = dict(holding)
    alerts = list(prior_alerts or merged.get("dividendAlerts") or [])
    seen = {str(a.get("id") or "") for a in alerts}
    now = datetime.now(timezone.utc).isoformat()
    ticker = merged.get("ticker") or "?"
    for change in changes:
        alert_id = f"{change['type']}:{ticker}:{change['date']}"
        if alert_id in seen:
            for alert in alerts:
                if alert.get("id") == alert_id:
                    alert["dismissed"] = False
                    alert["message"] = _alert_message(ticker, change)
                    alert["amount"] = change.get("amount")
                    alert["previousAmount"] = change.get("previous")
                    alert["updatedAt"] = now
            continue
        alerts.insert(
            0,
            {
                "id": alert_id,
                "type": change["type"],
                "ticker": ticker,
                "date": change["date"],
                "amount": change.get("amount"),
                "previousAmount": change.get("previous"),
                "message": _alert_message(ticker, change),
                "createdAt": now,
                "updatedAt": now,
                "dismissed": False,
            },
        )
        seen.add(alert_id)
    merged["dividendAlerts"] = alerts[:40]
    return merged


def _largest_qty_quarter(holding, year: int) -> str | None:
    prev = year - 1
    best_key = None
    best_abs = -1
    for dividend in holding.get("dividends") or []:
        date = str(dividend.get("date") or "")
        if len(date) < 4:
            continue
        try:
            div_year = int(date[:4])
        except ValueError:
            continue
        if div_year != year:
            continue
        key = db.quarter_key(date)
        try:
            quarter = int(key.split("-Q", 1)[1])
        except (TypeError, ValueError, IndexError):
            continue
        prev_key = f"{prev}-Q{quarter}"
        delta = db._qty_for_quarter(holding, key) - db._qty_for_quarter(holding, prev_key)
        if abs(delta) > best_abs:
            best_abs = abs(delta)
            best_key = key
    return best_key


def income_attribution_tasks(holdings, year_value, analysis: dict | None = None) -> list:
    if not isinstance(year_value, int):
        return []
    year = year_value
    rows = []
    for holding in holdings:
        row = db.holding_yoy_attribution(holding, year)
        if not row or abs(float(row.get("delta") or 0)) < ATTRIBUTION_MIN_DELTA_ABS:
            continue
        if float(row.get("dominantSharePct") or 0) < ATTRIBUTION_DOMINANCE_PCT:
            continue
        rows.append(row)
    rows.sort(key=lambda r: abs(float(r.get("delta") or 0)), reverse=True)

    tasks = []
    for row in rows[:MAX_ATTRIBUTION_TASKS]:
        ticker = row["ticker"]
        delta = float(row["delta"])
        dominant = str(row.get("dominant") or "quantity")
        label = _DOMINANT_LABELS.get(dominant, dominant)
        share = float(row.get("dominantSharePct") or 0)
        sign = "+" if delta >= 0 else ""
        action = "focus-quarter" if dominant == "quantity" else "open-insight"
        holding = next((h for h in holdings if h.get("ticker") == ticker), None)
        quarter = _largest_qty_quarter(holding, year) if holding and dominant == "quantity" else None
        tasks.append(
            {
                "id": f"attrib:{ticker}:{year}",
                "priority": "p2",
                "type": "income-attribution",
                "ticker": ticker,
                "year": year,
                "quarter": quarter,
                "message": (
                    f"{ticker}: {sign}₹{abs(delta):,.0f} income vs {year - 1} is mostly "
                    f"{label} ({share:.0f}% of the move)."
                ),
                "action": action,
                "context": {
                    "dominant": dominant,
                    "delta": delta,
                    "dominantSharePct": share,
                },
            }
        )

    bridge = (analysis or {}).get("incomeBridge")
    if bridge and bridge.get("delta") is not None:
        total_delta = float(bridge["delta"])
        if abs(total_delta) >= ATTRIBUTION_BRIDGE_MIN_ABS:
            components = {c["key"]: float(c["amount"]) for c in bridge.get("components") or []}
            drivers = {k: abs(v) for k, v in components.items()}
            dominant = max(drivers, key=drivers.get)
            base = abs(total_delta)
            share = round(drivers[dominant] / base * 100, 1) if base > 0 else 0.0
            if share >= ATTRIBUTION_DOMINANCE_PCT:
                label = _DOMINANT_LABELS.get(dominant, dominant)
                sign = "+" if total_delta >= 0 else ""
                tasks.append(
                    {
                        "id": f"attrib:portfolio:{year}",
                        "priority": "p2",
                        "type": "income-attribution",
                        "year": year,
                        "message": (
                            f"Portfolio: {sign}₹{abs(total_delta):,.0f} vs {year - 1} is "
                            f"mostly {label} ({share:.0f}% of the move). See Analysis income bridge."
                        ),
                        "action": "open-analysis",
                        "context": {"dominant": dominant, "delta": total_delta, "scope": "portfolio"},
                    }
                )
    return tasks


def cashflow_stress_tasks(holdings, forecast_data: dict | None = None) -> list:
    if not holdings:
        return []
    import forecast

    report = forecast.stress_report(forecast_data)
    if not report:
        return []
    base = float(report.get("baseTotal") or 0)
    if base < CASHFLOW_STRESS_MIN_BASE:
        return []

    tasks = []
    gap_pct = report.get("baseVsConservativeGapPct")
    cons_total = float(report.get("conservativeTotal") or 0)
    if gap_pct is not None and gap_pct >= CASHFLOW_STRESS_GAP_PCT:
        tasks.append(
            {
                "id": "cashflow:base-vs-conservative",
                "priority": "p2",
                "type": "cashflow-stress",
                "message": (
                    f"12-month cashflow: base ₹{base:,.0f} vs conservative ₹{cons_total:,.0f} "
                    f"({gap_pct:.0f}% gap). Peak quantities or DPS may be optimistic."
                ),
                "action": "open-analysis",
                "context": {"stressKind": "base-vs-conservative", "gapPct": gap_pct},
            }
        )

    severe_pct = report.get("severeStressGapPct")
    severe_total = float(report.get("severeStressTotal") or 0)
    if severe_pct is not None and severe_pct >= CASHFLOW_STRESS_SEVERE_PCT:
        tasks.append(
            {
                "id": "cashflow:severe-shock",
                "priority": "p1",
                "type": "cashflow-stress",
                "message": (
                    f"Stress test (−10% qty & −10% DPS): 12-month cash falls to "
                    f"₹{severe_total:,.0f} ({severe_pct:.0f}% below base). "
                    "Review quantities and dividend assumptions."
                ),
                "action": "open-analysis",
                "context": {"stressKind": "severe", "gapPct": severe_pct},
            }
        )

    portfolio_gap = float(report.get("baseVsConservativeGapAbs") or 0)
    for row in (report.get("tickerGaps") or [])[:MAX_CASHFLOW_STRESS_TICKER_TASKS]:
        gap = float(row.get("gap") or 0)
        if gap < 100:
            continue
        if portfolio_gap > 0 and gap / portfolio_gap < 0.15:
            continue
        ticker = row.get("ticker") or "?"
        share = round(gap / base * 100, 1) if base > 0 else 0.0
        tasks.append(
            {
                "id": f"cashflow:gap:{ticker}",
                "priority": "p2",
                "type": "cashflow-stress",
                "ticker": ticker,
                "message": (
                    f"{ticker}: conservative 12-month forecast is ₹{gap:,.0f} below base "
                    f"({share:.0f}% of portfolio base scenario)."
                ),
                "action": "open-insight",
                "context": {"stressKind": "ticker-gap", "gap": gap},
            }
        )
    return tasks


def refresh_diff_tasks(holdings, year_value=None) -> list:
    from coach import _dividend_in_coach_year

    tasks = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        for alert in holding.get("dividendAlerts") or []:
            if alert.get("dismissed"):
                continue
            date = str(alert.get("date") or "")
            if year_value is not None and date and not _dividend_in_coach_year(date, year_value):
                continue
            priority = "p1" if alert.get("type") == "new-payout" else "p2"
            quarter = db.quarter_key(date) if len(date) >= 7 else None
            action = "focus-quarter" if alert.get("type") == "new-payout" else "open-insight"
            tasks.append(
                {
                    "id": f"alert:{alert.get('id') or ticker}",
                    "priority": priority,
                    "type": str(alert.get("type") or "dividend-alert"),
                    "ticker": ticker,
                    "date": date,
                    "quarter": quarter,
                    "message": str(alert.get("message") or ""),
                    "action": action,
                    "context": {"alertId": alert.get("id")},
                }
            )
    return tasks


def calendar_ics(holdings, year_value) -> str:
    events = upcoming_calendar_events(holdings, year_value)
    target_year = int(year_value) if year_value is not None else datetime.now(timezone.utc).year
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NSE Dividend Tracker//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for event in events:
        if event.get("type") != "upcoming-payout":
            continue
        month = int(event.get("month") or 1)
        ticker = event.get("ticker") or "?"
        dt = f"{target_year}{month:02d}15"
        uid = event.get("id") or f"{ticker}-{month}"
        summary = f"Dividend window: {ticker}"
        desc = str(event.get("message") or "").replace(",", "\\,")
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}@dividend-tracker",
                f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                f"DTSTART;VALUE=DATE:{dt}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{desc}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
