from datetime import datetime, timedelta, timezone

from orb.models import Candle
from orb.trueopen import TrueOpenTracker

UTC = timezone.utc


def mk(ts: datetime, o: float) -> Candle:
    return Candle(ts=ts, open=o, high=o + 1, low=o - 1, close=o + 0.5)


def test_tdo_and_session_opens_set_at_ny_times():
    tr = TrueOpenTracker()
    # 2026-06-08 is a Monday. NY = UTC-4 in June (EDT).
    start = datetime(2026, 6, 8, 3, 58, tzinfo=UTC)  # 23:58 Sun NY
    for i in range(0, 24 * 60, 1):
        ts = start + timedelta(minutes=i)
        tr.update(mk(ts, 4000.0 + i))
    # 00:00 NY Monday = 04:00 UTC -> i=2 -> open 4002
    assert tr.levels.tdo == 4002.0
    # Monday 18:00 NY = 22:00 UTC -> i=1082
    assert tr.levels.week_open == 5082.0
    # last session slot crossed in 24h window: 13:30 NY = 17:30 UTC -> i=812
    assert tr.levels.session_open == 4812.0
    assert tr.levels.session_name == "pm"


def test_levels_set_across_gap():
    tr = TrueOpenTracker()
    t0 = datetime(2026, 6, 9, 3, 55, tzinfo=UTC)  # 23:55 NY Mon
    tr.update(mk(t0, 100.0))
    # gap straight over midnight NY: next bar 00:30 NY
    t1 = datetime(2026, 6, 9, 4, 30, tzinfo=UTC)
    tr.update(mk(t1, 200.0))
    assert tr.levels.tdo == 200.0  # first bar after the crossing


def test_bias_and_zone():
    tr = TrueOpenTracker()
    assert tr.bias(100.0) is None
    assert tr.zone(100.0) is None
    tr.levels.session_open = 100.0
    assert tr.bias(101.0) == "bullish"
    assert tr.bias(99.0) == "bearish"
    tr.levels.tdo = 100.0
    tr.levels.week_open = 100.0
    assert tr.zone(101.0) == "premium"
    assert tr.zone(99.0) == "discount"
    tr.levels.week_open = 102.0
    assert tr.zone(101.0) == "dead_zone"
