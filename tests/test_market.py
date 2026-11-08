"""Tests for the NSE dividend parser and cache."""

from __future__ import annotations

import pytest

import market

HTML = """
<html>
<head><title>Reliance Industries (NSE:RELIANCE) Dividend History</title></head>
<body>
<script>
quote:{c:-2.5,s:1,p:1164.9,dp:0.2},
annual:"6.000 INR",
yield:"1.23%",
history:[
  {dt:"2026-06-05",amt:"6.000 INR",type:"Cash"},
  {dt:"2025-06-05",amt:"5.500 INR",type:"Cash"},
  {dt:"2024-06-05",amt:"0.000 INR",type:"Cash"}
],chartData:[]
</script>
</body>
</html>
"""


@pytest.fixture(autouse=True)
def clear_cache():
    market._cache.clear()
    yield
    market._cache.clear()


def test_parse_dividends_extracts_company_and_history():
    payload = market.parse_dividends("RELIANCE", HTML)
    assert payload["symbol"] == "RELIANCE"
    assert payload["name"] == "Reliance Industries"
    assert payload["price"] == 1164.9
    assert payload["annualDividend"] == 6.0
    assert payload["yieldPct"] == 1.23
    assert payload["currency"] == "INR"
    assert payload["dividends"] == [
        {"date": "2025-06-05", "amount": 5.5},
        {"date": "2026-06-05", "amount": 6.0},
    ]


def test_parse_dividends_without_history_raises_lookup_error():
    with pytest.raises(LookupError):
        market.parse_dividends("NODIV", "<html><title>No Dividend (NSE:NODIV)</title></html>")


def test_get_dividends_caches_and_respects_ttl(monkeypatch):
    calls = []

    def fake_fetch(symbol, page="dividend"):
        calls.append(symbol)
        return HTML

    monkeypatch.setattr(market, "_fetch_html", fake_fetch)
    monkeypatch.setenv("DIVIDEND_CACHE_TTL", "3600")

    first = market.get_dividends("reliance")
    second = market.get_dividends("RELIANCE")
    assert first == second
    assert calls == ["RELIANCE"]

    monkeypatch.setenv("DIVIDEND_CACHE_TTL", "0")
    market.get_dividends("RELIANCE")
    assert calls == ["RELIANCE", "RELIANCE"]


def test_get_dividends_rejects_invalid_symbol():
    with pytest.raises(ValueError):
        market.get_dividends("not a ticker")


def test_get_dividends_uses_overview_when_dividend_page_missing(monkeypatch):
    overview = """
    <html><head><title>Delhivery (NSE:DELHIVERY) Stock Price</title></head>
    <body><script>dividend:"n/a",dividendTable:null,"p":428.5,quote:{p:428.5}</script></body></html>
    """

    def fake_fetch(symbol, page="dividend"):
        if page == "dividend":
            import urllib.error

            raise urllib.error.HTTPError("url", 404, "Not Found", None, None)
        return overview

    monkeypatch.setattr(market, "_fetch_html", fake_fetch)
    payload = market.get_dividends("DELHIVERY")
    assert payload["symbol"] == "DELHIVERY"
    assert payload["name"] == "Delhivery"
    assert payload["price"] == 428.5
    assert payload["dividends"] == []


def test_get_dividends_tries_dot_slug_when_hyphen_returns_404(monkeypatch):
    calls = []

    def fake_fetch(symbol, page="dividend"):
        calls.append((symbol, page))
        if symbol == "NAM-INDIA":
            import urllib.error

            raise urllib.error.HTTPError("url", 404, "Not Found", None, None)
        if symbol == "NAM.INDIA":
            return HTML.replace("RELIANCE", "NAM-INDIA").replace("Reliance Industries", "Nippon AMC")
        raise AssertionError(symbol)

    monkeypatch.setattr(market, "_fetch_html", fake_fetch)
    payload = market.get_dividends("NAM-INDIA")
    assert payload["symbol"] == "NAM-INDIA"
    assert calls == [("NAM-INDIA", "dividend"), ("NAM.INDIA", "dividend")]
