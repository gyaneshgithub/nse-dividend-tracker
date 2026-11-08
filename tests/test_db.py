"""Tests for storage, merging, tombstones and analysis."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def holding(ticker="INFY", **overrides):
    payload = {
        "ticker": ticker,
        "name": f"{ticker} Ltd",
        "shares": 10,
        "quarters": {"2026-Q1": 10},
        "dividends": [
            {"date": "2026-05-01", "amount": 2.5},
            {"date": "2025-05-01", "amount": 1.5},
        ],
        "price": 100,
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def future(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_normalize_holding_builds_year_totals(db):
    normalized = db.normalize_holding(holding())
    assert normalized["ticker"] == "INFY"
    assert normalized["quarters"] == {"2026-Q1": 10}
    assert normalized["yearTotals"] == {"2025": 1.5, "2026": 2.5}
    assert normalized["status"] == "ok"


def test_save_load_delete_and_tombstone(db):
    db.save_holding(holding())
    assert [h["ticker"] for h in db.load_holdings()] == ["INFY"]

    db.delete_holding("INFY")
    assert db.load_holdings() == []

    stale = holding(updatedAt="2025-01-01T00:00:00+00:00")
    assert db.save_holding(stale) is None
    assert db.load_holdings() == []

    revived = holding(updatedAt=future())
    saved = db.save_holding(revived)
    assert saved is not None
    assert [h["ticker"] for h in db.load_holdings()] == ["INFY"]


def test_merge_keeps_manually_edited_quarters(db):
    db.save_holding(
        holding(
            quarters={"2026-Q1": 10},
            quarterUpdatedAt={"2026-Q1": "2026-01-02T00:00:00+00:00"},
        )
    )
    db.save_holding(
        holding(
            quarters={"2026-Q1": 99, "2026-Q2": 5},
            updatedAt="2026-01-03T00:00:00+00:00",
        )
    )
    saved = db.load_holdings()[0]
    assert saved["quarters"]["2026-Q1"] == 10
    assert saved["quarters"]["2026-Q2"] == 5


def test_import_holdings_reports_saved_count(db):
    saved = db.import_holdings([holding("INFY"), holding("TCS"), {"ticker": "bad ticker"}])
    assert len(saved) == 2
    assert {h["ticker"] for h in db.load_holdings()} == {"INFY", "TCS"}


def test_analysis_uses_quarter_quantities(db):
    db.save_holding(
        holding(
            quarters={"2026-Q1": 10, "2026-Q2": 4},
            dividends=[
                {"date": "2026-02-01", "amount": 2.0},
                {"date": "2026-05-01", "amount": 2.5},
                {"date": "2025-05-01", "amount": 1.5},
            ],
        )
    )
    result = db.analysis("2026")
    assert result["totalIncome"] == 30.0
    assert result["totalPayouts"] == 2
    assert result["byQuarter"] == [
        {"quarter": "Q1", "income": 20.0},
        {"quarter": "Q2", "income": 10.0},
        {"quarter": "Q3", "income": 0.0},
        {"quarter": "Q4", "income": 0.0},
    ]


def test_analysis_projects_next_year_from_peak_quarters(db):
    db.save_holding(
        holding(
            quarters={"2026-Q1": 10, "2026-Q2": 4, "2026-Q3": 12},
            dividends=[
                {"date": "2026-02-01", "amount": 2.0},
                {"date": "2026-05-01", "amount": 2.5},
            ],
        )
    )
    db.save_holding(
        holding(
            "TCS",
            quarters={"2026-Q4": 5},
            dividends=[{"date": "2026-11-01", "amount": 1.0}],
        )
    )

    result = db.analysis("2026")

    # INFY: max(10, 4, 12) × (2.0 + 2.5) = 54; TCS: 5 × 1.0 = 5.
    assert result["totalIncome"] == 35.0
    assert result["projectedNextYearIncome"] == 59.0
    assert result["projectedIncreasePct"] == 68.57
    projections = result["projections"]
    assert projections["base"]["income"] == 59.0
    assert projections["conservative"]["income"] == 30.5
    assert projections["growth"]["income"] == 59.0
    assert len(projections["notes"]) == 3
    assert db.analysis("all")["projectedNextYearIncome"] is None
    assert db.analysis("all")["projections"] is None


def test_analysis_growth_scenario_uses_capped_cagr(db):
    db.save_holding(
        holding(
            quarters={"2026-Q1": 10},
            dividends=[
                {"date": "2024-05-01", "amount": 2.0},
                {"date": "2025-05-01", "amount": 2.2},
                {"date": "2026-05-01", "amount": 2.5},
            ],
        )
    )
    result = db.analysis("2026")
    base = result["projections"]["base"]["income"]
    growth = result["projections"]["growth"]["income"]
    assert base == 25.0
    assert growth > base


def test_income_bridge_decomposes_yoy_change(db):
    db.save_holding(
        holding(
            quarters={"2025-Q2": 10, "2026-Q2": 20},
            dividends=[
                {"date": "2025-05-01", "amount": 2.0},
                {"date": "2026-05-01", "amount": 2.0},
            ],
        )
    )

    bridge = db.analysis("2026")["incomeBridge"]
    assert bridge["previousIncome"] == 20.0
    assert bridge["currentIncome"] == 40.0
    assert bridge["delta"] == 20.0
    by_key = {row["key"]: row["amount"] for row in bridge["components"]}
    assert by_key["quantity"] == 20.0
    assert by_key["dps"] == 0.0


def test_growth_by_ticker(db):
    db.save_holding(
        holding(
            quarters={"2026-Q1": 1},
            dividends=[
                {"date": "2025-05-01", "amount": 2.0},
                {"date": "2026-05-01", "amount": 2.5},
            ],
        )
    )
    growth = db.analysis("2026")["growthByTicker"]["INFY"]
    assert growth["dpsYoyPct"] == 25.0
    assert len(growth["dpsTrend"]) == 2


def test_backup_mirror_restores_empty_database(db, tmp_path, monkeypatch):
    db.save_holding(holding())
    assert db.BACKUP_FILE.endswith("portfolio.json")

    monkeypatch.setattr(db, "DB_DIR", str(tmp_path / "fresh-chroma"))
    db.reset_connection()
    db.init_storage()
    restored = db.load_holdings()
    assert len(restored) == 1
    assert restored[0]["ticker"] == "INFY"


def test_storage_info_reports_counts(db):
    db.save_holding(holding())
    info = db.storage_info()
    assert info["backend"] == "chromadb"
    assert info["holdings"] == 1
    assert info["payouts"] == 2
    assert info["revision"] >= 1


def test_search_returns_matching_payouts(db):
    db.save_holding(holding())
    results = db.search_payouts("INFY dividend", limit=5)
    assert results
    assert results[0]["ticker"] == "INFY"


def test_tombstone_read_failure_fails_closed(db, monkeypatch):
    def broken_meta():
        raise RuntimeError("meta unavailable")

    monkeypatch.setattr(db, "_get_meta_col", broken_meta)
    with pytest.raises(db.StorageUnavailable, match="tombstones"):
        db.save_holding(holding())


def test_holding_read_failure_is_not_treated_as_missing(db, monkeypatch):
    class BrokenHoldings:
        def get(self, **_kwargs):
            raise RuntimeError("holdings unavailable")

    monkeypatch.setattr(db, "_get_collections", lambda: (BrokenHoldings(), None))
    with pytest.raises(RuntimeError, match="holdings unavailable"):
        db._load_holding_locked("INFY")


def test_payout_delete_failure_aborts_save(db, monkeypatch):
    class FakeHoldings:
        def upsert(self, **_kwargs):
            pass

    class BrokenPayouts:
        def delete(self, **_kwargs):
            raise RuntimeError("payout index unavailable")

    monkeypatch.setattr(db, "_get_collections", lambda: (FakeHoldings(), BrokenPayouts()))
    with pytest.raises(RuntimeError, match="payout index unavailable"):
        db._save_holding_locked(holding())


def test_payout_delete_failure_aborts_holding_delete(db, monkeypatch):
    class FakeHoldings:
        def delete(self, **_kwargs):
            pass

    class BrokenPayouts:
        def delete(self, **_kwargs):
            raise RuntimeError("payout index unavailable")

    monkeypatch.setattr(db, "_get_collections", lambda: (FakeHoldings(), BrokenPayouts()))
    with pytest.raises(RuntimeError, match="payout index unavailable"):
        db.delete_holding("INFY")
