#!/usr/bin/env python3
"""Forward dividend cashflow forecast and quantity autofill (read-only math)."""

from __future__ import annotations

from datetime import datetime, timezone

import db

SCENARIOS = ("conservative", "base", "growth")
DEFAULT_HORIZON = 12


def _as_of_month():
    now = datetime.now(timezone.utc)
    return now.year, now.month


def _month_add(year: int, month: int, offset: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + offset
    return idx // 12, idx % 12 + 1


def _payout_months(holding) -> dict[int, float]:
    """Typical ex-month -> latest DPS observed in that month."""
    last_by_month: dict[int, float] = {}
    for div in holding.get("dividends") or []:
        date = str(div.get("date") or "")
        if len(date) < 7:
            continue
        try:
            month = int(date[5:7])
            amount = float(div.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            last_by_month[month] = amount
    return last_by_month


def _forecast_dps_for_month(holding, month: int, scenario: str, calendar_year: int) -> float:
    months = _payout_months(holding)
    base = months.get(month)
    if base is None:
        totals = holding.get("yearTotals") or {}
        try:
            annual = float(totals.get(str(calendar_year)) or totals.get(str(calendar_year - 1)) or 0)
        except (TypeError, ValueError):
            annual = 0.0
        if annual <= 0:
            return 0.0
        payout_months = len(months) or 1
        base = annual / payout_months
    if scenario == "conservative":
        return base * 0.95
    if scenario == "base":
        return base
    if scenario == "growth":
        series = db._dps_series_until(holding, calendar_year)
        cagr = db._clamp_cagr(db._dps_cagr(series))
        if len(series) >= db.GROWTH_MIN_YEARS and cagr is not None:
            return base * (1.0 + cagr)
        return base
    return base


def _forecast_qty(holding, calendar_year: int, month: int, scenario: str) -> int:
    quarter = (month - 1) // 3 + 1
    key = f"{calendar_year}-Q{quarter}"
    qty = db._qty_for_quarter(holding, key)
    if qty > 0:
        return qty
    if scenario == "conservative":
        return int(db._avg_quarter_qty(holding, calendar_year))
    peak = db._peak_quarter_qty(holding, calendar_year)
    if peak > 0:
        return peak
    for y in (calendar_year - 1, calendar_year - 2):
        peak = db._peak_quarter_qty(holding, y)
        if peak > 0:
            return peak
    return max(0, int(holding.get("shares") or 0))


def _holding_month_income(holding, year: int, month: int, scenario: str) -> float:
    months = _payout_months(holding)
    if month not in months and not (holding.get("yearTotals") or {}):
        return 0.0
    dps = _forecast_dps_for_month(holding, month, scenario, year)
    qty = _forecast_qty(holding, year, month, scenario)
    return round(dps * qty, 2)


def build(horizon_months: int = DEFAULT_HORIZON) -> dict:
    horizon = max(1, min(int(horizon_months or DEFAULT_HORIZON), 36))
    holdings = db.load_holdings()
    start_year, start_month = _as_of_month()
    out_scenarios = {}
    covered = 0
    no_history = 0
    qty_gaps = 0

    for holding in holdings:
        if _payout_months(holding) or (holding.get("yearTotals") or {}):
            covered += 1
        else:
            no_history += 1
        if (holding.get("dividends") or []) and not (holding.get("quarters") or {}):
            qty_gaps += 1

    for scenario in SCENARIOS:
        by_month: dict[str, dict] = {}
        for offset in range(horizon):
            y, m = _month_add(start_year, start_month, offset)
            ym = f"{y}-{m:02d}"
            by_month.setdefault(ym, {"month": ym, "income": 0.0, "payouts": 0, "tickers": []})
            for holding in holdings:
                income = _holding_month_income(holding, y, m, scenario)
                if income <= 0:
                    continue
                by_month[ym]["income"] += income
                by_month[ym]["payouts"] += 1
                ticker = holding.get("ticker") or "?"
                if ticker not in by_month[ym]["tickers"]:
                    by_month[ym]["tickers"].append(ticker)
        months = []
        total = 0.0
        for ym in sorted(by_month.keys()):
            row = by_month[ym]
            row["income"] = round(row["income"], 2)
            row["tickers"] = sorted(row["tickers"])
            total += row["income"]
            months.append(row)
        out_scenarios[scenario] = {
            "total": round(total, 2),
            "avgMonthly": round(total / horizon, 2) if horizon else 0.0,
            "byMonth": months,
        }

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "horizonMonths": horizon,
        "startMonth": f"{start_year}-{start_month:02d}",
        "scenarios": out_scenarios,
        "coverage": {
            "holdings": len(holdings),
            "withPayoutHistory": covered,
            "withoutHistory": no_history,
            "missingQuantityMaps": qty_gaps,
        },
        "assumptions": [
            "Payout months follow the most recent dividend dates per stock.",
            "Quantities use the selected calendar year's quarters when set; otherwise peak/avg priors.",
            "Growth scenario applies capped per-share CAGR when at least three payout years exist.",
            "Illustrative cash timing only — not a guarantee of future dividends or dates.",
        ],
    }


def autofill_suggestions(holding, year: int) -> list[dict]:
    suggestions = []
    for q in range(1, 5):
        key = f"{year}-Q{q}"
        if db._qty_for_quarter(holding, key) > 0:
            continue
        prev_key = f"{year - 1}-Q{q}"
        prev_qty = db._qty_for_quarter(holding, prev_key)
        if prev_qty > 0:
            suggestions.append(
                {
                    "quarter": key,
                    "quantity": prev_qty,
                    "reason": "same_quarter_prior_year",
                }
            )
            continue
        peak_prev = db._peak_quarter_qty(holding, year - 1)
        if peak_prev > 0:
            suggestions.append(
                {
                    "quarter": key,
                    "quantity": peak_prev,
                    "reason": "peak_quarter_prior_year",
                }
            )
    return suggestions


def autofill_plan(holdings, year: int) -> dict:
    rows = []
    tasks = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        suggestions = autofill_suggestions(holding, year)
        if not suggestions:
            continue
        rows.append({"ticker": ticker, "suggestions": suggestions})
        tasks.append(
            {
                "id": f"autofill:{ticker}:{year}",
                "priority": "p1",
                "type": "autofill-quantity",
                "ticker": ticker,
                "year": year,
                "message": (
                    f"Auto-fill {len(suggestions)} blank quarter(s) for {ticker} in {year} "
                    f"from last year's quantities."
                ),
                "action": "apply-autofill",
                "context": {"count": len(suggestions)},
            }
        )
    return {
        "year": year,
        "holdings": len(rows),
        "quarters": sum(len(r["suggestions"]) for r in rows),
        "rows": rows,
        "tasks": tasks,
    }


def apply_autofill_row(holding, year: int) -> dict | None:
    suggestions = autofill_suggestions(holding, year)
    if not suggestions:
        return None
    merged = dict(holding)
    quarters = dict(merged.get("quarters") or {})
    times = dict(merged.get("quarterUpdatedAt") or {})
    stamp = datetime.now(timezone.utc).isoformat()
    for item in suggestions:
        key = item["quarter"]
        quarters[key] = int(item["quantity"])
        times[key] = stamp
    merged["quarters"] = quarters
    merged["quarterUpdatedAt"] = times
    merged["updatedAt"] = stamp
    return db.save_holding(merged)
