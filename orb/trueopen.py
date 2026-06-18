"""True Open levels (port of the "OT Trades" TradingView indicator).

Tracks, from a stream of UTC 1m candles, the open price captured at key
New York times:

    TDO          00:00 NY (True Day Open)
    session open 01:30 NY (True London) / 07:30 NY (True New York)
                 / 13:30 NY (True PM) — latest one wins
    TWO          Monday 18:00 NY (True Week Open)
    90m cycle    03:23 / 09:23 / 15:23 NY

Derived reads:
    bias(close)  -> "bullish" | "bearish" | None   (close vs session open)
    zone(close)  -> "premium" | "discount" | "dead_zone" | None
                    (close vs TDO + session open + TWO, as in the indicator)

Feed bars with ``update(candle)``; levels survive gaps — a level is set on the
first bar whose NY time crosses the slot time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Candle

NY = ZoneInfo("America/New_York")

_SESSION_SLOTS = (time(1, 30), time(7, 30), time(13, 30))
_SESSION_NAMES = {time(1, 30): "london", time(7, 30): "new_york", time(13, 30): "pm"}
_CYCLE_SLOTS = (time(3, 23), time(9, 23), time(15, 23))


@dataclass
class TrueOpens:
    tdo: float | None = None            # True Day Open (00:00 NY)
    session_open: float | None = None   # latest of London/NY/PM true opens
    session_name: str | None = None
    week_open: float | None = None      # Monday 18:00 NY
    cycle_open: float | None = None     # latest 90m cycle open


class TrueOpenTracker:
    """Incremental tracker; feed closed 1m candles in ascending UTC order."""

    def __init__(self) -> None:
        self.levels = TrueOpens()
        self._prev_ny: datetime | None = None

    # ------------------------------------------------------------------ #
    def update(self, c: Candle) -> TrueOpens:
        ny = c.ts.astimezone(NY)
        prev = self._prev_ny
        self._prev_ny = ny
        if prev is None:
            prev = ny - timedelta(minutes=1)

        if self._crossed(prev, ny, time(0, 0)):
            self.levels.tdo = c.open
        for slot in _SESSION_SLOTS:
            if self._crossed(prev, ny, slot):
                self.levels.session_open = c.open
                self.levels.session_name = _SESSION_NAMES[slot]
        for slot in _CYCLE_SLOTS:
            if self._crossed(prev, ny, slot):
                self.levels.cycle_open = c.open
        if ny.weekday() == 0 and self._crossed(prev, ny, time(18, 0)):
            self.levels.week_open = c.open
        return self.levels

    @staticmethod
    def _crossed(prev: datetime, cur: datetime, slot: time) -> bool:
        """True if the slot time on cur's NY date falls in (prev, cur]."""
        target = cur.replace(hour=slot.hour, minute=slot.minute,
                             second=0, microsecond=0)
        return prev < target <= cur

    # ------------------------------------------------------------------ #
    def bias(self, close: float) -> str | None:
        so = self.levels.session_open
        if so is None:
            return None
        return "bullish" if close > so else "bearish"

    def zone(self, close: float) -> str | None:
        lv = self.levels
        if lv.tdo is None or lv.session_open is None or lv.week_open is None:
            return None
        above = (close > lv.tdo, close > lv.session_open, close > lv.week_open)
        if all(above):
            return "premium"
        if not any(above):
            return "discount"
        return "dead_zone"
