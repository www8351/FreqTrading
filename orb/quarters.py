"""Quarters Theory cycles (port of the "Sav FX" TradingView indicator,
methodology per Brain.md).

Every cycle splits into four quarters with algorithmic roles:
    Q1 accumulation, Q2 manipulation, Q3 distribution (the tradeable move),
    Q4 reversal/continuation.
The cycle's True Open = the open of its Q2; price above it = premium
(look for shorts), below = discount (look for longs).

Cycles implemented (NY-time anchored, matching the Pine):
    day  - trading day starts 18:00 NY; quarters are the four 6h sessions:
           Q1 Asia 18-00, Q2 London 00-06, Q3 AM 06-12, Q4 PM 12-18
    m90  - each 6h session splits into 4 x 90min quarters
           (the 0:00 / 1:30 / 3:00 / 4:30 ... NY grid)

``QuarterTracker.update(candle)`` feeds bars; ``q2_open`` per cycle is
captured on the first bar at-or-after the Q2 boundary (gap-safe).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Candle

NY = ZoneInfo("America/New_York")


def _ny(ts: datetime) -> datetime:
    return ts.astimezone(NY)


def day_quarter(ts: datetime) -> str:
    """Q1 Asia 18-00 / Q2 London 00-06 / Q3 AM 06-12 / Q4 PM 12-18 (NY)."""
    h = _ny(ts).hour
    if h >= 18:
        return "Q1"
    if h < 6:
        return "Q2"
    if h < 12:
        return "Q3"
    return "Q4"


def m90_quarter(ts: datetime) -> str:
    """Quarter within the current 6h session: 4 x 90min on the NY grid."""
    t = _ny(ts)
    minutes = (t.hour % 6) * 60 + t.minute
    return f"Q{minutes // 90 + 1}"


def quarters(ts: datetime) -> dict:
    return {"day": day_quarter(ts), "m90": m90_quarter(ts)}


def _day_cycle_id(ts: datetime) -> str:
    """Trading-day id: the NY date the 18:00-anchored day ENDS on."""
    t = _ny(ts)
    if t.hour >= 18:
        t = t + timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def _m90_cycle_id(ts: datetime) -> str:
    t = _ny(ts)
    session_start_hour = (t.hour // 6) * 6
    return f"{t:%Y-%m-%d}T{session_start_hour:02d}"


@dataclass
class TrueOpenState:
    day_q2_open: float | None = None   # open of London 00:00 NY (== TDO)
    m90_q2_open: float | None = None   # open of the session's 2nd 90min block


class QuarterTracker:
    """Tracks the Q2 True Open of the day and 90-min cycles."""

    def __init__(self) -> None:
        self.state = TrueOpenState()
        self._day_id: str | None = None
        self._m90_id: str | None = None

    def update(self, c: Candle) -> TrueOpenState:
        day_id = _day_cycle_id(c.ts)
        if day_id != self._day_id:
            self._day_id = day_id
            self.state.day_q2_open = None
        if self.state.day_q2_open is None and day_quarter(c.ts) in ("Q2", "Q3", "Q4"):
            self.state.day_q2_open = c.open

        m90_id = _m90_cycle_id(c.ts)
        if m90_id != self._m90_id:
            self._m90_id = m90_id
            self.state.m90_q2_open = None
        if self.state.m90_q2_open is None and m90_quarter(c.ts) in ("Q2", "Q3", "Q4"):
            self.state.m90_q2_open = c.open
        return self.state

    def value_zone(self, close: float, cycle: str = "day") -> str | None:
        """Brain.md fair-value read vs the cycle's True Open (Q2 open):
        premium (above, look for shorts) / discount (below, look for longs)."""
        ref = (self.state.day_q2_open if cycle == "day"
               else self.state.m90_q2_open)
        if ref is None:
            return None
        return "premium" if close > ref else "discount"
