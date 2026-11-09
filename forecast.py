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


def _schedule_year(holding, as_of_year: int) -> int | None:
    """Latest calendar year with dividend-per-share data at or before as_of_year."""
    totals = holding.get("yearTotals") or {}
    years = []
    for key, value in totals.items():
        try:
            year = int(key)
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if year <= as_of_year and amount > 0:
            years.append(year)
    if years:
        return max(years)
    years = []
    for div in holding.get("dividends") or []:
        date = str(div.get("date") or "")
        if len(date) < 4:
            continue
        try:
            year = int(date[:4])
        except ValueError:
            continue
        if year <= as_of_year:
            years.append(year)
    return max(years) if years else None


def _payout_months(holding, as_of_year: int | None = None) -> dict[int, float]:
    """Payment months and total DPS per month from the latest dividend year (not all history)."""
    if as_of_year is None:
        as_of_year = _as_of_month()[0]
    schedule_year = _schedule_year(holding, as_of_year)
    if schedule_year is None:
        return {}
    by_month: dict[int, float] = {}
    for div in holding.get("dividends") or []:
        date = str(div.get("date") or "")
        if len(date) < 7:
            continue
        try:
            year = int(date[:4])
            month = int(date[5:7])
            amount = float(div.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if year != schedule_year or amount <= 0:
            continue
        by_month[month] = round(by_month.get(month, 0.0) + amount, 4)
    return by_month


def _forecast_dps_for_month(holding, month: int, scenario: str, calendar_year: int) -> float:
    months = _payout_months(holding, as_of_year=calendar_year)
    base = months.get(month)
    if base is None:
        return 0.0
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
    months = _payout_months(holding, as_of_year=year)
    if month not in months:
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

    compare_year = start_year
    projection_next = db._scenario_income(holdings, compare_year, "base")

    for holding in holdings:
        if _payout_months(holding, as_of_year=start_year) or (holding.get("yearTotals") or {}):
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
        "compare": {
            "nextYearBaseFromAnalysis": projection_next,
            "hint": (
                "Next year (base) is one full calendar year at peak qty × annual DPS. "
                "Rolling 12 months is when cash may land from today — usually similar, not 2×."
            ),
        },
        "assumptions": [
            "Payment months come from the latest dividend year per stock, not every month ever seen in history.",
            "Quantities use the calendar year of each forecast month; otherwise peak/avg priors.",
            "Growth scenario applies capped per-share CAGR when at least three payout years exist.",
            "Monthly average is the 12-month total ÷ 12 (lumpy months, not a flat monthly cheque).",
            "Illustrative timing only — not a guarantee of future dividends or dates.",
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


STRESS_QTY_MULT = 0.90
STRESS_DPS_MULT = 0.90


def _stress_holding_month(holding, year: int, month: int, qty_mult: float, dps_mult: float) -> float:
    months = _payout_months(holding, as_of_year=year)
    if month not in months:
        return 0.0
    dps = _forecast_dps_for_month(holding, month, "base", year) * dps_mult
    qty = _forecast_qty(holding, year, month, "base")
    if qty_mult != 1.0:
        qty = max(0, int(round(qty * qty_mult)))
    return round(dps * qty, 2)


def _stress_total(holdings, horizon: int, qty_mult: float, dps_mult: float) -> float:
    start_year, start_month = _as_of_month()
    total = 0.0
    for offset in range(horizon):
        y, m = _month_add(start_year, start_month, offset)
        for holding in holdings:
            total += _stress_holding_month(holding, y, m, qty_mult, dps_mult)
    return round(total, 2)


def _ticker_scenario_gaps(holdings, horizon: int) -> list:
    start_year, start_month = _as_of_month()
    rows = []
    for holding in holdings:
        ticker = holding.get("ticker") or "?"
        base_t = 0.0
        cons_t = 0.0
        for offset in range(horizon):
            y, m = _month_add(start_year, start_month, offset)
            base_t += _holding_month_income(holding, y, m, "base")
            cons_t += _holding_month_income(holding, y, m, "conservative")
        gap = round(base_t - cons_t, 2)
        if gap > 0:
            rows.append(
                {
                    "ticker": ticker,
                    "base": round(base_t, 2),
                    "conservative": round(cons_t, 2),
                    "gap": gap,
                }
            )
    return sorted(rows, key=lambda r: r["gap"], reverse=True)


def stress_report(forecast_data: dict | None = None, horizon: int = DEFAULT_HORIZON) -> dict:
    """Compare base, conservative, and explicit ±10% qty/DPS shocks over the horizon."""
    holdings = db.load_holdings()
    if not holdings:
        return {}
    data = forecast_data or build(horizon)
    months = int(data.get("horizonMonths") or horizon)
    scenarios = data.get("scenarios") or {}
    base = float(scenarios.get("base", {}).get("total") or 0)
    cons = float(scenarios.get("conservative", {}).get("total") or 0)
    growth = float(scenarios.get("growth", {}).get("total") or 0)
    gap_abs = round(base - cons, 2)
    gap_pct = round(gap_abs / base * 100, 1) if base > 0 else None
    severe = _stress_total(holdings, months, STRESS_QTY_MULT, STRESS_DPS_MULT)
    severe_gap_pct = round((base - severe) / base * 100, 1) if base > 0 else None
    return {
        "horizonMonths": months,
        "baseTotal": base,
        "conservativeTotal": cons,
        "growthTotal": growth,
        "baseVsConservativeGapAbs": gap_abs,
        "baseVsConservativeGapPct": gap_pct,
        "severeStressTotal": severe,
        "severeStressGapPct": severe_gap_pct,
        "qtyDown10Total": _stress_total(holdings, months, STRESS_QTY_MULT, 1.0),
        "dpsDown10Total": _stress_total(holdings, months, 1.0, STRESS_DPS_MULT),
        "tickerGaps": _ticker_scenario_gaps(holdings, months),
    }
