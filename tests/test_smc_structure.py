"""StructureTracker (SMC BOS/CHOCH market-structure) unit tests."""

from datetime import datetime, timedelta, timezone

import pytest

from orb.models import Candle, Direction
from orb.smc.structure import StructureTracker, SwingPoint

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def mk(i: int, h: float, lo: float, c: float, o: float | None = None) -> Candle:
    """Candle at T0+i minutes; open defaults to mid so OHLC stays sane."""
    if o is None:
        o = (h + lo) / 2.0
    return Candle(ts=T0 + timedelta(minutes=i), open=o, high=h, low=lo, close=c)


def feed(t: StructureTracker, bars, start: int = 0):
    """Feed (high, low, close) tuples; return emitted StructureEvents."""
    out = []
    for i, (h, lo, c) in enumerate(bars, start):
        ev = t.update(mk(i, h, lo, c))
        if ev is not None:
            out.append(ev)
    return out


# lookback=2 -> 5-bar centred window, swing confirmed 2 bars after its bar.
# Bars 0-4 sculpt a single swing high 5.0 at bar 2 (strict max), no swing low
# (centre low 4.0 is not the window min). Closes never exceed 5.0.
SEQ_HIGH = [
    (3.0, 2.0, 2.5),
    (4.0, 3.0, 3.5),
    (5.0, 4.0, 4.5),   # swing high 5.0 @ bar 2
    (4.0, 3.0, 3.5),
    (3.0, 2.0, 2.5),   # window full -> bar 2 confirms here
]

# Mirror: single swing low 2.0 at bar 2, confirmed at bar 4. No swing high.
SEQ_LOW = [
    (5.0, 4.0, 4.5),
    (4.0, 3.0, 3.5),
    (3.5, 2.0, 3.0),   # swing low 2.0 @ bar 2
    (4.0, 3.0, 3.5),
    (5.0, 4.0, 4.5),
]

BOS_BAR = (6.0, 3.8, 5.5)  # close 5.5 > ref high 5.0 -> BOS LONG

# Appended after SEQ_HIGH + BOS_BAR: confirms swing high 6.0 @ bar 5 (at bar 7)
# and swing low 3.0 @ bar 7 (at bar 9); bar 10 closes below 3.0 -> CHOCH SHORT.
CHOCH_TAIL = [
    (5.6, 4.5, 5.0),
    (5.1, 3.0, 4.0),
    (5.0, 3.5, 4.5),
    (5.2, 4.0, 5.0),
    (4.2, 2.0, 2.5),   # close 2.5 < ref low 3.0
]


# --------------------------------------------------------------------------- #
# 1. swing confirmation timing
# --------------------------------------------------------------------------- #
def test_swing_high_confirms_lookback_bars_late():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_HIGH[:4])
    assert t.last_swing_high is None          # bar 2 not yet confirmable
    feed(t, SEQ_HIGH[4:], start=4)            # bar 4 = bar 2 + lookback
    assert t.last_swing_high == SwingPoint(T0 + timedelta(minutes=2), 5.0)


def test_swing_low_confirms():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_LOW)
    assert t.last_swing_low == SwingPoint(T0 + timedelta(minutes=2), 2.0)
    assert t.last_swing_high is None


# --------------------------------------------------------------------------- #
# 2-3. BOS then CHOCH
# --------------------------------------------------------------------------- #
def test_close_above_ref_high_is_bos_long():
    t = StructureTracker(lookback=2)
    events = feed(t, SEQ_HIGH + [BOS_BAR])
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "BOS"
    assert ev.direction is Direction.LONG
    assert ev.level == 5.0
    assert ev.ts == T0 + timedelta(minutes=5)
    assert t.trend is Direction.LONG


def test_break_below_ref_low_after_long_is_choch_short():
    t = StructureTracker(lookback=2)
    events = feed(t, SEQ_HIGH + [BOS_BAR] + CHOCH_TAIL)
    assert [e.kind for e in events] == ["BOS", "CHOCH"]
    ch = events[1]
    assert ch.direction is Direction.SHORT
    assert ch.level == 3.0
    assert ch.ts == T0 + timedelta(minutes=10)
    assert t.trend is Direction.SHORT


# --------------------------------------------------------------------------- #
# 4. broken ref consumed
# --------------------------------------------------------------------------- #
def test_consumed_ref_emits_no_second_event():
    t = StructureTracker(lookback=2)
    # bar 6 closes above the already-broken 5.0 level; no new swing high has
    # confirmed post-break, so the ref is gone -> no event.
    events = feed(t, SEQ_HIGH + [BOS_BAR, (5.8, 5.2, 5.6)])
    assert len(events) == 1
    assert events[0].kind == "BOS"


# --------------------------------------------------------------------------- #
# 5. close-based breaks only
# --------------------------------------------------------------------------- #
def test_wick_poke_above_ref_high_is_not_a_break():
    t = StructureTracker(lookback=2)
    events = feed(t, SEQ_HIGH + [(5.5, 3.9, 4.5)])  # high 5.5 > 5.0, close 4.5
    assert events == []
    assert t.trend is None


# --------------------------------------------------------------------------- #
# 6. sweep-and-reclaim queries
# --------------------------------------------------------------------------- #
def test_swept_low_reclaim_returns_level():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_LOW)
    assert t.swept_low(mk(5, 3.2, 1.8, 3.0)) == 2.0     # wick below, close above
    assert t.swept_low(mk(5, 3.1, 1.8, 1.9)) is None    # closed below level
    assert t.swept_low(mk(5, 3.2, 2.5, 3.0)) is None    # no wick below level


def test_swept_high_mirror():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_HIGH)
    assert t.swept_high(mk(5, 5.5, 4.0, 4.4)) == 5.0    # wick above, close below
    assert t.swept_high(mk(5, 5.5, 4.0, 5.2)) is None   # closed above level


def test_sweep_queries_none_without_swings():
    t = StructureTracker(lookback=2)
    assert t.swept_low(mk(0, 3.0, 1.0, 2.0)) is None
    assert t.swept_high(mk(0, 3.0, 1.0, 2.0)) is None


# --------------------------------------------------------------------------- #
# 7. bounded memory
# --------------------------------------------------------------------------- #
def test_swing_deques_bounded_by_max_swings():
    t = StructureTracker(lookback=2, max_swings=3)
    # period-4 zigzag: one swing high (5.0) and one swing low (2.0) per cycle;
    # closes stay inside 2.0..5.0 so no ref ever breaks.
    cycle = [(3.0, 2.0, 2.5), (4.0, 3.0, 3.5), (5.0, 4.0, 4.5), (4.0, 3.0, 3.5)]
    events = feed(t, cycle * 20)
    assert events == []
    assert len(t._highs) == 3
    assert len(t._lows) == 3


# --------------------------------------------------------------------------- #
# 8. trend / reset
# --------------------------------------------------------------------------- #
def test_trend_none_before_first_break():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_HIGH)
    assert t.trend is None


def test_reset_clears_everything():
    t = StructureTracker(lookback=2)
    feed(t, SEQ_HIGH + [BOS_BAR])
    assert t.trend is Direction.LONG
    t.reset()
    assert t.trend is None
    assert t.last_swing_high is None
    assert t.last_swing_low is None
    # window cleared too: a fresh partial feed confirms nothing
    feed(t, SEQ_HIGH[:4])
    assert t.last_swing_high is None


def test_lookback_validation():
    with pytest.raises(ValueError):
        StructureTracker(lookback=0)
