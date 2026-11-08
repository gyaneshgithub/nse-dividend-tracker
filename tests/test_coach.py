"""Tests for the read-only portfolio coach."""

from __future__ import annotations

import coach


def test_build_empty_portfolio(db):
    data = coach.build("2026")

    assert data["year"] == 2026
    assert data["yearLabel"] == "2026"
    assert data["briefing"]["headline"] == "No holdings yet"
    assert data["tasks"] == []
    assert data["events"] == []
    assert data["counts"] == {"total": 0, "p0": 0, "p1": 0, "p2": 0}
    assert data["ruleset"]["id"] == "stub"
    assert data["disclaimer"]
    assert data["generatedAt"]


def test_build_with_holdings(db):
    db.save_holding(
        {
            "ticker": "TCS",
            "name": "TCS",
            "shares": 5,
            "quarters": {"2026-Q1": 5},
            "dividends": [{"date": "2026-02-01", "amount": 2.0}],
            "price": 100,
            "updatedAt": "2026-01-01T00:00:00+00:00",
        }
    )

    data = coach.build("all")

    assert data["year"] == "all"
    assert data["briefing"]["headline"] == "Briefing ready"
    assert "1 holding" in data["briefing"]["summary"]
    assert data["tasks"] == []
    assert data["events"] == []


def test_coach_endpoint_is_read_only(client, db):
    holding = {
        "ticker": "INFY",
        "name": "Infosys",
        "shares": 10,
        "quarters": {"2026-Q1": 10},
        "dividends": [{"date": "2026-02-01", "amount": 2.0}],
        "price": 100,
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
