"""Tests for upcoming bonus/split notifications. No network access."""

from __future__ import annotations

from datetime import date, timedelta

import corporate


def _row(symbol, subject, ex_date):
    return {
        "symbol": symbol,
        "comp": symbol + " Limited",
        "subject": subject,
        "exDate": ex_date.strftime("%d-%b-%Y"),
    }


def test_classify_keeps_only_bonus_and_split():
    assert corporate.classify("Bonus 1:1") == "bonus"
    assert corporate.classify("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/-") == "split"
    assert corporate.classify("Stock Split - From Rs 10 To Rs 1") == "split"
    assert corporate.classify("Interim Dividend - Rs 5 Per Share") is None
    assert corporate.classify("Buy Back") is None
    assert corporate.classify("Rights 5:9") is None
    # Preference-share bonus does not change the ordinary share count.
    assert corporate.classify("Scheme Of Arrangement - Bonus Ncrps 46:1") is None
    assert corporate.classify("") is None


def test_describe_extracts_ratio_and_face_value():
    assert corporate.describe("Bonus 1:1", "bonus") == "Bonus 1:1"
    assert (
        corporate.describe("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/-", "split")
        == "Face value ₹10 → ₹2"
    )


def test_parse_actions_filters_window_and_dedupes():
    today = date.today()
    rows = [
        _row("BONUSCO", "Bonus 2:1", today + timedelta(days=5)),
        _row("SPLITCO", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/-", today + timedelta(days=2)),
        _row("DIVCO", "Interim Dividend - Rs 3 Per Share", today + timedelta(days=1)),
        _row("PASTCO", "Bonus 1:1", today - timedelta(days=1)),
        _row("FARCO", "Bonus 1:1", today + timedelta(days=400)),
        _row("BONUSCO", "Bonus 2:1", today + timedelta(days=5)),
    ]
    actions = corporate.parse_actions(rows, today, days=60)
    assert [a["symbol"] for a in actions] == ["SPLITCO", "BONUSCO"]
    assert actions[0]["type"] == "split"
    assert actions[0]["daysAway"] == 2
    assert actions[1]["detail"] == "Bonus 2:1"
    assert len({a["id"] for a in actions}) == 2


def test_parse_actions_tolerates_bad_rows():
    today = date.today()
    rows = [
        None,
        {"symbol": "X", "subject": "Bonus 1:1", "exDate": "not-a-date"},
        {"subject": "Bonus 1:1", "exDate": (today + timedelta(days=2)).strftime("%d-%b-%Y")},
        _row("OKCO", "Bonus 1:1", today + timedelta(days=3)),
    ]
    actions = corporate.parse_actions(rows, today, days=60)
    assert [a["symbol"] for a in actions] == ["OKCO"]


def test_upcoming_flags_holdings_and_sorts_them_first(monkeypatch):
    today = date.today()
    rows = [
        _row("OTHER", "Bonus 1:1", today + timedelta(days=3)),
        _row("HELD", "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 1/-", today + timedelta(days=9)),
    ]
    monkeypatch.setattr(corporate, "_fetch_rows", lambda start, end: rows)
    corporate.reset_cache()

    payload = corporate.upcoming([{"ticker": "HELD"}], days=60)
    assert payload["ok"] is True
    assert payload["heldCount"] == 1
    assert [a["symbol"] for a in payload["actions"]] == ["HELD", "OTHER"]
    assert payload["actions"][0]["held"] is True
    assert payload["actions"][1]["held"] is False
    assert payload["source"] == "nseindia.com corporate actions"


def test_upcoming_degrades_when_the_feed_fails(monkeypatch):
    def boom(start, end):
        raise OSError("network down")

    monkeypatch.setattr(corporate, "_fetch_rows", boom)
    corporate.reset_cache()

    payload = corporate.upcoming([{"ticker": "HELD"}], days=30)
    assert payload["ok"] is False
    assert payload["actions"] == []
    assert payload["heldCount"] == 0
    assert "network down" in payload["error"]


def test_corporate_actions_route(client, monkeypatch):
    payload = {
        "ok": True,
        "source": "nseindia.com corporate actions",
        "asOf": date.today().isoformat(),
        "days": 60,
        "actions": [{"id": "ca:X:2026-11-27:split", "symbol": "X", "held": True, "type": "split"}],
        "heldCount": 1,
        "error": None,
    }
    seen = {}

    def fake_upcoming(holdings, days=60):
        seen["days"] = days
        seen["tickers"] = [h["ticker"] for h in holdings]
        return payload

    monkeypatch.setattr(corporate, "upcoming", fake_upcoming)
    response = client.get("/api/corporate-actions?days=45")
    assert response.status_code == 200
    assert response.get_json() == payload
    assert seen == {"days": 45, "tickers": []}
