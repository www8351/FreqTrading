"""Shared test helpers: candle builder + a fast-warmup config."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orb import Candle, OrbConfig

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


def mk(minute: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Candle:
    return Candle(ts=BASE + timedelta(minutes=minute), open=o, high=h, low=l, close=c, volume=v)


def make_cfg(**overrides) -> OrbConfig:
    """Small periods so warmup completes within the first few session bars.

    range window = minutes 0,1,2 (range_minutes=3); first armed bar = minute 3.
    ATR ready at 3 bars, ROC ready at 3 closes -> warm by minute 2/3.
    """
    base = dict(
        range_minutes=3,
        atr_period=3,
        atr_mult=2.0,
        roc_period=2,
        roc_min=0.0,
        use_rvol=False,
        session_len_min=60,
        instrument_dp=2,
    )
    base.update(overrides)
    return OrbConfig(**base)


def long_session():
    """3-bar range (H=2001,L=1999), long breakout @m3, reentry exit @m5."""
    return [
        mk(0, 2000, 2000.5, 1999.5, 2000),
        mk(1, 2000, 2001.0, 1999.0, 2000),
        mk(2, 2000, 2000.5, 1999.5, 2000),
        mk(3, 2002, 2006.0, 2001.5, 2005),
        mk(4, 2004, 2005.0, 2003.0, 2004),
        mk(5, 2004, 2004.5, 1998.0, 1998),
    ]


def short_session():
    """3-bar range, short breakout @m3, reentry exit @m5."""
    return [
        mk(0, 2000, 2000.5, 1999.5, 2000),
        mk(1, 2000, 2001.0, 1999.0, 2000),
        mk(2, 2000, 2000.5, 1999.5, 2000),
        mk(3, 1999, 1999.5, 1994.0, 1995),
        mk(4, 1996, 1997.0, 1995.0, 1996),
        mk(5, 1998, 2003.0, 1997.0, 2002),
    ]
