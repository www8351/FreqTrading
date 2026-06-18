"""VolumeProfile math against hand-computed fixtures."""

from datetime import datetime, timedelta, timezone

from pytest import approx

from orb import Candle
from orb.svp.levels import Shape
from orb.svp.profile import VolumeProfile

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)


def c(minute: int, lo: float, hi: float, vol: float) -> Candle:
    mid = (lo + hi) / 2
    return Candle(ts=BASE + timedelta(minutes=minute), open=mid, high=hi, low=lo,
                  close=mid, volume=vol)


def single_row(vp: VolumeProfile, vols: list[float]) -> None:
    """Feed one bar per element so element i lands entirely in row i.

    First bar low = 2000.1 -> anchor = 2000.1; bar i spans [2000.1+i, 2000.4+i],
    which is wholly inside row i (row_size 1.0).
    """
    for i, v in enumerate(vols):
        vp.update(c(i, 2000.0 + i + 0.1, 2000.0 + i + 0.4, v))


# --------------------------------------------------------------------------- #
# Even-split distribution
# --------------------------------------------------------------------------- #
def test_even_split_uniform():
    vp = VolumeProfile(row_size=0.10, min_bars=1)
    vp.update(c(0, 2000.00, 2000.25, 300))  # spans rows 0,1,2 -> 100 each
    assert vp._hist == {0: 100.0, 1: 100.0, 2: 100.0}
    assert vp.total_volume == 300.0


def test_single_row_bar():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    vp.update(c(0, 2000.1, 2000.4, 250))  # one row
    assert vp._hist == {0: 250.0}


def test_zero_volume_bar_noop():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    vp.update(c(0, 2000.0, 2000.4, 0))
    assert vp._hist == {} and vp.total_volume == 0.0
    assert vp.bars == 1
    assert vp.ready is False  # total volume is zero


def test_tpo_fallback_weights_volumeless_bars():
    vp = VolumeProfile(row_size=1.0, min_bars=1, tpo_fallback=True)
    vp.update(c(0, 2000.1, 2000.4, 0))   # single row, no volume -> 1 TPO unit
    vp.update(c(1, 2001.1, 2001.4, 0))
    assert vp._hist == {0: 1.0, 1: 1.0}
    assert vp.total_volume == 2.0 and vp.ready


# --------------------------------------------------------------------------- #
# POC
# --------------------------------------------------------------------------- #
def test_poc_argmax():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [10, 10, 50, 10])  # row 2 dominates
    assert vp.poc == approx(vp._row_price(2))


def test_poc_tie_breaks_to_lower_row():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [50, 20, 50])  # rows 0 and 2 tie at 50
    assert vp.poc == approx(vp._row_price(0))


# --------------------------------------------------------------------------- #
# Value Area
# --------------------------------------------------------------------------- #
def test_value_area_expands_both_sides():
    vp = VolumeProfile(row_size=1.0, value_area_pct=0.70, min_bars=1, va_tiebreak="up")
    single_row(vp, [5, 10, 20, 60, 20, 10, 5])  # total 130, target 91
    # POC=row3; first step ties (30 vs 30)->up adds rows4,5; next step down adds rows2,1
    assert vp.poc == approx(vp._row_price(3))
    assert vp.vah == approx(vp._row_price(5))
    assert vp.val == approx(vp._row_price(1))


def test_va_tiebreak_direction():
    def build(tb: str) -> VolumeProfile:
        vp = VolumeProfile(row_size=1.0, value_area_pct=0.70, min_bars=1, va_tiebreak=tb)
        single_row(vp, [20, 50, 20])  # symmetric -> the tie decides which edge
        return vp

    up, dn = build("up"), build("down")
    assert up.val == approx(up._row_price(1)) and up.vah == approx(up._row_price(2))
    assert dn.val == approx(dn._row_price(0)) and dn.vah == approx(dn._row_price(1))


# --------------------------------------------------------------------------- #
# HVN / LVN
# --------------------------------------------------------------------------- #
def test_nodes_bimodal_two_hvn_one_lvn():
    vp = VolumeProfile(row_size=1.0, min_bars=1, hvn_frac=0.70, lvn_frac=0.30)
    single_row(vp, [10, 50, 10, 5, 10, 50, 10])
    assert vp.hvns() == [vp._row_price(1), vp._row_price(5)]
    assert vp.lvns() == [vp._row_price(3)]


def test_flat_profile_has_no_lvn():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [20, 20, 20, 20, 20])
    assert vp.lvns() == []


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
def test_ready_requires_min_bars():
    vp = VolumeProfile(row_size=1.0, min_bars=3)
    vp.update(c(0, 2000.0, 2000.4, 10))
    assert not vp.ready and vp.poc is None
    vp.update(c(1, 2001.1, 2001.4, 10))
    assert not vp.ready
    vp.update(c(2, 2002.1, 2002.4, 10))
    assert vp.ready and vp.poc is not None


def test_reset_clears_state():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [10, 20, 30])
    vp.reset()
    assert vp._hist == {} and vp.total_volume == 0.0 and vp.bars == 0
    assert vp._anchor is None and not vp.ready


# --------------------------------------------------------------------------- #
# Shape morphology
# --------------------------------------------------------------------------- #
def test_shape_d_balanced():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [5, 20, 60, 20, 5])
    assert vp.shape() is Shape.D


def test_shape_p_bullish():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [5, 10, 20, 30, 80])
    assert vp.shape() is Shape.P


def test_shape_b_bearish():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [80, 30, 20, 10, 5])
    assert vp.shape() is Shape.b


def test_shape_double_distribution():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [10, 50, 10, 5, 10, 50, 10])
    assert vp.shape() is Shape.B


def test_shape_i_trend():
    vp = VolumeProfile(row_size=1.0, min_bars=1)
    single_row(vp, [20, 20, 20, 20, 20])
    assert vp.shape() is Shape.I
