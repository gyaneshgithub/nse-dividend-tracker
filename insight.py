#!/usr/bin/env python3
"""Portfolio insight scoring for the Dividend Tracker.

Why this module exists
----------------------
The dashboard answers "how much income did I get?". It does not answer "how
healthy is that income?". This module derives that second answer from data the
tracker already holds, with no network calls, no language model and no writes.

Everything here is deterministic and read-only:
  * inputs come from db.load_holdings() and db.analysis()
  * nothing is stored, cached to disk, or sent anywhere
  * the same input always produces the same output

The score is deliberately explainable. Every component is a 0-100 number with
a published formula, and the response carries the weights and thresholds so the
dashboard can show its own working. Components that need data the tracker does
not have yet (price history, Google Trends) are listed as deferred rather than
silently scored as zero.
"""

import statistics
from datetime import datetime, timezone

import db
import forecast
from db import _yield_pct

# Weights for the components that can be computed offline today. When the
# deferred components arrive they join this table and the rest are
# renormalised, so no score is ever dragged down by missing data.
WEIGHTS = {
    "income": 0.40,
    "growth": 0.30,
    "diversification": 0.30,
    "valuation": 0.15,
}

VALUATION_MIN_SNAPSHOTS = 5

DEFERRED_COMPONENTS = [
    {"key": "technicals", "label": "Technical posture", "phase": 3,
     "reason": "needs daily price history for moving averages and RSI"},
    {"key": "youth", "label": "Young-consumer exposure", "phase": 5,
     "reason": "needs Google Trends and the youth-consumption basket"},
]

THRESHOLDS = {
    "stability": "100 × (1 − coefficient of variation of annual dividend per share over the last 5 years)",
    "streak": "100 × consecutive years with a payout ÷ 5, capped at 100",
    "cutPenalty": "25 points deducted from a holding's income score for each dividend cut (>15% year on year) in the last 5 years",
    "growth": "50 + 250 × dividend CAGR, clamped to 0-100",
    "projection": "50 + 150 × projected increase, clamped to 0-100",
    "diversification": "100 × (1 − normalised HHI) × min(1, holdings ÷ 8), minus 100 × max(0, top-3 share − 0.5) once there are more than three holdings, clamped to 0-100",
}

DISCLAIMER = (
    "Descriptive analysis of your recorded dividend history, not investment advice. "
    "Scores are relative measures of this portfolio's own data; they do not forecast returns."
)


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _inr(value):
    """Format rupees with Indian digit grouping (1,83,435)."""
    amount = int(round(float(value or 0)))
    sign = "-" if amount < 0 else ""
    digits = str(abs(amount))
    if len(digits) <= 3:
        return f"{sign}₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join(groups)},{tail}"


def _dps_series(holding):
    """Annual dividend per share, oldest first, ignoring empty years."""
    totals = holding.get("yearTotals") or {}
    series = []
    for key, value in totals.items():
        try:
            year = int(key)
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            series.append((year, round(amount, 4)))
    series.sort()
    return series


def _series_until(holding, year):
    """Dividend series truncated to a year, so a past view is not a forecast.

    A 2024 insight must not know about a 2026 cut. ``year=None`` means all
    recorded history.
    """
    series = _dps_series(holding)
    if year is None:
        return series
    return [(series_year, amount) for series_year, amount in series if series_year <= year]


def _stability(series):
    """100 = identical annual dividend, 0 = wildly uneven.

    Coefficient of variation is used instead of raw volatility so a stock that
    pays ₹2 and one that pays ₹20 are compared on the same scale.
    """
    values = [amount for _, amount in series[-5:]]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    cv = statistics.pstdev(values) / mean
    return _clamp(100.0 * (1.0 - min(1.0, cv)))


def _cagr(series):
    """Compound annual growth of the annual dividend per share, or None."""
    if len(series) < 2:
        return None
    first_year, first = series[0]
    last_year, last = series[-1]
    span = last_year - first_year
    if span <= 0 or first <= 0:
        return None
    return (last / first) ** (1.0 / span) - 1.0


def _streak(series):
    """Consecutive calendar years with a payout, ending at the latest year."""
    if not series:
        return 0
    years = {year for year, _ in series}
    year = series[-1][0]
    count = 0
    while year in years:
        count += 1
        year -= 1
    return count


def _cuts(series):
    """Year-on-year cuts greater than 15% within the last five years, newest first."""
    if not series:
        return []
    latest = series[-1][0]
    window = [(year, amount) for year, amount in series if year >= latest - 4]
    out = []
    for (prev_year, prev), (year, current) in zip(window, window[1:]):
        if year != prev_year + 1 or prev <= 0:
            continue
        if current < prev * 0.85:
            out.append(
                {
                    "year": year,
                    "from": round(prev, 4),
                    "to": round(current, 4),
                    "dropPct": round((current - prev) / prev * 100, 1),
                }
            )
    out.sort(key=lambda item: item["year"], reverse=True)
    return out


def _valuation_score(holding):
    history = holding.get("priceHistory") or []
    yields = [float(row["yieldPct"]) for row in history if row.get("yieldPct") is not None]
    current = _yield_pct(holding)
    if current is None or len(yields) < VALUATION_MIN_SNAPSHOTS:
        return None, None
    median = statistics.median(yields)
    if median <= 0:
        return None, None
    ratio = current / median
    score = _clamp(100.0 - 200.0 * (ratio - 1.0))
    versus = round((ratio - 1.0) * 100, 1)
    return round(score, 1), versus


def _holding_insight(holding, share_pct, year=None):
    series = _series_until(holding, year)
    stability = _stability(series)
    cagr = _cagr(series)
    streak = _streak(series)
    cuts = _cuts(series)
    valuation_score, yieldVsMedianPct = _valuation_score(holding)

    stability_score = None
    if stability is not None:
        stability_score = _clamp(stability - 25.0 * len(cuts))
    streak_score = _clamp(streak / 5.0 * 100.0)
    resilience = None
    if stability_score is not None:
        resilience = 0.7 * stability_score + 0.3 * streak_score

    parts = []
    if resilience is not None:
        parts.append((0.7, resilience))
    if cagr is not None:
        parts.append((0.3, _clamp(50.0 + 250.0 * cagr)))
    if not parts:
        score = None
    else:
        weight_total = sum(weight for weight, _ in parts)
        score = sum(weight * value for weight, value in parts) / weight_total

    return {
        "ticker": holding.get("ticker"),
        "name": holding.get("name") or holding.get("ticker"),
        "score": None if score is None else round(score, 1),
        "resilience": None if resilience is None else round(resilience, 1),
        "stability": None if stability is None else round(stability, 1),
        "cagrPct": None if cagr is None else round(cagr * 100, 2),
        "streak": streak,
        "cuts": cuts,
        "sharePct": round(float(share_pct or 0), 2),
        "dps": [{"year": year, "amount": amount} for year, amount in series[-5:]],
        "hasPrice": bool(holding.get("price")),
        "yearsOfHistory": len(series),
        "valuationScore": valuation_score,
        "yieldVsMedianPct": yieldVsMedianPct,
        "priceSnapshots": len(holding.get("priceHistory") or []),
    }


def _weighted(values, weights):
    """Weighted mean that ignores entries with no weight."""
    total = 0.0
    weight_total = 0.0
    for value, weight in zip(values, weights):
        if value is None or weight <= 0:
            continue
        total += value * weight
        weight_total += weight
    if weight_total <= 0:
        return None
    return total / weight_total


def _component(key, label, score, weight, note):
    return {
        "key": key,
        "label": label,
        "score": None if score is None else round(_clamp(score), 1),
        "weight": weight,
        "note": note,
    }


def _concentration(rows, total_income):
    """HHI plus top-1 and top-3 income share. Zeros when there is no income."""
    shares = [row["sharePct"] / 100.0 for row in rows if row["sharePct"] > 0]
    count = len(shares)
    if not shares or total_income <= 0:
        return {"hhi": None, "normalisedHhi": None, "top1Pct": None, "top3Pct": None, "count": count}
    hhi = sum(share * share for share in shares)
    normalised = (hhi - 1.0 / count) / (1.0 - 1.0 / count) if count > 1 else 1.0
    ordered = sorted(shares, reverse=True)
    return {
        "hhi": round(hhi, 4),
        "normalisedHhi": round(normalised, 4),
        "top1Pct": round(ordered[0] * 100, 1),
        "top3Pct": round(sum(ordered[:3]) * 100, 1),
        "count": count,
    }


def _diversification_score(concentration):
    if concentration["normalisedHhi"] is None:
        return None
    base = 100.0 * (1.0 - concentration["normalisedHhi"])
    # An even split across two names is still a fragile portfolio, so the
    # balance score is capped by breadth: fewer than eight income sources can
    # never score as fully diversified.
    breadth = min(1.0, concentration["count"] / 8.0)
    base *= breadth
    # The top-three test only means something once there are more than three
    # holdings; otherwise "top 3" is trivially the whole portfolio.
    penalty = 0.0
    if concentration["count"] > 3:
        penalty = max(0.0, (concentration["top3Pct"] or 0) / 100.0 - 0.5) * 100.0
    return _clamp(base - penalty)


def _band(score):
    if score is None:
        return "unknown"
    if score >= 75:
        return "strong"
    if score >= 60:
        return "steady"
    if score >= 40:
        return "uneven"
    return "fragile"


def _headline(band, concentration, cuts, holdings_count):
    if holdings_count == 0:
        return "No holdings yet"
    if concentration["top1Pct"] is not None and concentration["top1Pct"] >= 40:
        return "Income leans heavily on one holding"
    if holdings_count <= 3:
        return "Income comes from a small number of holdings"
    if band == "strong":
        return "Well-spread income with a steady dividend record"
    if band == "steady":
        return "Solid income base with a few concentration risks"
    if band == "uneven":
        return "Income is meaningful but uneven"
    if cuts:
        return "Income depends on a small number of names and has recent cuts"
    return "Income depends on a small number of names"


def _narrative(year_label, stats, rows, concentration, cuts, holdings_count):
    total_income = stats["totalIncome"]
    currency_income = _inr(total_income)
    strengths = []
    risks = []
    watchlist = []

    if holdings_count == 0:
        return {
            "headline": "No holdings yet",
            "summary": "Add stocks and refresh their dividends to generate a portfolio insight.",
            "strengths": [],
            "risks": [],
            "watchlist": [],
            "caveats": [DISCLAIMER],
        }

    if total_income <= 0:
        payouts = int(stats.get("payouts") or 0)
        if payouts:
            summary = (
                f"{holdings_count} stock{'s' if holdings_count != 1 else ''} recorded {payouts} "
                f"dividend payout{'s' if payouts != 1 else ''} in {year_label}, but no quantities "
                "are recorded for those quarters, so the income is zero."
            )
            risks = [
                "Quarterly quantities are blank for this period. Enter the shares held in each "
                "quarter on the dashboard to score the income."
            ]
        else:
            summary = (
                f"No dividend payouts are recorded for {year_label}. The tracker holds "
                f"{holdings_count} stock{'s' if holdings_count != 1 else ''}, so there is no income "
                "to score for this period."
            )
            risks = ["No dividend payouts fall in this period."]
        return {
            "headline": "No income recorded for this period",
            "summary": summary,
            "strengths": [],
            "risks": risks,
            "watchlist": [],
            "caveats": [DISCLAIMER],
        }

    summary = (
        f"{holdings_count} holding{'s' if holdings_count != 1 else ''} produced "
        f"{currency_income} of dividend income in {year_label}."
    )
    if concentration["top1Pct"] is not None:
        if (concentration["count"] or 0) > 3:
            summary += (
                f" The top three names account for {concentration['top3Pct']:.1f}% of that income"
                f" and the largest single name for {concentration['top1Pct']:.1f}%."
            )
        else:
            summary += (
                f" The largest single name accounts for {concentration['top1Pct']:.1f}% of that income."
            )
    if stats.get("projectedNextYearIncome") is not None:
        summary += (
            f" Carrying this year's peak quarterly quantity forward at the same dividend "
            f"gives {_inr(stats['projectedNextYearIncome'])} next year"
        )
        if stats.get("projectedIncreasePct") is not None:
            summary += f", a change of {stats['projectedIncreasePct']:+.1f}%."
        else:
            summary += "."
    if stats.get("forwardCashflow12m") is not None:
        summary += (
            f" The rolling 12-month cashflow outlook (base scenario) is about "
            f"{_inr(stats['forwardCashflow12m'])}"
        )
        if stats.get("forwardCashflowMonthly") is not None:
            summary += f", roughly {_inr(stats['forwardCashflowMonthly'])} per month on average."
        else:
            summary += "."

    stable = [row for row in rows if (row["stability"] or 0) >= 70]
    if stable:
        income = sum(row["sharePct"] for row in stable)
        strengths.append(
            f"{len(stable)} holding{'s' if len(stable) != 1 else ''} with a stable dividend "
            f"record supply {income:.1f}% of this year's income."
        )
    growers = [row for row in rows if (row["cagrPct"] or 0) >= 8 and (row["yearsOfHistory"] or 0) >= 3]
    if growers:
        strengths.append(
            f"{len(growers)} holding{'s' if len(growers) != 1 else ''} grew the dividend per "
            f"share by at least 8% a year across their recorded history."
        )
    if stats.get("avgYieldPct") is not None:
        strengths.append(f"Average trailing yield is {stats['avgYieldPct']:.2f}% across stocks with a known price.")

    if concentration["top3Pct"] is not None and concentration["top3Pct"] > 50 and (concentration["count"] or 0) > 3:
        risks.append(
            f"Income concentration is high: the top three holdings produce "
            f"{concentration['top3Pct']:.1f}% of the total."
        )
    if concentration["top1Pct"] is not None and concentration["top1Pct"] > 25:
        risks.append(
            f"A single holding produces {concentration['top1Pct']:.1f}% of income, so one "
            "dividend decision moves the total."
        )
    if cuts:
        names = ", ".join(f"{cut['ticker']} ({cut['year']})" for cut in cuts[:3])
        risks.append(
            f"{len(cuts)} dividend cut{'s' if len(cuts) != 1 else ''} in the last five years: {names}."
        )
    thin = [row for row in rows if (row["yearsOfHistory"] or 0) < 3]
    if thin:
        risks.append(
            f"{len(thin)} holding{'s have' if len(thin) != 1 else ' has'} less than three years of "
            "recorded dividend history, so their stability score is less reliable."
        )
    no_price = [row for row in rows if not row["hasPrice"]]
    if no_price:
        risks.append(
            f"{len(no_price)} holding{'s have' if len(no_price) != 1 else ' has'} no price, so dividend "
            "yield is excluded from the averages."
        )

    for cut in cuts[:5]:
        watchlist.append(
            f"{cut['ticker']}: dividend fell {abs(cut['dropPct']):.1f}% in {cut['year']} "
            f"({cut['from']:.2f} to {cut['to']:.2f} per share)."
        )
    weakest = sorted(
        [row for row in rows if row["score"] is not None and row["sharePct"] > 0],
        key=lambda row: row["score"],
    )[:3]
    for row in weakest:
        if row["score"] < 45:
            watchlist.append(
                f"{row['ticker']}: low income score ({row['score']:.0f}/100) with "
                f"{row['sharePct']:.1f}% of income."
            )

    caveats = [
        DISCLAIMER,
        "Scores use the dividend history stored in this tracker and update when that data is refreshed.",
        "Projections assume the highest quarterly quantity is held all year and the same dividends repeat.",
        "Technical, valuation and young-consumer signals are planned for a later phase and are not scored yet.",
    ]

    return {
        "headline": _headline(
            _band(stats.get("overall")), concentration, cuts, holdings_count
        ),
        "summary": summary,
        "strengths": strengths,
        "risks": risks,
        "watchlist": watchlist,
        "caveats": caveats,
    }


def build(year="all"):
    """Return the full insight payload for one year. Read-only."""
    try:
        year_value = None if str(year).lower() in ("all", "", "none") else int(year)
    except (TypeError, ValueError):
        year_value = None
    year_label = "all recorded years" if year_value is None else str(year_value)

    holdings = db.load_holdings()
    result = db.analysis("all" if year_value is None else year_value)

    by_stock = {row["ticker"]: row for row in result.get("byStock", [])}
    share = {ticker: row.get("sharePct") or 0 for ticker, row in by_stock.items()}

    rows = [_holding_insight(holding, share.get(holding["ticker"], 0), year_value) for holding in holdings]
    rows.sort(key=lambda row: (row["sharePct"], row["score"] or 0), reverse=True)

    total_income = float(result.get("totalIncome") or 0)
    concentration = _concentration(rows, total_income)

    # Income resilience: stability and uninterrupted payment history matter
    # more than size, so holdings are weighted by the income they actually
    # produce. An empty-weight portfolio falls back to equal weighting.
    income_score = _weighted(
        [row["resilience"] for row in rows],
        [row["sharePct"] for row in rows],
    )
    if income_score is None and rows:
        income_score = _weighted(
            [row["resilience"] for row in rows],
            [1.0] * len(rows),
        )

    cagr_rows = [row for row in rows if row["cagrPct"] is not None]
    cagr_score = _weighted(
        [_clamp(50.0 + 250.0 * (row["cagrPct"] / 100.0)) for row in cagr_rows],
        [row["sharePct"] or 1.0 for row in cagr_rows],
    )
    uplift = result.get("projectedIncreasePct")
    uplift_score = None if uplift is None else _clamp(50.0 + 150.0 * (uplift / 100.0))
    if cagr_score is None:
        growth_score = uplift_score
    elif uplift_score is None:
        growth_score = cagr_score
    else:
        growth_score = 0.7 * cagr_score + 0.3 * uplift_score

    diversification_score = _diversification_score(concentration)

    valuation_rows = [row for row in rows if row.get("valuationScore") is not None]
    valuation_score = _weighted(
        [row["valuationScore"] for row in valuation_rows],
        [row["sharePct"] or 1.0 for row in valuation_rows],
    )

    components = [
        _component(
            "income", "Income resilience", income_score, WEIGHTS["income"],
            "Stability of each holding's annual dividend, how many years it has paid without a break, and a penalty for cuts.",
        ),
        _component(
            "growth", "Dividend growth", growth_score, WEIGHTS["growth"],
            "Income-weighted dividend CAGR, blended with the projected next-year change.",
        ),
        _component(
            "diversification", "Diversification", diversification_score, WEIGHTS["diversification"],
            "How evenly income is spread across holdings, with an extra penalty when the top three dominate.",
        ),
        _component(
            "valuation",
            "Yield vs own history",
            valuation_score,
            WEIGHTS["valuation"],
            "Compares today's yield with the stock's own refresh history (at least five snapshots).",
        ),
    ]

    available = [item for item in components if item["score"] is not None]
    weight_total = sum(item["weight"] for item in available)
    overall = None
    # A period with no recorded income has no income health to score. The
    # component values stay visible, but the headline number is withheld so
    # an empty year cannot look like a strong year.
    if weight_total > 0 and total_income > 0:
        overall = round(sum(item["score"] * item["weight"] for item in available) / weight_total, 1)

    with_history = sum(1 for row in rows if row["yearsOfHistory"] >= 3)
    with_price = sum(1 for row in rows if row["hasPrice"])
    with_income = sum(1 for row in rows if row["sharePct"] > 0)
    coverage_parts = []
    if rows:
        coverage_parts.append(with_history / len(rows))
        coverage_parts.append(with_price / len(rows))
        if total_income > 0:
            coverage_parts.append(with_income / len(rows))
    confidence = round(sum(coverage_parts) / len(coverage_parts), 2) if coverage_parts else 0.0

    cuts = [
        {"ticker": row["ticker"], **cut}
        for row in rows
        for cut in row["cuts"]
    ]
    cuts.sort(key=lambda cut: (cut["year"], cut["ticker"]), reverse=True)

    forward = forecast.build(12) if rows else None
    forward_base = None
    forward_avg = None
    if forward:
        base = (forward.get("scenarios") or {}).get("base") or {}
        forward_base = base.get("total")
        forward_avg = base.get("avgMonthly")

    stats = {
        "holdings": len(rows),
        "totalIncome": total_income,
        "forwardCashflow12m": forward_base,
        "forwardCashflowMonthly": forward_avg,
        "payouts": int(result.get("totalPayouts") or 0),
        "avgYieldPct": result.get("avgYieldPct"),
        "projectedNextYearIncome": result.get("projectedNextYearIncome"),
        "projectedIncreasePct": result.get("projectedIncreasePct"),
        "projections": result.get("projections"),
        "top1Pct": concentration["top1Pct"],
        "top3Pct": concentration["top3Pct"],
        "hhi": concentration["hhi"],
        "holdingsWithHistory": with_history,
        "incomeSources": with_income,
        "holdingsWithoutPrice": len(rows) - with_price,
        "cuts": cuts,
        "overall": overall,
    }

    income_by_year = sorted(result.get("years", []), key=lambda row: row["year"])
    top = [row for row in rows if row["sharePct"] > 0][:6]
    covered = sum(row["sharePct"] for row in top)
    others = max(0.0, round(sum(row["sharePct"] for row in rows if row["sharePct"] > 0) - covered, 2))
    concentration_chart = [
        {"label": row["ticker"], "name": row["name"], "sharePct": row["sharePct"]}
        for row in top
    ]
    if others > 0.05:
        concentration_chart.append(
            {"label": "Other", "name": f"{max(0, len(rows) - len(top))} more holdings", "sharePct": others}
        )

    narrative = _narrative(year_label, stats, rows, concentration, cuts, len(rows))

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "year": "all" if year_value is None else year_value,
        "yearLabel": year_label,
        "currency": "INR",
        "scores": {
            "overall": overall,
            "band": _band(overall),
            "confidence": confidence,
            "components": components,
            "deferred": DEFERRED_COMPONENTS,
        },
        "narrative": narrative,
        "stats": stats,
        "charts": {
            "incomeByYear": [
                {"year": row["year"], "income": round(float(row["income"]), 2)}
                for row in income_by_year
            ],
            "concentration": concentration_chart,
            "holdings": rows,
        },
        "method": {
            "weights": WEIGHTS,
            "thresholds": THRESHOLDS,
            "deferred": DEFERRED_COMPONENTS,
            "inputs": ["holdings", "quarter quantities", "dividend history", "prices"],
        },
        "disclaimer": DISCLAIMER,
    }
