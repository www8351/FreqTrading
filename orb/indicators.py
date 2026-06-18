"""Incremental indicators: Wilder ATR, Rate of Change, Volume SMA.

Each is a small stateful object with the same contract:
    update(...) -> None     feed one bar
    ready: bool             True once enough bars seen
    value: float | None     None until ready

O(1) per bar, fixed memory. Feeding a non-finite input raises IndicatorError
(defensive — the engine validates candles upstream, but indicators never trust).
"""

from __future__ import annotations

import math
from collections import deque

from .models import IndicatorError


def _check(*values: float) -> None:
    for v in values:
        if not (isinstance(v, (int, float)) and math.isfinite(v)):
            raise IndicatorError(f"non-finite indicator input: {v!r}")


class WilderATR:
    """Average True Range using Wilder's smoothing.

    TR = max(high-low, |high-prev_close|, |low-prev_close|); first bar TR=high-low.
    Seed = simple mean of first ``period`` TRs (ready then); thereafter
    ATR = (ATR_prev*(period-1) + TR) / period. Needs period+1 bars for a stable
    value (one prior close for the first real TR).
    """

    __slots__ = ("period", "_prev_close", "_count", "_seed_sum", "_atr")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise IndicatorError("ATR period must be >= 1")
        self.period = period
        self._prev_close: float | None = None
        self._count = 0
        self._seed_sum = 0.0
        self._atr: float | None = None

    def update(self, high: float, low: float, close: float) -> None:
        _check(high, low, close)
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        self._count += 1

        if self._atr is not None:
            self._atr = (self._atr * (self.period - 1) + tr) / self.period
        elif self._count <= self.period:
            self._seed_sum += tr
            if self._count == self.period:
                self._atr = self._seed_sum / self.period

    @property
    def ready(self) -> bool:
        return self._atr is not None

    @property
    def value(self) -> float | None:
        return self._atr


class ROC:
    """Rate of Change in percent over ``period`` bars: (close/close[-period]-1)*100."""

    __slots__ = ("period", "_closes")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise IndicatorError("ROC period must be >= 1")
        self.period = period
        self._closes: deque[float] = deque(maxlen=period + 1)

    def update(self, close: float) -> None:
        _check(close)
        self._closes.append(close)

    @property
    def ready(self) -> bool:
        return len(self._closes) == self.period + 1

    @property
    def value(self) -> float | None:
        if not self.ready:
            return None
        ref = self._closes[0]
        if ref == 0:
            raise IndicatorError("ROC divisor is zero (corrupt price series)")
        return (self._closes[-1] / ref - 1.0) * 100.0


class VolumeSMA:
    """Simple moving average of volume over ``period`` bars (rolling sum)."""

    __slots__ = ("period", "_vols", "_sum")

    def __init__(self, period: int) -> None:
        if period < 1:
            raise IndicatorError("Volume SMA period must be >= 1")
        self.period = period
        self._vols: deque[float] = deque(maxlen=period)
        self._sum = 0.0

    def update(self, volume: float) -> None:
        _check(volume)
        if len(self._vols) == self.period:
            self._sum -= self._vols[0]
        self._vols.append(volume)
        self._sum += volume

    @property
    def ready(self) -> bool:
        return len(self._vols) == self.period

    @property
    def value(self) -> float | None:
        if not self.ready:
            return None
        return self._sum / self.period
