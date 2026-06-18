"""ForexFactory economic-calendar collector (forward schedule + impact).

Source: the FairEconomy weekly JSON feed that mirrors the ForexFactory calendar
(``ff_calendar_thisweek.json``). It is a JSON endpoint (no HTML scraping, no API
key) — far more stable than parsing the HTML page. Each row looks like::

    {"title":"CPI m/m","country":"USD","date":"2026-06-17T08:30:00-04:00",
     "impact":"High","forecast":"0.3%","previous":"0.2%"}

``parse_calendar`` is pure (takes already-decoded JSON) and tolerant: a junk row
is skipped, the rest are kept. ``fetch`` does the network I/O behind an injectable
``opener`` so the test suite never hits the wire.

Free + open-source per D-013. Health note: if the feed shape changes or the host
is down, ``fetch`` raises / returns ``[]`` and the daemon degrades (keeps the last
state until it ages out -> consumer falls back to ``default_when_stale=allow``).
"""

from __future__ import annotations

import json
import urllib.request

from ..normalizer import RawEvent, classify_kind, normalize_impact, parse_ts

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SOURCE = "forexfactory"
_UA = "Mozilla/5.0 (orb-macro sidecar; +local)"


def _default_opener(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted feed)
        return r.read()


def _parse_row(row: dict) -> RawEvent:
    title = str(row["title"])
    ts = parse_ts(row["date"])                  # raises on missing/garbage -> skipped
    return RawEvent(
        source=SOURCE,
        ts=ts,
        title=title,
        currency=str(row.get("country", "") or "").upper(),
        impact=normalize_impact(row.get("impact", "")),
        kind=classify_kind(title),
        forecast=(row.get("forecast") or None),
        previous=(row.get("previous") or None),
        actual=(row.get("actual") or None),
    )


def parse_calendar(data) -> list[RawEvent]:
    """Decode a JSON list of calendar rows into RawEvents. Tolerant of junk rows."""
    if not isinstance(data, list):
        return []
    out: list[RawEvent] = []
    for row in data:
        try:
            out.append(_parse_row(row))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda e: e.ts)
    return out


def fetch(url: str = DEFAULT_URL, opener=None) -> list[RawEvent]:
    """Fetch + parse the calendar. ``opener(url) -> bytes`` is injectable for tests."""
    raw = (opener or _default_opener)(url)
    return parse_calendar(json.loads(raw))
