"""Normalize raw calendar rows into a uniform ``RawEvent`` (all times UTC).

Every collector emits ``RawEvent`` so the scorer/blackout layers never see
source-specific formats. M1 uses the schedule fields (ts/impact/kind); actual/
surprise scoring arrives in M2. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# title keyword -> event kind. First match wins (order matters: rate before CPI).
_KIND_RULES = (
    ("nonfarm", "NFP"),
    ("non-farm", "NFP"),
    ("employment change", "NFP"),
    ("fomc", "FOMC"),
    ("federal funds", "FOMC"),
    ("rate decision", "FOMC"),
    ("interest rate", "FOMC"),
    ("cpi", "CPI"),
    ("consumer price", "CPI"),
    ("ppi", "PPI"),
    ("gdp", "GDP"),
    ("unemployment rate", "JOBS"),
)

_IMPACT_MAP = {"high": "high", "medium": "medium", "low": "low",
               "holiday": "holiday"}


def classify_kind(title: str) -> str:
    t = (title or "").lower()
    for needle, kind in _KIND_RULES:
        if needle in t:
            return kind
    return "OTHER"


def normalize_impact(raw: str) -> str:
    return _IMPACT_MAP.get((raw or "").strip().lower(), "unknown")


def parse_ts(raw: str) -> datetime:
    """Parse an ISO-8601 timestamp (with offset or 'Z') to tz-aware UTC."""
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:                       # assume UTC if naive
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_value(s) -> float | None:
    """Parse a calendar figure ('190K', '0.3%', '<5.50%', '-0.1%') to a float.

    Strips %, thousands separators and </>/~ qualifiers; expands K/M/B/T. Returns
    None for empty / unparseable cells (so surprise = actual - forecast can short
    out cleanly when either side is missing)."""
    if s is None:
        return None
    t = (str(s).strip()
         .replace("<", "").replace(">", "").replace("~", "")
         .replace(",", "").replace("%", "").strip())
    if not t:
        return None
    mult = 1.0
    if t[-1].lower() in _SUFFIX:
        mult = _SUFFIX[t[-1].lower()]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RawEvent:
    source: str
    ts: datetime              # tz-aware UTC
    title: str
    currency: str             # e.g. "USD"
    impact: str               # high | medium | low | holiday | unknown
    kind: str                 # NFP | CPI | FOMC | ... | OTHER
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None   # populated by the feed once released

    def reason(self) -> str:
        """Short label for a blackout reason / log."""
        return self.kind if self.kind != "OTHER" else self.title[:24]
