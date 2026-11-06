"""Dividend data fetched from the upstream source."""

from __future__ import annotations

import urllib.error

from flask import Blueprint, jsonify, request

from market import SYMBOL_RE

bp = Blueprint("dividends", __name__)


def _error(code, message, hint, status, symbol):
    """Structured fetch error.

    `error` stays the human-readable message every client already reads;
    `code` and `hint` let the UI explain what went wrong and what to do next
    without guessing from the status code.
    """
    return (
        jsonify(
            {
                "error": message,
                "code": code,
                "hint": hint,
                "symbol": symbol,
            }
        ),
        status,
    )


@bp.get("/api/dividends")
def api_dividends():
    # Resolve the fetcher through the server module so callers and tests that
    # replace `server.get_dividends` keep working after the blueprint split.
    import server

    symbol = request.args.get("symbol", "").strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        return _error(
            "invalid_symbol",
            "That doesn't look like a valid NSE ticker.",
            "Use 2–15 letters or digits, with optional hyphens (for example "
            "RELIANCE, TCS, BAJAJ-AUTO). Spaces and the .NS suffix are removed "
            "automatically.",
            400,
            symbol,
        )
    try:
        return jsonify(server.get_dividends(symbol))
    except LookupError:
        return _error(
            "no_history",
            f"No dividend history found for {symbol}.",
            "The ticker may be delisted, renamed, or may never have paid a "
            "dividend. Check the symbol on nseindia.com and try again.",
            404,
            symbol,
        )
    except ValueError as exc:
        return _error(
            "invalid_symbol",
            str(exc),
            "Check the symbol spelling. Use the NSE trading symbol, not the "
            "company name.",
            400,
            symbol,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _error(
                "unknown_ticker",
                f"stockanalysis.com has no page for {symbol}.",
                "Check that the NSE symbol is current. A renamed or delisted "
                "company may no longer have a dividend page.",
                404,
                symbol,
            )
        if exc.code in (403, 429):
            return _error(
                "rate_limited",
                "The dividend source is rate-limiting requests.",
                "Wait a minute, then press Retry. Data already fetched is "
                "cached for 6 hours, so the rest of the tracker keeps working.",
                429,
                symbol,
            )
        if exc.code >= 500:
            return _error(
                "source_down",
                "The dividend source returned a server error.",
                "This is usually temporary. Press Retry in a moment.",
                502,
                symbol,
            )
        return _error(
            "upstream",
            f"The dividend source rejected the request (HTTP {exc.code}).",
            "Press Retry. If it keeps failing, copy the debug info and check "
            "the server log.",
            502,
            symbol,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        detail = getattr(exc, "reason", None) or str(exc)
        return _error(
            "network",
            "Could not reach stockanalysis.com.",
            "Check your internet connection, then press Retry. If you are "
            "behind a proxy or firewall, allow stockanalysis.com. "
            f"Details: {detail}",
            502,
            symbol,
        )
    except Exception as exc:
        return _error(
            "upstream",
            "Unexpected error while fetching dividend data.",
            "Press Retry. If it keeps failing, copy the debug info and check "
            f"the server log. Details: {exc}",
            502,
            symbol,
        )
