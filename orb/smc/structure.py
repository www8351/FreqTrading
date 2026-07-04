"""Streaming market-structure BOS/CHOCH tracker over one timeframe's candles.

Fractal swing confirmation identical in spirit to
:class:`orb.svp.structure.SwingStructure`: a bar is a swing high when its high
is the STRICT max of the centred ``2*lookback+1`` window (mirror for lows), so
a swing is confirmed ``lookback`` bars after it prints. The most recent
confirmed, not-yet-broken swing high/low serve as break references; a CLOSE
through a reference emits a :class:`StructureEvent` — ``"BOS"`` when it
continues the current trend (or no trend exists yet), ``"CHOCH"`` when it
flips it. Wick pokes never signal. A broken reference is consumed and stays
empty until the next swing on that side confirms after the break.

Pure, sync, stdlib only. O(1) per bar, bounded memory, no I/O. Ambiguous
state -> ``None``, never raise for market conditions.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import NamedTuple

from ..models import Candle, Direction


class SwingPoint(NamedTuple):
    ts: datetime
    price: float


class StructureEvent(NamedTuple):
    ts: datetime
    kind: str              # "BOS" | "CHOCH"
    direction: Direction
    level: float           # the swing level broken


class StructureTracker:
    """Fractal swing + close-based BOS/CHOCH detector, fed one closed bar at a time."""

    __slots__ = ("lookback", "_window", "_highs", "_lows",
                 "_ref_high", "_ref_low", "_trend")

    def __init__(self, lookback: int = 2, max_swings: int = 50) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        if max_swings < 1:
            raise ValueError("max_swings must be >= 1")
        self.lookback = lookback
        # rolling (ts, high, low) window for centred fractal confirmation
        self._window: deque[tuple[datetime, float, float]] = deque(
            maxlen=2 * lookback + 1)
        self._highs: deque[SwingPoint] = deque(maxlen=max_swings)
        self._lows: deque[SwingPoint] = deque(maxlen=max_swings)
        self._ref_high: SwingPoint | None = None   # unbroken break reference
        self._ref_low: SwingPoint | None = None
        self._trend: Direction | None = None       # None until first break

    def update(self, c: Candle) -> StructureEvent | None:
        """Feed one closed bar; return the structure break event, if any."""
        # 1. confirm the bar that is now `lookback` bars old (window centre)
        self._window.append((c.ts, c.high, c.low))
        w = self._window.maxlen
        if len(self._window) == w:
            k = self.lookback
            ts, hi, lo = self._window[k]
            if all(hi > self._window[j][1] for j in range(w) if j != k):
                sp = SwingPoint(ts, hi)
                self._highs.append(sp)
                self._ref_high = sp    # most recent unbroken reference
            if all(lo < self._window[j][2] for j in range(w) if j != k):
                sp = SwingPoint(ts, lo)
                self._lows.append(sp)
                self._ref_low = sp

        # 2. close-based break check; at most one event per bar
        up = self._ref_high is not None and c.close > self._ref_high.price
        dn = self._ref_low is not None and c.close < self._ref_low.price
        if up and dn:                  # freak bar broke both: bar body decides
            if c.close >= c.open:
                dn = False
            else:
                up = False
        if up:
            level = self._ref_high.price
            self._ref_high = None      # consumed until next post-break swing
            kind = "CHOCH" if self._trend is Direction.SHORT else "BOS"
            self._trend = Direction.LONG
            return StructureEvent(c.ts, kind, Direction.LONG, level)
        if dn:
            level = self._ref_low.price
            self._ref_low = None
            kind = "CHOCH" if self._trend is Direction.LONG else "BOS"
            self._trend = Direction.SHORT
            return StructureEvent(c.ts, kind, Direction.SHORT, level)
        return None

    @property
    def trend(self) -> Direction | None:
        """Direction of the last structure break; None until the first break."""
        return self._trend

    @property
    def last_swing_high(self) -> SwingPoint | None:
        """Most recent confirmed swing high, regardless of broken state."""
        return self._highs[-1] if self._highs else None

    @property
    def last_swing_low(self) -> SwingPoint | None:
        """Most recent confirmed swing low, regardless of broken state."""
        return self._lows[-1] if self._lows else None

    def swept_low(self, c: Candle) -> float | None:
        """Wick sweep-and-reclaim of the last swing low -> swept level, else None."""
        sl = self.last_swing_low
        if sl is not None and c.low < sl.price and c.close > sl.price:
            return sl.price
        return None

    def swept_high(self, c: Candle) -> float | None:
        """Wick sweep-and-reject of the last swing high -> swept level, else None."""
        sh = self.last_swing_high
        if sh is not None and c.high > sh.price and c.close < sh.price:
            return sh.price
        return None

    def reset(self) -> None:
        self._window.clear()
        self._highs.clear()
        self._lows.clear()
        self._ref_high = None
        self._ref_low = None
        self._trend = None
