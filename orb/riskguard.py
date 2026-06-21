"""Risk guards: daily loss circuit breaker + momentum-spike limit cancel.

Tracks the account balance at the first observation of each UTC day; once the
drawdown from that day-start reaches ``max_daily_loss``, trading halts until
the next UTC day.
"""

from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("orb.riskguard")


class DailyLossBreaker:
    """Halt after the day's drawdown reaches the cap. The cap is either a flat
    account-currency amount (``max_daily_loss``) or a percentage of the day's
    starting balance (``max_daily_loss_pct``). Percentage mode tracks compounding
    equity: the cap is recomputed from each new day's opening balance."""

    def __init__(self, max_daily_loss: float | None = None, *,
                 max_daily_loss_pct: float | None = None) -> None:
        if max_daily_loss is None and max_daily_loss_pct is None:
            raise ValueError("one of max_daily_loss / max_daily_loss_pct required")
        if max_daily_loss is not None and max_daily_loss <= 0:
            raise ValueError("max_daily_loss must be > 0")
        if max_daily_loss_pct is not None and max_daily_loss_pct <= 0:
            raise ValueError("max_daily_loss_pct must be > 0")
        self.max_daily_loss = max_daily_loss
        self.max_daily_loss_pct = max_daily_loss_pct
        self._day: date | None = None
        self._day_start: float = 0.0
        self._day_cap: float = float(max_daily_loss or 0.0)
        self._delta: float = 0.0
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def day_pnl(self) -> float:
        return 0.0 if self._day is None else self._delta

    @property
    def day_cap(self) -> float:
        """The active loss cap for the current day (flat or pct-derived)."""
        return self._day_cap

    def update(self, day: date, balance: float) -> bool:
        """Feed the current UTC date + balance; returns True while halted."""
        if day != self._day:
            self._day = day
            self._day_start = balance
            if self.max_daily_loss_pct is not None:
                self._day_cap = balance * (self.max_daily_loss_pct / 100.0)
            else:
                self._day_cap = self.max_daily_loss
            if self._halted:
                log.info("daily_loss_breaker_reset day=%s start=%s", day, balance)
            self._halted = False
        self._delta = balance - self._day_start
        if not self._halted and self._day_start - balance >= self._day_cap:
            self._halted = True
            log.warning("DAILY_LOSS_HALT day=%s start=%s now=%s loss=%.2f cap=%.2f",
                        day, self._day_start, balance,
                        self._day_start - balance, self._day_cap)
        return self._halted


class SpikeCancel:
    """Cancel-unfilled-limits trigger: a 1m bar whose range is ``ratio`` times
    the average range of the previous ``lookback`` bars signals abnormal
    momentum — pending limits that haven't filled should be pulled."""

    def __init__(self, ratio: float = 2.5, lookback: int = 20,
                 min_bars: int = 5) -> None:
        if ratio <= 1.0:
            raise ValueError("ratio must be > 1")
        self.ratio = ratio
        self.lookback = lookback
        self.min_bars = min_bars
        self._ranges: list[float] = []

    def update(self, high: float, low: float) -> bool:
        """Feed a closed bar; True if THIS bar is a momentum spike."""
        rng = high - low
        history = self._ranges[-self.lookback:]
        total = sum(history)
        avg = total / len(history) if history else 0.0
        spike = len(history) >= self.min_bars and total > 0 and rng >= self.ratio * avg
        self._ranges.append(rng)
        if len(self._ranges) > self.lookback:
            self._ranges = self._ranges[-self.lookback:]
        if spike:
            log.warning("momentum_spike range=%.2f avg=%.2f ratio=%.1fx",
                        rng, avg, rng / avg)
        return spike
