"""Filter & risk-management gate in SvpEngine: trend bias, killzone blackout,
ATR stop, and the delta-confirmation stub. The edge-rotation TRIGGER itself
(pierce VAH/VAL + close inside) is covered by test_svp_strategy.py and is NOT
touched by any of these gates — they only veto or re-stop a detected setup.
"""

from datetime import datetime, timedelta, timezone

from pytest import approx

from orb.models import Direction, SignalKind, State
from orb.svp import SvpConfig, SvpEngine
from orb.svp.levels import PriorProfile, Shape

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
CFG = dict(min_session_bars=10, ticks_per_row=100, tick_size=0.01)
# armed() builds POC 1998.5, VA 1996.5..2000.5; structural short stop = vah+0.5.


def c(minute, lo, hi, close, vol=100.0):
    from orb import Candle
    return Candle(ts=BASE + timedelta(minutes=minute), open=close, high=hi, low=lo,
                  close=close, volume=vol)


def armed(**ov):
    cfg = SvpConfig(**{**CFG, **ov})
    e = SvpEngine(cfg)
    bell = [
        (1995.0, 1995.4, 10), (1996.1, 1996.4, 30), (1997.1, 1997.4, 60),
        (1998.1, 1998.4, 80), (1999.1, 1999.4, 60), (2000.1, 2000.4, 30),
        (2001.1, 2001.4, 10),
    ]
    m = 0
    for lo, hi, v in bell:
        e.on_candle(c(m, lo, hi, (lo + hi) / 2, v))
        m += 1
    for _ in range(3):
        e.on_candle(c(m, 1998.1, 1998.4, 1998.25, 5))
        m += 1
    return e


VAH_SHORT = dict(lo=1999.0, hi=2001.0, close=2000.0)   # tag VAH, close inside
VAL_LONG = dict(lo=1995.0, hi=1997.5, close=1997.0)    # tag VAL, close inside


def short_bar(minute=20, vol=1):
    return c(minute, VAH_SHORT["lo"], VAH_SHORT["hi"], VAH_SHORT["close"], vol)


def long_bar(minute=20, vol=1):
    return c(minute, VAL_LONG["lo"], VAL_LONG["hi"], VAL_LONG["close"], vol)


# --------------------------------------------------------------------------- #
# Killzone / time-of-day blackout
# --------------------------------------------------------------------------- #
def test_block_open_min_blocks_early_entry():
    e = armed(block_open_min=25)             # session opens 00:00; entry @00:20
    assert e.on_candle(short_bar(20)) is None
    assert e.state is State.RANGE_DEFINED


def test_no_blackout_allows_entry():
    e = armed()                              # filters off
    sig = e.on_candle(short_bar(20))
    assert sig is not None and sig.kind is SignalKind.ENTRY


def test_killzone_window_excludes_entry():
    e = armed(killzones=((0, 15),))          # only 00:00-00:15 UTC allowed
    assert e.on_candle(short_bar(20)) is None        # 00:20 outside window


def test_killzone_window_includes_entry():
    e = armed(killzones=((0, 30),))          # 00:00-00:30 allowed
    sig = e.on_candle(short_bar(20))
    assert sig is not None and sig.direction is Direction.SHORT


# --------------------------------------------------------------------------- #
# ATR-based risk stop (replaces the structural shelf stop)
# --------------------------------------------------------------------------- #
def test_atr_stop_replaces_structural_when_floor_off():
    e = armed(atr_stop_mult=2.0, atr_period=3, atr_stop_floor_structural=False)
    sig = e.on_candle(short_bar(20))
    atr = e.position.atr_at_entry
    assert atr > 0
    assert sig.stop == approx(2000.0 + 2.0 * atr)    # entry + mult*ATR


def test_atr_stop_never_tighter_than_structural_by_default():
    # tiny ATR mult -> ATR stop sits inside the shelf; the structural floor wins.
    e = armed(atr_stop_mult=0.1, atr_period=3)
    sig = e.on_candle(short_bar(20))
    assert sig.stop == approx(2001.0)                # vah 2000.5 + buffer 0.5


def test_structural_stop_unchanged_when_atr_off():
    e = armed()                              # atr_stop_mult default 0
    sig = e.on_candle(short_bar(20))
    assert sig.stop == approx(2001.0)        # original structural shelf stop


# --------------------------------------------------------------------------- #
# Volume / delta confirmation stub
# --------------------------------------------------------------------------- #
def test_delta_bypassed_on_zero_volume():
    e = armed(use_delta_confirmation=True, absorb_lookback=3)
    sig = e.on_candle(short_bar(20, vol=0))  # no volume -> bypass, must still fire
    assert sig is not None and sig.kind is SignalKind.ENTRY


def test_delta_blocks_high_volume_fade():
    e = armed(use_delta_confirmation=True, absorb_lookback=3)
    # avg ~ 5 (filler vols); a high-volume fade bar is NOT exhaustion -> blocked
    assert e.on_candle(short_bar(20, vol=100)) is None


def test_delta_allows_low_volume_fade():
    e = armed(use_delta_confirmation=True, absorb_lookback=3)
    sig = e.on_candle(short_bar(20, vol=1))  # below-avg volume = exhaustion
    assert sig is not None and sig.direction is Direction.SHORT


# --------------------------------------------------------------------------- #
# Trend-bias gate (Condition A: open vs prior POC)
# --------------------------------------------------------------------------- #
def armed_with_prior(prior_poc, mode):
    cfg = SvpConfig(**{**CFG, "trend_filter_mode": mode})
    e = SvpEngine(cfg)
    # set BEFORE the first candle: the first _roll_session (None->id) won't clear it
    e._prior = PriorProfile(session_id="2026-06-09", poc=prior_poc,
                            vah=prior_poc + 2, val=prior_poc - 2, shape=Shape.D)
    bell = [
        (1995.0, 1995.4, 10), (1996.1, 1996.4, 30), (1997.1, 1997.4, 60),
        (1998.1, 1998.4, 80), (1999.1, 1999.4, 60), (2000.1, 2000.4, 30),
        (2001.1, 2001.4, 10),
    ]
    m = 0
    for lo, hi, v in bell:
        e.on_candle(c(m, lo, hi, (lo + hi) / 2, v))
        m += 1
    for _ in range(3):
        e.on_candle(c(m, 1998.1, 1998.4, 1998.25, 5))
        m += 1
    return e
    # session open price captured = first bell bar open = 1995.2


def test_bearish_bias_blocks_counter_trend_long():
    e = armed_with_prior(prior_poc=2010.0, mode="open")   # open 1995.2 << 2010 = bearish
    assert e.on_candle(long_bar(20)) is None
    assert e.state is State.RANGE_DEFINED


def test_bearish_bias_allows_with_trend_short():
    e = armed_with_prior(prior_poc=2010.0, mode="open")   # bearish
    sig = e.on_candle(short_bar(20))
    assert sig is not None and sig.direction is Direction.SHORT


def test_bullish_bias_blocks_counter_trend_short():
    e = armed_with_prior(prior_poc=1980.0, mode="open")   # open 1995.2 > 1980 = bullish
    assert e.on_candle(short_bar(20)) is None


def test_neutral_bias_blocks_everything():
    # no prior -> Condition A neutral -> "open" mode acts only on a confirmed bias
    cfg = SvpConfig(**{**CFG, "trend_filter_mode": "open"})
    e = SvpEngine(cfg)
    assert e._bias_open() is None
    assert e._trend_ok(Direction.SHORT) is False
    assert e._trend_ok(Direction.LONG) is False


def test_trend_filter_off_passes_through():
    cfg = SvpConfig(**CFG)                    # trend_filter_mode default "off"
    e = SvpEngine(cfg)
    assert e._trend_ok(Direction.LONG) is True
    assert e._trend_ok(Direction.SHORT) is True
