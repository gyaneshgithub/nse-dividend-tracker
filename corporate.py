#!/usr/bin/env python3
"""Upcoming NSE bonus and stock-split actions.

Read-only. Fetches the NSE corporate-actions feed, keeps only bonus issues
and share splits, and flags the ones that match a tracked holding. Results
are cached in memory, so the dashboard can poll this cheaply.

Only bonus and split actions are surfaced here. Dividends, buy-backs,
rights issues and scheme-of-arrangement entries are ignored on purpose: this
feed exists to warn about share-count changes, not to duplicate the payout
calendar.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

SOURCE = "https://www.nseindia.com/api/corporates-corporateActions"
SOURCE_LABEL = "nseindia.com corporate actions"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_DAYS = 60
DEFAULT_CACHE_TTL = 6 * 3600

_BONUS_RE = re.compile(r"\bbonus\b", re.I)
_SPLIT_RE = re.compile(r"\bsplit\b|sub-?division", re.I)
_PREFERENCE_RE = re.compile(r"\bncrps\b|preference", re.I)
_DATE_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")
_MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
)}
_RATIO_RE = re.compile(r"(\d+)\s*:\s*(\d+)")
_FACE_VALUE_RE = re.compile(
    r"from\s+rs\.?\s*([0-9.]+)\s*/?-?\s*per\s+share\s+to\s+rs\.?\s*([0-9.]+)", re.I
)

_cache: dict[str, tuple[float, list]] = {}


def _cache_ttl() -> int:
    try:
        return max(0, int(os.environ.get("DIVIDEND_ACTIONS_TTL", DEFAULT_CACHE_TTL)))
    except ValueError:
        return DEFAULT_CACHE_TTL


def _parse_ex_date(value) -> date | None:
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name[:3].title())
    if month is None:
        return None
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def classify(subject: str) -> str | None:
    """Return "bonus", "split", or None for actions we do not notify about."""
    text = str(subject or "")
    if not text:
        return None
    # "Bonus NCRPS" is a preference-share issue, not an equity bonus, and it
    # never changes the ordinary share count in the tracker.
    if _PREFERENCE_RE.search(text):
        return None
    if _SPLIT_RE.search(text):
        return "split"
    if _BONUS_RE.search(text):
        return "bonus"
    return None


def describe(subject: str, kind: str) -> str:
    """A short, human-readable ratio/detail line for the notification."""
    text = str(subject or "")
    if kind == "split":
        face = _FACE_VALUE_RE.search(text)
        if face:
            return f"Face value ₹{face.group(1)} → ₹{face.group(2)}"
        ratio = _RATIO_RE.search(text)
        if ratio:
            return f"{ratio.group(1)}:{ratio.group(2)}"
        return "Share split"
    ratio = _RATIO_RE.search(text)
    if ratio:
        return f"Bonus {ratio.group(1)}:{ratio.group(2)}"
    return "Bonus issue"


def parse_actions(rows, today: date | None = None, days: int = DEFAULT_DAYS) -> list[dict]:
    """Filter raw NSE rows to bonus/split actions inside the look-ahead window."""
    today = today or date.today()
    horizon = today + timedelta(days=max(0, int(days)))
    actions: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject") or "").strip()
        kind = classify(subject)
        if kind is None:
            continue
        ex_date = _parse_ex_date(row.get("exDate"))
        if ex_date is None or ex_date < today or ex_date > horizon:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        action_id = f"ca:{symbol}:{ex_date.isoformat()}:{kind}"
        if action_id in seen:
            continue
        seen.add(action_id)
        actions.append(
            {
                "id": action_id,
                "symbol": symbol,
                "name": str(row.get("comp") or symbol).strip(),
                "type": kind,
                "exDate": ex_date.isoformat(),
                "daysAway": (ex_date - today).days,
                "detail": describe(subject, kind),
                "subject": subject,
            }
        )
    actions.sort(key=lambda item: (item["daysAway"], item["symbol"], item["type"]))
    return actions


def _fetch_rows(start: date, end: date) -> list:
    query = urllib.parse.urlencode(
        {
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        }
    )
    request = urllib.request.Request(
        f"{SOURCE}?{query}",
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": REFERER,
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=15, context=context) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, list):
        raise ValueError("Unexpected corporate-actions response")
    return payload


def _rows_for_window(today: date, days: int) -> list:
    key = f"{today.isoformat()}:{int(days)}"
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < _cache_ttl():
        return cached[1]
    rows = _fetch_rows(today, today + timedelta(days=max(0, int(days))))
    _cache[key] = (time.time(), rows)
    return rows


def reset_cache() -> None:
    _cache.clear()


def upcoming(holdings=None, days: int = DEFAULT_DAYS) -> dict:
    """Bonus/split actions in the next ``days``, with tracked holdings flagged.

    Never raises: a network or upstream failure comes back as ``ok: False``
    plus an ``error`` string so the UI can degrade to a quiet notice.
    """
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    today = date.today()
    held = {str(item.get("ticker") or "").upper() for item in (holdings or [])} - {""}
    payload = {
        "ok": False,
        "source": SOURCE_LABEL,
        "asOf": today.isoformat(),
        "days": days,
        "actions": [],
        "heldCount": 0,
        "error": None,
    }
    try:
        rows = _rows_for_window(today, days)
    except Exception as exc:  # network, JSON, NSE hiccup
        payload["error"] = str(exc) or exc.__class__.__name__
        return payload

    actions = parse_actions(rows, today, days)
    for action in actions:
        action["held"] = action["symbol"] in held
    actions.sort(key=lambda item: (not item["held"], item["daysAway"], item["symbol"]))
    payload.update(
        {
            "ok": True,
            "actions": actions,
            "heldCount": sum(1 for action in actions if action["held"]),
        }
    )
    return payload
