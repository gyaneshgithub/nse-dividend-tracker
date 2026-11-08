"""Tests for the read-only portfolio coach."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import coach


def test_build_empty_portfolio(db):
    data = coach.build("2026")

    assert data["year"] == 2026
    assert data["yearLabel"] == "2026"
    assert data["briefing"]["headline"] == "No holdings yet"
    assert data["tasks"] == []
    assert data["events"] == []
    assert data["counts"] == {"total": 0, "p0": 0, "p1": 0, "p2": 0}
    assert data["ruleset"]["id"] == "v1"
    assert data["disclaimer"]
    assert data["generatedAt"]


def test_missing_quantity_task(db):
    db.save_holding(
        {
            "ticker": "TCS",
            "name": "TCS",
            "shares": 5,
            "quarters": {"2026-Q1": 5},
            "dividends": [
                {"date": "2026-02-01", "amount": 2.0},
                {"date": "2026-05-01", "amount": 2.5},
            ],
            "price": 100,
            "status": "ok",
            "lastFetched": datetime.now(timezone.utc).isoformat(),
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )

    data = coach.build("2026")
    missing = [task for task in data["tasks"] if task["type"] == "missing-quantity"]
    assert len(missing) == 1
    assert missing[0]["priority"] == "p0"
    assert missing[0]["quarter"] == "2026-Q2"
    assert missing[0]["action"] == "focus-quarter"


def test_fetch_failed_task(db):
    db.save_holding(
        {
            "ticker": "INFY",
            "name": "Infosys",
            "shares": 10,
            "quarters": {"2026-Q1": 10},
            "dividends": [{"date": "2026-02-01", "amount": 2.0}],
            "price": 100,
            "status": "error",
            "lastError": "upstream blocked",
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )

    data = coach.build("2026")
    refresh = [task for task in data["tasks"] if task["type"] == "fetch-failed"]
    assert len(refresh) == 1
    assert refresh[0]["ticker"] == "INFY"
    assert refresh[0]["action"] == "refresh"


def test_stale_fetch_task(db):
    stale = datetime.now(timezone.utc) - timedelta(days=10)
    db.save_holding(
        {
            "ticker": "ITC",
            "name": "ITC",
            "shares": 1,
            "quarters": {"2026-Q1": 1},
            "dividends": [{"date": "2026-02-01", "amount": 2.0}],
            "price": 100,
            "status": "ok",
            "lastFetched": stale.isoformat(),
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )

    data = coach.build("2026")
    tasks = [task for task in data["tasks"] if task["type"] == "stale-fetch"]
    assert len(tasks) == 1
    assert tasks[0]["ticker"] == "ITC"


def test_coach_endpoint_is_read_only(client, db):
    holding = {
        "ticker": "INFY",
        "name": "Infosys",
        "shares": 10,
        "quarters": {"2026-Q1": 10},
        "dividends": [{"date": "2026-02-01", "amount": 2.0}],
        "price": 100,
        "status": "ok",
        "lastFetched": datetime.now(timezone.utc).isoformat(),
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    client.put("/api/portfolio/INFY", json=holding)
    before = db.storage_info()

    response = client.get("/api/coach?year=2026")
    payload = response.get_json()

    assert response.status_code == 200
    assert set(payload) >= {
        "briefing",
        "tasks",
        "events",
        "counts",
        "disclaimer",
        "year",
        "ruleset",
    }
    assert payload["briefing"]["headline"]

    after = db.storage_info()
    assert after["revision"] == before["revision"]
