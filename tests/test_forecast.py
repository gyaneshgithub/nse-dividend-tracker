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


def test_forecast_ignores_ancient_bonus_months(db):
    db.save_holding(
        {
            "ticker": "TATASTEEL",
            "name": "Tata Steel",
            "shares": 10,
            "quarters": {"2026-Q1": 10},
            "dividends": [
                {"date": "2018-01-01", "amount": 50.0},
                {"date": "2025-08-15", "amount": 3.0},
                {"date": "2025-02-15", "amount": 2.0},
            ],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    data = forecast.build(12)
    assert data["scenarios"]["base"]["total"] <= 60.0


def test_forecast_only_payout_months(db):
    db.save_holding(
        {
            "ticker": "ITC",
            "name": "ITC",
            "shares": 100,
            "quarters": {"2026-Q3": 100},
            "dividends": [{"date": "2025-08-15", "amount": 5.0}],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    data = forecast.build(12)
    with_itc = [row for row in data["scenarios"]["base"]["byMonth"] if "ITC" in row["tickers"]]
    assert len(with_itc) <= 2
    assert sum(row["income"] for row in with_itc) <= 501.0
    assert sum(row["income"] for row in data["scenarios"]["base"]["byMonth"]) <= 501.0


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
