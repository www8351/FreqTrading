"""FRED collector — authoritative macro *actuals* (St. Louis Fed). Free API key.

FRED is the sanctioned source for released figures: PAYEMS (NFP), CPIAUCSL (CPI),
DFF (Fed Funds), PPIACO (PPI), GDPC1 (GDP). It is a forward-actuals source, not a
schedule — the ForexFactory feed supplies the forecast + release time. The scorer
computes surprise from the calendar's forecast/actual; FRED is the cross-check /
fallback when the calendar's ``actual`` lags the official print.

Auth: ``FRED_API_KEY`` env var (or ``api_key=``). Pure ``parse_observations`` +
network ``fetch_series`` with an injectable opener (no live network in tests).
Free + open-source per D-013.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date

DEFAULT_BASE = "https://api.stlouisfed.org/fred/series/observations"
# FRED series id -> our event kind (matches macro.sensitivity keys)
SERIES_KIND = {
    "PAYEMS": "NFP",
    "CPIAUCSL": "CPI",
    "DFF": "FOMC",
    "PPIACO": "PPI",
    "GDPC1": "GDP",
}
_UA = "Mozilla/5.0 (orb-macro sidecar; +local)"


class FredError(Exception):
    """FRED fetch/auth failure (non-fatal: the daemon degrades to calendar-only)."""


def _resolve_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("FRED_API_KEY")
    if not key:
        raise FredError("FRED_API_KEY not set (pass api_key= or env var)")
    return key


def _default_opener(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted)
        return r.read()


def parse_observations(data) -> list[tuple[date, float]]:
    """Decode FRED observations JSON into ascending (date, value) pairs.
    Skips '.' (missing) values. Tolerant of junk rows."""
    out: list[tuple[date, float]] = []
    for row in (data or {}).get("observations", []):
        try:
            val = row["value"]
            if val in (".", "", None):
                continue
            out.append((date.fromisoformat(row["date"]), float(val)))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def fetch_series(series_id: str, api_key: str | None = None, opener=None,
                 base: str = DEFAULT_BASE, limit: int = 4) -> list[tuple[date, float]]:
    """Fetch the most recent ``limit`` observations for ``series_id`` (ascending)."""
    params = urllib.parse.urlencode({
        "series_id": series_id, "api_key": _resolve_key(api_key),
        "file_type": "json", "sort_order": "desc", "limit": limit,
    })
    raw = (opener or _default_opener)(f"{base}?{params}")
    obs = parse_observations(json.loads(raw))
    return obs


def latest(series_id: str, **kw) -> tuple[date, float] | None:
    obs = fetch_series(series_id, **kw)
    return obs[-1] if obs else None
