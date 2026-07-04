"""Tests for orb.smc.mtf.TimeframeAggregator (incremental 1m -> HTF folding)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orb.models import Candle
from orb.smc.mtf import TimeframeAggregator


def mk(ts: datetime, o: float = 100.0, h: float = 101.0, l: float = 99.0,
       c: float = 100.0, v: float = 1.0) -> Candle:
    return Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v)


T0 = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1. H4 fold: full bucket 00:00..03:59, completes on the 04:00 bar
# --------------------------------------------------------------------------- #
def test_h4_full_bucket_folds_ohlcv():
    agg = TimeframeAggregator(240)
    for i in range(240):
        ts = T0 + timedelta(minutes=i)
        if i == 0:
            bar = mk(ts, o=100.5, h=101.0, l=100.0, c=100.6)   # first open = 100.5
        elif i == 100:
            bar = mk(ts, h=150.0)                              # distinct bucket high
        elif i == 200:
            bar = mk(ts, l=50.0)                               # distinct bucket low
        elif i == 239:
            bar = mk(ts, c=100.7)                              # last close = 100.7
        else:
            bar = mk(ts)
        assert agg.update(bar) is None                         # in-bucket: no emit

    done = agg.update(mk(T0 + timedelta(minutes=240)))         # 04:00 -> rollover
    assert done is not None
    assert done.ts == T0                                       # bucket open time
    assert done.ts.tzinfo is not None
    assert done.open == 100.5
    assert done.high == 150.0
    assert done.low == 50.0
    assert done.close == 100.7
    assert done.volume == pytest.approx(240.0)                 # 240 bars x v=1.0


def test_h4_buckets_align_to_00_04_08():
    agg = TimeframeAggregator(240)
    agg.update(mk(T0.replace(hour=5, minute=30)))              # lands in 04:00 bucket
    done = agg.update(mk(T0.replace(hour=8, minute=0)))        # 08:00 -> new bucket
    assert done is not None
    assert done.ts == T0.replace(hour=4, minute=0)


# --------------------------------------------------------------------------- #
# 2. D1: key by date alone, completes on first bar of the next date
# --------------------------------------------------------------------------- #
def test_d1_completes_on_new_date():
    agg = TimeframeAggregator(1440)
    assert agg.update(mk(T0, o=10.0, h=11.0, l=9.0, c=10.5, v=2.0)) is None
    assert agg.update(mk(T0.replace(hour=12),
                         o=10.5, h=20.0, l=10.0, c=15.0, v=3.0)) is None
    assert agg.update(mk(T0.replace(hour=23, minute=59),
                         o=8.0, h=9.0, l=5.0, c=7.0, v=4.0)) is None

    done = agg.update(mk(T0 + timedelta(days=1, minutes=30)))  # 2026-06-11 00:30
    assert done is not None
    assert done.ts == T0                                       # day open, midnight UTC
    assert done.open == 10.0
    assert done.high == 20.0
    assert done.low == 5.0
    assert done.close == 7.0
    assert done.volume == pytest.approx(9.0)


# --------------------------------------------------------------------------- #
# 3. Weekend gap: reactive completion on Monday's first bar
# --------------------------------------------------------------------------- #
def test_weekend_gap_completes_on_monday_bar():
    agg = TimeframeAggregator(240)
    fri = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)    # Friday
    for i in range(3):                                         # 20:00, 20:01, 20:02
        assert agg.update(mk(fri + timedelta(minutes=i))) is None

    mon = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)     # Monday
    done = agg.update(mk(mon))
    assert done is not None
    assert done.ts == fri                                      # Friday 20:00 bucket
    assert done.volume == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# 4. developing: snapshot of the partial fold; never emitted by update
# --------------------------------------------------------------------------- #
def test_developing_snapshot_and_none_before_first_bar():
    agg = TimeframeAggregator(240)
    assert agg.developing is None                              # pre-data

    assert agg.update(mk(T0, o=100.0, h=105.0, l=95.0, c=101.0, v=2.0)) is None
    assert agg.update(mk(T0 + timedelta(minutes=1),
                         o=101.0, h=110.0, l=100.0, c=102.0, v=3.0)) is None
    dev = agg.developing
    assert dev is not None
    assert dev.ts == T0
    assert dev.open == 100.0
    assert dev.high == 110.0
    assert dev.low == 95.0
    assert dev.close == 102.0
    assert dev.volume == pytest.approx(5.0)

    # rollover: emitted candle is the COMPLETED bucket; developing is the new one
    done = agg.update(mk(T0 + timedelta(hours=4), o=200.0, h=201.0, l=199.0,
                         c=200.5, v=7.0))
    assert done.ts == T0
    assert agg.developing.ts == T0 + timedelta(hours=4)
    assert agg.developing.volume == pytest.approx(7.0)

    agg.reset()
    assert agg.developing is None
    assert agg.update(mk(T0)) is None                          # fresh first bar


# --------------------------------------------------------------------------- #
# 5. minutes=1 identity passthrough
# --------------------------------------------------------------------------- #
def test_minutes_1_identity():
    agg = TimeframeAggregator(1)
    bars = [
        mk(T0, o=1.0, h=2.0, l=0.5, c=1.5, v=10.0),
        mk(T0 + timedelta(minutes=1), o=1.5, h=3.0, l=1.0, c=2.0, v=20.0),
        mk(T0 + timedelta(minutes=2), o=2.0, h=2.5, l=1.5, c=2.2, v=30.0),
    ]
    assert agg.update(bars[0]) is None
    assert agg.update(bars[1]) == bars[0]                      # prior 1m unchanged
    assert agg.update(bars[2]) == bars[1]


# --------------------------------------------------------------------------- #
# 6. Validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("minutes", [0, 7, 2000, -5, 1441])
def test_invalid_minutes_raise(minutes):
    with pytest.raises(ValueError):
        TimeframeAggregator(minutes)


@pytest.mark.parametrize("minutes", [1, 5, 15, 60, 240, 720, 1440])
def test_valid_minutes_accepted(minutes):
    TimeframeAggregator(minutes)


# --------------------------------------------------------------------------- #
# 7. Volume summed; completed ts is the bucket open time
# --------------------------------------------------------------------------- #
def test_volume_sum_and_bucket_open_ts():
    agg = TimeframeAggregator(60)
    base = T0.replace(hour=5)
    agg.update(mk(base, v=1.0))
    agg.update(mk(base + timedelta(minutes=30), v=2.5))
    agg.update(mk(base + timedelta(minutes=59), v=3.0))
    done = agg.update(mk(base + timedelta(minutes=60)))        # 06:00 -> rollover
    assert done is not None
    assert done.ts == base                                     # 05:00 bucket open
    assert done.volume == pytest.approx(6.5)
