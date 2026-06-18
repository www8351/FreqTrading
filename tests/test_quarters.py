from datetime import datetime, timedelta, timezone

from orb.models import Candle
from orb.quarters import QuarterTracker, day_quarter, m90_quarter, quarters

UTC = timezone.utc


def mk(ts, o):
    return Candle(ts=ts, open=o, high=o + 1, low=o - 1, close=o)


def test_day_quarters_ny_anchored():
    # June: NY = UTC-4. 23:00 UTC = 19:00 NY -> Q1 Asia
    assert day_quarter(datetime(2026, 6, 10, 23, 0, tzinfo=UTC)) == "Q1"
    # 05:00 UTC = 01:00 NY -> Q2 London
    assert day_quarter(datetime(2026, 6, 10, 5, 0, tzinfo=UTC)) == "Q2"
    # 13:00 UTC = 09:00 NY -> Q3 AM
    assert day_quarter(datetime(2026, 6, 10, 13, 0, tzinfo=UTC)) == "Q3"
    # 17:00 UTC = 13:00 NY -> Q4 PM
    assert day_quarter(datetime(2026, 6, 10, 17, 0, tzinfo=UTC)) == "Q4"


def test_m90_quarters_grid():
    # 04:00 UTC = 00:00 NY -> session start -> Q1
    base = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)
    assert m90_quarter(base) == "Q1"
    assert m90_quarter(base + timedelta(minutes=90)) == "Q2"
    assert m90_quarter(base + timedelta(minutes=180)) == "Q3"
    assert m90_quarter(base + timedelta(minutes=270)) == "Q4"
    assert m90_quarter(base + timedelta(minutes=360)) == "Q1"  # next session


def test_quarters_dict():
    q = quarters(datetime(2026, 6, 10, 13, 0, tzinfo=UTC))
    # 09:00 NY: AM session (06:00) + 180min -> third 90m block
    assert q == {"day": "Q3", "m90": "Q3"}


def test_tracker_q2_open_and_value_zone():
    tr = QuarterTracker()
    # Asia bar (Q1): no day q2 open yet
    tr.update(mk(datetime(2026, 6, 9, 23, 0, tzinfo=UTC), 100.0))
    assert tr.state.day_q2_open is None
    assert tr.value_zone(100.0) is None
    # First London bar (00:00 NY = 04:00 UTC) -> day Q2 open captured
    tr.update(mk(datetime(2026, 6, 10, 4, 0, tzinfo=UTC), 105.0))
    assert tr.state.day_q2_open == 105.0
    assert tr.value_zone(110.0) == "premium"
    assert tr.value_zone(100.0) == "discount"
    # Next trading day (after 18:00 NY) resets
    tr.update(mk(datetime(2026, 6, 10, 22, 30, tzinfo=UTC), 200.0))
    assert tr.state.day_q2_open is None


def test_tracker_q2_open_gap_safe():
    tr = QuarterTracker()
    tr.update(mk(datetime(2026, 6, 9, 23, 0, tzinfo=UTC), 100.0))
    # gap straight into mid-Q2 (01:30 NY): first bar at-or-after boundary wins
    tr.update(mk(datetime(2026, 6, 10, 5, 30, tzinfo=UTC), 107.0))
    assert tr.state.day_q2_open == 107.0
