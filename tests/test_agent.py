"""Tests for the deterministic portfolio agent."""

from __future__ import annotations

import agent
import coach


def test_reconcile_gap_task(db):
    db.save_holding(
        {
            "ticker": "TCS",
            "name": "TCS",
            "shares": 10,
            "quarters": {"2026-Q1": 10},
            "dividends": [{"date": "2026-02-01", "amount": 10.0}],
            "reconcile": {"2026": {"actual": 30.0, "updatedAt": "2026-01-02T00:00:00+00:00"}},
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    tasks = agent.reconcile_tasks(db.load_holdings(), 2026)
    gaps = [t for t in tasks if t["type"] == "reconcile-gap"]
    assert len(gaps) == 1
    assert gaps[0]["ticker"] == "TCS"
    assert gaps[0]["action"] == "open-reconcile"


def test_import_plan_update_mode(db):
    db.save_holding(
        {
            "ticker": "ITC",
            "name": "ITC",
            "shares": 5,
            "quarters": {"2026-Q1": 5},
            "dividends": [{"date": "2026-02-01", "amount": 2.0}],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    plan = agent.import_plan(
        db.load_holdings(),
        [{"ticker": "ITC", "qtys": [8, 8, 8, 8]}, {"ticker": "INFY", "qtys": [1, 1, 1, 1]}],
        2026,
        mode="upsert",
    )
    assert plan["counts"]["update"] == 1
    assert plan["counts"]["add"] == 1
    by = {r["ticker"]: r for r in plan["rows"]}
    assert by["ITC"]["action"] == "update"
    assert by["INFY"]["action"] == "add"


def test_export_csv_includes_reconcile(db):
    db.save_holding(
        {
            "ticker": "RELIANCE",
            "name": "Reliance",
            "shares": 2,
            "quarters": {"2026-Q1": 2},
            "dividends": [{"date": "2026-03-01", "amount": 5.0}],
            "reconcile": {"2026": {"actual": 10.0, "updatedAt": "2026-03-02T00:00:00+00:00"}},
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    csv_text = agent.export_csv(2026)
    assert "RELIANCE" in csv_text
    assert "10.0" in csv_text


def test_tax_playbook_fy(db):
    db.save_holding(
        {
            "ticker": "HDFCBANK",
            "name": "HDFC Bank",
            "shares": 1,
            "quarters": {"2025-Q2": 1, "2025-Q3": 1},
            "dividends": [
                {"date": "2025-06-01", "amount": 10.0},
                {"date": "2025-09-01", "amount": 10.0},
            ],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    pack = agent.tax_playbook("fy:2025")
    assert pack["totalIncome"] == 20.0
    assert pack["copyText"]


def test_dividend_refresh_diff_detects_new_and_change():
    old = [{"date": "2025-01-15", "amount": 5.0}]
    new = [
        {"date": "2025-01-15", "amount": 6.0},
        {"date": "2026-02-01", "amount": 4.0},
    ]
    changes = agent.dividend_refresh_diff(old, new)
    types = {c["type"] for c in changes}
    assert "dps-change" in types
    assert "new-payout" in types


def test_save_holding_records_dividend_alerts_and_coach_task(db):
    db.save_holding(
        {
            "ticker": "INFY",
            "name": "Infosys",
            "shares": 10,
            "quarters": {"2026-Q1": 10},
            "dividends": [{"date": "2026-01-15", "amount": 12.0}],
            "status": "ok",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )
    db.save_holding(
        {
            "ticker": "INFY",
            "name": "Infosys",
            "shares": 10,
            "quarters": {"2026-Q1": 10},
            "dividends": [
                {"date": "2026-01-15", "amount": 12.0},
                {"date": "2026-06-01", "amount": 14.0},
            ],
            "status": "ok",
            "updatedAt": "2026-06-02T00:00:00+00:00",
        }
    )
    holding = db.load_holdings()[0]
    assert any(a.get("type") == "new-payout" for a in holding.get("dividendAlerts") or [])
    tasks = coach.build("2026")["tasks"]
    assert any(t.get("type") == "new-payout" and t.get("ticker") == "INFY" for t in tasks)
