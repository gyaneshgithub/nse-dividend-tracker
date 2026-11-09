"""Tests for forward cashflow forecast and autofill."""

from __future__ import annotations

import forecast


def test_forecast_builds_monthly_totals(db):
    db.save_holding(
        {
            "ticker": "ITC",
            "name": "ITC",
            "shares": 10,
            "quarters": {"2026-Q1": 10, "2026-Q2": 10, "2026-Q3": 10, "2026-Q4": 10},
            "dividends": [
                {"date": "2025-02-01", "amount": 2.0},
                {"date": "2026-02-01", "amount": 2.0},
            ],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    data = forecast.build(6)
    assert data["horizonMonths"] == 6
    base = data["scenarios"]["base"]
    assert base["total"] > 0
    assert len(base["byMonth"]) == 6


def test_autofill_from_prior_year(db):
    db.save_holding(
        {
            "ticker": "INFY",
            "name": "Infosys",
            "shares": 5,
            "quarters": {"2025-Q1": 8, "2025-Q2": 8},
            "dividends": [{"date": "2026-02-01", "amount": 2.0}],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    plan = forecast.autofill_plan(db.load_holdings(), 2026)
    assert plan["quarters"] >= 2
    row = plan["rows"][0]
    assert row["ticker"] == "INFY"
    updated = forecast.apply_autofill_row(db.load_holdings()[0], 2026)
    assert updated
    assert updated["quarters"].get("2026-Q1") == 8
