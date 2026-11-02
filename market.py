#!/usr/bin/env python3
"""Live NSE dividend data for the Dividend Tracker.

Data source: stockanalysis.com (NSE listed companies), parsed from the
dividend page's embedded JSON. Responses are cached in memory for 6 hours.
"""

from html import unescape as html_unescape
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

UPSTREAM = "https://stockanalysis.com/quote/nse/{symbol}/dividend/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_CACHE_TTL = 6 * 3600


def _cache_ttl():
    """Read DIVIDEND_CACHE_TTL (seconds) on each call; tests can override it."""
    try:
        return max(0, int(os.environ.get("DIVIDEND_CACHE_TTL", DEFAULT_CACHE_TTL)))
    except ValueError:
        return DEFAULT_CACHE_TTL


CACHE_TTL = DEFAULT_CACHE_TTL
SYMBOL_RE = re.compile(r"^(?=.{2,15}$)[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_cache = {}


def _fetch_html(symbol):
    url = UPSTREAM.format(symbol=urllib.parse.quote(symbol))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=25, context=context) as response:
        return response.read().decode("utf-8", "replace")


def parse_dividends(symbol, html):
    # Company name from <title>: "Reliance Industries (NSE:RELIANCE) Dividend History..."
    name = symbol
    title = re.search(r"<title>([^<]+)</title>", html)
    if title:
        match = re.match(r"(.+?)\s+\(NSE:", title.group(1))
        if match:
            name = html_unescape(match.group(1).strip())

    # Current price from embedded quote object: quote:{c:-2.5,...,p:1164.9,...}
    price = None
    quote = re.search(r"quote:\{([^}]*)\}", html)
    if quote:
        price_match = re.search(r"(?:^|,)\s*p:([0-9.]+)", quote.group(1))
        if price_match:
            price = float(price_match.group(1))

    annual = None
    annual_match = re.search(r'annual:"([0-9.,]+)\s*INR"', html)
    if annual_match:
        annual = float(annual_match.group(1).replace(",", ""))

    yield_pct = None
    yield_match = re.search(r'yield:"([0-9.]+)%"', html)
    if yield_match:
        yield_pct = float(yield_match.group(1))

    # Dividend history: history:[{dt:"2026-06-05",amt:"6.000 INR",...},...],chartData
    dividends = []
    history_match = re.search(r"history:\[(.*?)\]\s*,\s*chartData", html, re.S)
    if not history_match:
        history_match = re.search(r"history:\[(.*?)\]", html, re.S)
    if history_match:
        for entry in re.finditer(
            r'dt:"([0-9]{4}-[0-9]{2}-[0-9]{2})"\s*,\s*amt:"([0-9.]+)\s*INR"',
            history_match.group(1),
        ):
            date, amount = entry.group(1), float(entry.group(2))
            if amount > 0:
                dividends.append({"date": date, "amount": amount})
    dividends.sort(key=lambda item: item["date"])

    if not dividends:
        raise LookupError("No dividend data found for this ticker")

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "currency": "INR",
        "annualDividend": annual,
        "yieldPct": yield_pct,
        "dividends": dividends,
        "source": "stockanalysis.com (NSE)",
    }


def get_dividends(symbol):
    symbol = str(symbol or "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Invalid NSE symbol")
    now = time.time()
    cached = _cache.get(symbol)
    if cached and now - cached[0] < _cache_ttl():
        return cached[1]
    html = _fetch_html(symbol)
    payload = parse_dividends(symbol, html)
    _cache[symbol] = (now, payload)
    return payload
