"""Streaming market-structure (swing) detector — Condition B of the trend bias.

Confirms swing highs/lows with a simple fractal rule: a bar is a swing high if
its high is the strict maximum of a window of ``2*lookback+1`` bars centred on
it (so a swing is only confirmed ``lookback`` bars after it prints). Tracks the
last two confirmed swing highs and lows and reports the directional bias:

    Higher-High + Higher-Low  -> Direction.LONG   (bullish structure)
    Lower-High  + Lower-Low   -> Direction.SHORT  (bearish structure)
    anything else / not enough swings -> None      (neutral / unknown)

Pure, sync, stdlib only — same contract style as :mod:`orb.indicators`. Fed one
bar at a time via :meth:`update`; :meth:`reset` clears it at a session boundary.
"""

from __future__ import annotations

from collections import deque

from ..models import Direction


class SwingStructure:
    """Fractal swing-high/low tracker over a centred ``2*lookback+1`` window."""

    __slots__ = ("lookback", "_highs", "_lows", "_swing_highs", "_swing_lows")

    def __init__(self, lookback: int = 2) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = lookback
        w = 2 * lookback + 1
        self._highs: deque[float] = deque(maxlen=w)
        self._lows: deque[float] = deque(maxlen=w)
        # last two confirmed swing highs / lows (oldest first)
        self._swing_highs: deque[float] = deque(maxlen=2)
        self._swing_lows: deque[float] = deque(maxlen=2)

    def update(self, high: float, low: float) -> None:
        """Feed one closed bar; confirms the bar that is now ``lookback`` bars old."""
        self._highs.append(high)
        self._lows.append(low)
        w = self._highs.maxlen
        if len(self._highs) < w:
            return
        c = self.lookback  # centre index of a full window
        ch = self._highs[c]
        if all(ch > self._highs[j] for j in range(w) if j != c):
            self._swing_highs.append(ch)
        cl = self._lows[c]
        if all(cl < self._lows[j] for j in range(w) if j != c):
            self._swing_lows.append(cl)

    @property
    def bias(self) -> Direction | None:
        """Bullish if making HH+HL, bearish if LH+LL, else None (neutral)."""
        if len(self._swing_highs) < 2 or len(self._swing_lows) < 2:
            return None
        hh = self._swing_highs[-1] > self._swing_highs[-2]
        hl = self._swing_lows[-1] > self._swing_lows[-2]
        lh = self._swing_highs[-1] < self._swing_highs[-2]
        ll = self._swing_lows[-1] < self._swing_lows[-2]
        if hh and hl:
            return Direction.LONG
        if lh and ll:
            return Direction.SHORT
        return None

    def reset(self) -> None:
        self._highs.clear()
        self._lows.clear()
        self._swing_highs.clear()
        self._swing_lows.clear()
