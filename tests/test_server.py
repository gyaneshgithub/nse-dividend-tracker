"""Flask API tests using a temporary database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def holding(ticker="INFY", **overrides):
    payload = {
        "ticker": ticker,
        "name": f"{ticker} Ltd",
        "shares": 10,
        "quarters": {"2026-Q1": 10, "2026-Q2": 4},
        "dividends": [
            {"date": "2026-02-01", "amount": 2.0},
            {"date": "2026-05-01", "amount": 2.5},
        ],
        "price": 100,
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_health_and_empty_portfolio(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    response = client.get("/api/portfolio")
    assert response.status_code == 200
    assert response.get_json()["holdings"] == []


def test_index_page_renders(client):
    response = client.get("/tracker")
    assert response.status_code == 200
    assert b"Dividend" in response.data


def test_save_load_delete_flow(client):
    response = client.put("/api/portfolio/INFY", json=holding())
    assert response.status_code == 200
    assert response.get_json()["holding"]["ticker"] == "INFY"

    response = client.get("/api/portfolio")
    assert [h["ticker"] for h in response.get_json()["holdings"]] == ["INFY"]

    response = client.get("/api/analysis?year=2026")
    payload = response.get_json()
    assert payload["totalIncome"] == 30.0

    response = client.delete("/api/portfolio/INFY")
    assert response.status_code == 200
    assert client.get("/api/portfolio").get_json()["holdings"] == []


def test_stale_write_cannot_resurrect_deleted_holding(client):
    client.put("/api/portfolio/INFY", json=holding())
    client.delete("/api/portfolio/INFY")

    stale = holding(updatedAt="2025-01-01T00:00:00+00:00")
    response = client.put("/api/portfolio/INFY", json=stale)
    assert response.status_code == 200
    assert response.get_json()["ignored"] is True
    assert client.get("/api/portfolio").get_json()["holdings"] == []

    revived = holding(
        updatedAt=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    )
    response = client.put("/api/portfolio/INFY", json=revived)
    assert response.status_code == 200
    assert response.get_json()["holding"]["ticker"] == "INFY"


def test_import_endpoint(client):
    response = client.post(
        "/api/portfolio/import",
        json={"holdings": [holding("INFY"), holding("TCS")]},
    )
    assert response.status_code == 200
    assert response.get_json()["saved"] == 2
    assert {h["ticker"] for h in client.get("/api/portfolio").get_json()["holdings"]} == {
        "INFY",
        "TCS",
    }


def test_invalid_ticker_and_payload(client):
    assert client.put("/api/portfolio/bad ticker", json={}).status_code == 400
    assert client.put("/api/portfolio/INFY", json=[]).status_code == 400
    assert client.get("/api/portfolio/BAD%20TICKER").status_code == 404


def test_dividends_endpoint_uses_upstream(client, monkeypatch):
    import server

    monkeypatch.setattr(
        server,
        "get_dividends",
        lambda symbol: {
            "symbol": symbol,
            "name": "Test Ltd",
            "price": 10,
            "currency": "INR",
            "annualDividend": 1,
            "yieldPct": 10,
            "dividends": [{"date": "2026-01-01", "amount": 1}],
            "source": "test",
        },
    )
    response = client.get("/api/dividends?symbol=TEST")
    assert response.status_code == 200
    assert response.get_json()["symbol"] == "TEST"

    assert client.get("/api/dividends?symbol=bad symbol").status_code == 400


def test_search_and_storage_endpoints(client):
    client.put("/api/portfolio/INFY", json=holding())
    assert client.get("/api/search?q=INFY").status_code == 200
    storage = client.get("/api/storage").get_json()
    assert storage["backend"] == "chromadb"
    assert storage["holdings"] == 1


def test_unknown_api_route_returns_json_404(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Not found"


def test_no_wildcard_cors_header(client):
    response = client.get("/api/health")
    assert "Access-Control-Allow-Origin" not in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"


def test_cross_origin_write_is_blocked(client):
    response = client.put(
        "/api/portfolio/INFY",
        json=holding(),
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert client.get("/api/portfolio").get_json()["holdings"] == []


def test_same_origin_write_is_allowed(client):
    response = client.put(
        "/api/portfolio/INFY",
        json=holding(),
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 200
