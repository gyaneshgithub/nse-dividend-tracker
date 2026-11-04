"""Tests for the deterministic portfolio insight engine."""

from __future__ import annotations

import json

import pytest


def holding(ticker="INFY", **overrides):
    payload = {
        "ticker": ticker,
        "name": f"{ticker} Ltd",
        "shares": 10,
        "quarters": {"2026-Q2": 10},
        "dividends": [
            {"date": f"{year}-05-01", "amount": amount}
            for year, amount in ((2022, 2.0), (2023, 2.2), (2024, 2.4), (2025, 2.6), (2026, 2.8))
        ],
        "price": 100,
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def series(*amounts, start=2022):
    return [
        {"date": f"{start + offset}-05-01", "amount": amount}
        for offset, amount in enumerate(amounts)
    ]


def test_stability_is_high_for_flat_dividends():
    import insight

    assert insight._stability([(2022, 2.0), (2023, 2.0), (2024, 2.0)]) == 100.0
    assert insight._stability([(2026, 2.0)]) is None


def test_cuts_only_flag_year_on_year_drops_over_fifteen_percent():
    import insight

    flat = insight._cuts([(2024, 2.0), (2025, 1.9), (2026, 2.0)])
    assert flat == []

    cut = insight._cuts([(2024, 2.0), (2025, 1.0)])
    assert cut == [{"year": 2025, "from": 2.0, "to": 1.0, "dropPct": -50.0}]

    # A gap in history is not a year-on-year comparison.
    assert insight._cuts([(2022, 2.0), (2025, 0.5)]) == []


def test_cuts_are_limited_to_the_last_five_years():
    import insight

    data = insight._cuts([(2018, 4.0), (2019, 1.0), (2020, 1.0), (2021, 1.0), (2022, 2.0), (2023, 0.5)])

    assert [cut["year"] for cut in data] == [2023]


def test_streak_counts_consecutive_paid_years():
    import insight

    assert insight._streak([(2022, 1.0), (2023, 1.0), (2024, 1.0)]) == 3
    assert insight._streak([(2022, 1.0), (2024, 1.0)]) == 1
    assert insight._streak([]) == 0


def test_empty_portfolio_returns_an_empty_state(db):
    import insight

    data = insight.build("2026")

    assert data["scores"]["overall"] is None
    assert data["scores"]["band"] == "unknown"
    assert data["narrative"]["headline"] == "No holdings yet"
    assert data["charts"]["incomeByYear"] == []
    assert data["charts"]["concentration"] == []
    assert data["stats"]["holdings"] == 0
    json.dumps(data)  # must be JSON-serialisable for the API


def test_scores_shape_and_ranges(db):
    import insight

    db.save_holding(holding("INFY"))
    db.save_holding(holding("TCS", quarters={"2026-Q2": 5}, dividends=series(4, 4, 4, 3, 3)))

    data = insight.build("2026")
    scores = data["scores"]

    assert 0 <= scores["overall"] <= 100
    assert 0 <= scores["confidence"] <= 1
    assert [component["key"] for component in scores["components"]] == [
        "income",
        "growth",
        "diversification",
    ]
    for component in scores["components"]:
        assert 0 <= component["score"] <= 100
        assert component["weight"] > 0
    assert data["year"] == 2026
    assert data["currency"] == "INR"
    assert 0 <= data["stats"]["incomeSources"] <= data["stats"]["holdings"]


def test_cut_is_reported_and_penalised(db):
    import insight

    db.save_holding(holding("CUTCO", dividends=series(4.0, 4.0, 4.0, 2.0, 2.0)))

    data = insight.build("2026")

    assert data["stats"]["cuts"][0]["ticker"] == "CUTCO"
    assert data["stats"]["cuts"][0]["year"] == 2025
    assert any("dividend cut" in risk for risk in data["narrative"]["risks"])
    row = data["charts"]["holdings"][0]
    assert row["cuts"][0]["dropPct"] == -50.0
    assert row["stability"] < 100


def test_concentration_is_measured_from_income_share(db):
    import insight

    db.save_holding(holding("BIG", quarters={"2026-Q2": 90}))
    db.save_holding(holding("SMALL", quarters={"2026-Q2": 10}))

    data = insight.build("2026")
    stats = data["stats"]

    assert stats["top1Pct"] == 90.0
    assert stats["top3Pct"] == 100.0
    assert stats["hhi"] > 0.8
    assert "leans heavily" in data["narrative"]["headline"]


def test_more_balanced_portfolio_outscores_a_concentrated_one(db):
    import insight

    for index in range(8):
        db.save_holding(holding(f"STOCK{index}", quarters={"2026-Q2": 10}))

    balanced = insight.build("2026")

    for index in range(8):
        db.delete_holding(f"STOCK{index}")
    db.save_holding(holding("ONLY", quarters={"2026-Q2": 10}))

    concentrated = insight.build("2026")

    assert balanced["stats"]["hhi"] < concentrated["stats"]["hhi"]
    assert balanced["scores"]["components"][2]["score"] > concentrated["scores"]["components"][2]["score"]


def test_diversification_is_capped_by_breadth(db):
    import insight

    # Two equal holdings are evenly split but not diversified.
    db.save_holding(holding("AAA", quarters={"2026-Q2": 10}))
    db.save_holding(holding("BBB", quarters={"2026-Q2": 10}))

    data = insight.build("2026")
    diversification = data["scores"]["components"][2]["score"]

    assert diversification <= 25.1


def test_projection_only_exists_for_a_specific_year(db):
    import insight

    db.save_holding(holding("INFY"))

    specific = insight.build("2026")
    all_years = insight.build("all")

    assert specific["stats"]["projectedNextYearIncome"] is not None
    assert specific["stats"]["projectedIncreasePct"] is not None
    assert all_years["stats"]["projectedNextYearIncome"] is None
    assert all_years["stats"]["projectedIncreasePct"] is None


def test_insight_does_not_write_to_storage(db):
    import insight

    db.save_holding(holding("INFY"))
    before = db.storage_info()

    insight.build("2026")
    insight.build("all")

    after = db.storage_info()
    assert after["revision"] == before["revision"]
    assert after["holdings"] == before["holdings"]
    assert after["payouts"] == before["payouts"]


def test_period_without_quantities_is_explained(db):
    import insight

    db.save_holding(holding("INFY", quarters={}))

    data = insight.build("2026")

    assert data["stats"]["totalIncome"] == 0
    assert data["stats"]["payouts"] > 0
    assert data["narrative"]["headline"] == "No income recorded for this period"
    assert "no quantities" in data["narrative"]["summary"].lower()
    # Component values may exist from history, but an empty period is not
    # given an overall score.
    assert data["scores"]["overall"] is None
    assert data["scores"]["band"] == "unknown"


def test_period_without_payouts_is_explained(db):
    import insight

    db.save_holding(holding("OLDCO", quarters={"2026-Q2": 10}, dividends=series(2.0, 2.0, 2.0)))
    data = insight.build("2026")

    assert data["stats"]["payouts"] == 0
    assert "no dividend payouts" in data["narrative"]["summary"].lower()
    assert data["scores"]["overall"] is None


def test_scores_only_use_data_up_to_the_selected_year(db):
    import insight

    db.save_holding(
        holding(
            "CUTCO",
            quarters={"2026-Q2": 10},
            dividends=series(4.0, 4.0, 4.0, 4.0, 2.0),
        )
    )

    past = insight.build("2025")
    current = insight.build("2026")

    assert past["stats"]["cuts"] == []
    assert current["stats"]["cuts"][0]["year"] == 2026
    # The 2025 view must not see the 2026 dividend level.
    assert past["charts"]["holdings"][0]["dps"][-1]["amount"] == 4.0
    assert current["charts"]["holdings"][0]["dps"][-1]["amount"] == 2.0


def test_deferred_components_are_declared_not_scored(db):
    import insight

    db.save_holding(holding("INFY"))
    data = insight.build("2026")

    keys = {component["key"] for component in data["scores"]["components"]}
    deferred = {component["key"] for component in data["scores"]["deferred"]}

    assert "technicals" in deferred
    assert "youth" in deferred
    assert keys.isdisjoint(deferred)


@pytest.mark.parametrize("year", ["2026", "all", ""])
def test_build_accepts_analysis_year_arguments(db, year):
    import insight

    db.save_holding(holding("INFY"))
    assert insight.build(year)["year"] in (2026, "all")
