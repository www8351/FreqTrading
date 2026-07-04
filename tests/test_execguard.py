"""Tests for orb.execguard — spread gate, killzone session gate, fill assessment."""

from datetime import datetime, timedelta, timezone

import pytest

from orb.execguard import (
    FillAssessment,
    SessionGate,
    SpreadGate,
    assess_fill,
    parse_killzones,
)


# --------------------------- SpreadGate ------------------------------------

def test_spread_blocks_above_max():
    ok, spread = SpreadGate(0.4).allows(4000.0, 4000.5)
    assert ok is False
    assert spread == pytest.approx(0.5)


def test_spread_allows_at_exactly_max():
    ok, spread = SpreadGate(0.4).allows(4000.0, 4000.4)
    assert ok is True
    assert spread == pytest.approx(0.4)


def test_spread_allows_below_max():
    ok, spread = SpreadGate(0.4).allows(100.0, 100.25)
    assert ok is True
    assert spread == pytest.approx(0.25)


def test_spread_gate_validates_max():
    with pytest.raises(ValueError):
        SpreadGate(0.0)
    with pytest.raises(ValueError):
        SpreadGate(-1.0)


# ------------------------- parse_killzones ---------------------------------

def test_parse_multi_window():
    assert parse_killzones("12:00-16:00,07:30-10:00") == ((720, 960), (450, 600))


def test_parse_wrap_window_and_whitespace():
    assert parse_killzones(" 22:00-02:00 ") == ((1320, 120),)


def test_parse_empty_spec_is_no_windows():
    assert parse_killzones("") == ()


@pytest.mark.parametrize("spec", [
    "banana",           # not a window at all
    "12:00",            # missing end
    "12:60-13:00",      # minute out of range
    "24:00-01:00",      # hour out of range
    "12:00-12:00",      # zero-length window
    "12-13",            # missing minutes
])
def test_parse_rejects_garbage(spec):
    with pytest.raises(ValueError):
        parse_killzones(spec)


# --------------------------- SessionGate -----------------------------------

def _ts(h, m):
    return datetime(2026, 7, 3, h, m, tzinfo=timezone.utc)


def test_session_empty_allows_all():
    g = SessionGate(())
    assert g.allows(_ts(0, 0))
    assert g.allows(_ts(12, 34))
    assert g.allows(_ts(23, 59))


def test_session_start_inclusive_end_exclusive():
    g = SessionGate(((720, 960),))          # 12:00-16:00
    assert g.allows(_ts(12, 0))
    assert g.allows(_ts(15, 59))
    assert not g.allows(_ts(16, 0))
    assert not g.allows(_ts(11, 59))


def test_session_wraps_midnight():
    g = SessionGate(parse_killzones("22:00-02:00"))
    assert g.allows(_ts(22, 0))
    assert g.allows(_ts(23, 59))
    assert g.allows(_ts(0, 0))
    assert g.allows(_ts(1, 59))
    assert not g.allows(_ts(2, 0))
    assert not g.allows(_ts(12, 0))
    assert not g.allows(_ts(21, 59))


def test_session_converts_aware_ts_to_utc():
    g = SessionGate(((720, 960),))
    plus3 = timezone(timedelta(hours=3))
    assert g.allows(datetime(2026, 7, 3, 15, 0, tzinfo=plus3))       # 12:00 UTC
    assert not g.allows(datetime(2026, 7, 3, 12, 0, tzinfo=plus3))   # 09:00 UTC


def test_session_naive_ts_treated_as_utc():
    g = SessionGate(((720, 960),))
    assert g.allows(datetime(2026, 7, 3, 12, 30))


# --------------------------- assess_fill -----------------------------------

def test_assess_zero_slippage():
    fa = assess_fill(100.0, 100.0, 99.0, 102.0)
    assert fa.slippage == 0.0
    assert fa.risk_inflation_r == pytest.approx(0.0)
    assert fa.rr_planned == pytest.approx(2.0)
    assert fa.rr_achieved == pytest.approx(2.0)
    assert fa.breach is False
    assert fa.degraded is False


def test_assess_rr_degradation_short_side():
    # SHORT: stop ABOVE entry, tp below. Filled 0.25 lower = adverse fill:
    # stop distance widens 1.0 -> 1.25 while the tp distance shrinks.
    fa = assess_fill(100.0, 99.75, 101.0, 98.0, rr_floor=1.5)
    assert fa.slippage == pytest.approx(0.25)
    assert fa.rr_planned == pytest.approx(2.0)          # |98-100| / |100-101|
    assert fa.rr_achieved == pytest.approx(1.4)         # |98-99.75| / |99.75-101|
    assert fa.risk_inflation_r == pytest.approx(0.25)   # 0.25 slip / 1.0 planned risk
    assert fa.degraded is True
    assert fa.breach is False                           # no max_slippage set


def test_assess_breach_flag():
    fa = assess_fill(100.0, 100.25, 99.0, 102.0, max_slippage=0.1)
    assert fa.breach is True
    # breach only when slippage EXCEEDS the cap, not at exactly the cap
    ok = assess_fill(100.0, 100.1, 99.0, 102.0, max_slippage=0.1)
    assert ok.breach is False


def test_assess_rr_floor_not_degraded_at_floor():
    fa = assess_fill(100.0, 100.0, 99.0, 102.0, rr_floor=2.0)
    assert fa.degraded is False        # rr_achieved == floor -> still acceptable


def test_assess_zero_stop_dist_no_crash():
    fa = assess_fill(100.0, 100.0, 100.0, 102.0, max_slippage=0.1, rr_floor=1.5)
    assert fa.rr_planned is None
    assert fa.rr_achieved is None
    assert fa.risk_inflation_r is None
    assert fa.degraded is False
    assert fa.breach is False


@pytest.mark.parametrize("tp", [None, 0.0])
def test_assess_no_tp_rr_fields_none(tp):
    fa = assess_fill(100.0, 100.05, 99.0, tp, rr_floor=1.5)
    assert fa.rr_planned is None
    assert fa.rr_achieved is None
    assert fa.degraded is False
    assert fa.risk_inflation_r == pytest.approx(0.05)   # slippage still in R units


def test_assess_no_stop_rr_fields_none():
    # MT5 convention: sl == 0.0 means "no stop" -> no R denominator exists.
    fa = assess_fill(4182.0, 4182.05, 0.0, 4170.0)
    assert fa.slippage == pytest.approx(0.05)
    assert fa.rr_planned is None
    assert fa.rr_achieved is None
    assert fa.risk_inflation_r is None


def test_fill_assessment_frozen():
    fa = assess_fill(100.0, 100.0, 99.0, 102.0)
    assert isinstance(fa, FillAssessment)
    with pytest.raises(AttributeError):       # dataclasses.FrozenInstanceError
        fa.breach = True
