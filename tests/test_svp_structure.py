"""SwingStructure (Condition B market-structure) bias detector."""

from orb.models import Direction
from orb.svp.structure import SwingStructure

# Zigzag (high, low) per bar. lookback=2 -> a swing is the strict extreme of a
# 5-bar window, confirmed 2 bars later. This series prints, in order:
#   swing high H1=5 @bar2, swing low L1=2 @bar4, swing high H2=7 @bar6,
#   swing low L2=4 @bar8  -> Higher-High + Higher-Low -> bullish.
UPTREND = [
    (3.0, 2.0), (4.0, 3.0), (5.0, 4.0), (4.0, 3.0), (3.0, 2.0),
    (5.0, 4.0), (7.0, 6.0), (6.0, 5.0), (5.0, 4.0), (6.0, 5.0), (7.0, 6.0),
]


def feed(seq, lookback=2):
    s = SwingStructure(lookback)
    for hi, lo in seq:
        s.update(hi, lo)
    return s


def test_uptrend_is_bullish():
    s = feed(UPTREND)
    assert s.bias is Direction.LONG


def test_downtrend_is_bearish():
    # mirror the uptrend across price -> LH + LL
    downtrend = [(-lo, -hi) for hi, lo in UPTREND]
    s = feed(downtrend)
    assert s.bias is Direction.SHORT


def test_insufficient_swings_is_neutral():
    s = feed(UPTREND[:5])           # at most one confirmed swing each side
    assert s.bias is None


def test_flat_series_is_neutral():
    s = feed([(5.0, 4.0)] * 11)     # no strict extreme -> no swings
    assert s.bias is None


def test_reset_clears_state():
    s = feed(UPTREND)
    assert s.bias is Direction.LONG
    s.reset()
    assert s.bias is None


def test_lookback_validation():
    import pytest
    with pytest.raises(ValueError):
        SwingStructure(0)
