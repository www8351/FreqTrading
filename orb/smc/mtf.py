"""Incremental 1m -> higher-timeframe candle aggregation.

``TimeframeAggregator`` folds closed 1m :class:`orb.models.Candle` bars into
higher-TF candles, O(1) per bar with O(1) memory (running scalars only, no bar
buffer). Bucketing matches ``HtfErl`` in ``scripts/backtest_sweep.py``:
intraday keys are ``(date, minute_of_day - minute_of_day % minutes)`` — H4
buckets open at 00/04/08/12/16/20 UTC — and D1 (1440) keys by date alone.

Completion is reactive: a bucket is emitted ONLY when a bar carrying a new key
arrives (Friday's last bucket completes on the first Sunday/Monday bar; gaps
are never filled). Pure stdlib, sync, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from orb.models import Candle

_DAY_MIN = 1440


class TimeframeAggregator:
    """Folds 1m candles into ``minutes``-timeframe candles incrementally.

    ``update(c)`` returns the PRIOR bucket as a completed :class:`Candle` when
    ``c`` opens a new bucket, else ``None``. ``developing`` snapshots the
    in-progress bucket. ``ts`` of every emitted candle is the bucket OPEN time
    (tz-aware UTC); OHLCV folds as first-open / max / min / last-close / sum.
    """

    def __init__(self, minutes: int) -> None:
        if not isinstance(minutes, int) or not (1 <= minutes <= _DAY_MIN):
            raise ValueError(f"minutes must be an int in 1..{_DAY_MIN}: {minutes!r}")
        if minutes < _DAY_MIN and _DAY_MIN % minutes != 0:
            raise ValueError(f"intraday minutes must divide {_DAY_MIN}: {minutes}")
        self.minutes = minutes
        self._key: tuple | None = None
        self._open_ts: datetime | None = None
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0.0

    # ------------------------------------------------------------------ #
    def _bucket(self, c: Candle) -> tuple[tuple, datetime]:
        """(key, bucket open time UTC) for the bucket containing ``c``."""
        d = c.ts.date()
        if self.minutes == _DAY_MIN:
            return (d,), datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        mins = c.ts.hour * 60 + c.ts.minute
        start = mins - mins % self.minutes
        open_ts = datetime(d.year, d.month, d.day, start // 60, start % 60,
                           tzinfo=timezone.utc)
        return (d, start), open_ts

    def _start(self, key: tuple, open_ts: datetime, c: Candle) -> None:
        self._key, self._open_ts = key, open_ts
        self._o, self._h, self._l, self._c = c.open, c.high, c.low, c.close
        self._v = c.volume

    def _snapshot(self) -> Candle:
        return Candle(ts=self._open_ts, open=self._o, high=self._h,
                      low=self._l, close=self._c, volume=self._v)

    # ------------------------------------------------------------------ #
    def update(self, c: Candle) -> Candle | None:
        """Fold ``c``; return the prior bucket iff ``c`` opens a new one."""
        key, open_ts = self._bucket(c)
        if self._key is None:
            self._start(key, open_ts, c)
            return None
        if key == self._key:
            self._h = max(self._h, c.high)
            self._l = min(self._l, c.low)
            self._c = c.close
            self._v += c.volume
            return None
        done = self._snapshot()
        self._start(key, open_ts, c)
        return done

    @property
    def developing(self) -> Candle | None:
        """Snapshot of the in-progress bucket; ``None`` before any bar."""
        return None if self._key is None else self._snapshot()

    def reset(self) -> None:
        """Drop all state (developing bucket is discarded, not emitted)."""
        self._key = None
        self._open_ts = None
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0.0
