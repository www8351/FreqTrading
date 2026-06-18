from datetime import datetime, timezone

import pytest

from orb import (
    Candle,
    CandleError,
    Direction,
    OrbEngine,
    OutOfOrderError,
    SignalKind,
    State,
)

from ._util import make_cfg, mk


def armed_engine(**ov):
    """Engine after a locked 3-bar opening range (H=2001, L=1999), warm + armed."""
    cfg = make_cfg(**ov)
    trs = []
    e = OrbEngine(cfg, on_transition=trs.append)
    for c in (
        mk(0, 2000, 2000.5, 1999.5, 2000),
        mk(1, 2000, 2001.0, 1999.0, 2000),
        mk(2, 2000, 2000.5, 1999.5, 2000),
    ):
        e.on_candle(c)
    return e, trs


def events(trs):
    return [t.event for t in trs]


# --------------------------------------------------------------------------- #
# Range establishment
# --------------------------------------------------------------------------- #
def test_idle_to_range_defined_and_lock():
    e, trs = armed_engine()
    evs = events(trs)
    assert evs[0] == "RANGE_OPEN"
    assert "RANGE_LOCK" in evs
    assert e.state is State.RANGE_DEFINED
    snap = e.snapshot()
    assert snap["range_high"] == 2001.0 and snap["range_low"] == 1999.0
    assert snap["range_locked"] is True


# --------------------------------------------------------------------------- #
# Breakout + momentum
# --------------------------------------------------------------------------- #
def test_breakout_long_passes():
    e, _ = armed_engine()
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.LONG
    assert e.state is State.BREAKOUT
    assert e.position.stop < 2005  # ATR stop below entry


def test_breakout_short_passes():
    e, _ = armed_engine()
    sig = e.on_candle(mk(3, 1999, 1999.5, 1994, 1995))
    assert sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.SHORT
    assert e.state is State.BREAKOUT
    assert e.position.stop > 1995


def test_roc_fail_rejects():
    e, _ = armed_engine(roc_min=1.0)  # require >=1% ROC; breakout only ~0.25%
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert sig.kind is SignalKind.REJECT
    assert sig.reason == "momentum_fail:roc"
    assert e.state is State.RANGE_DEFINED


def test_rvol_fail_rejects():
    e, _ = armed_engine(use_rvol=True, rvol_period=2, rvol_min=5.0)
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005, v=100.0))  # rvol == 1 < 5
    assert sig.kind is SignalKind.REJECT
    assert sig.reason == "momentum_fail:rvol"


def test_both_gates_off_pure_breakout():
    e, _ = armed_engine(use_roc=False, use_rvol=False)
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert sig.kind is SignalKind.ENTRY
    assert sig.reason == "breakout_long"
    assert sig.roc is None


def test_direction_disabled_no_entry():
    e, _ = armed_engine(allow_long=False)  # short-only
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert sig is None
    assert e.state is State.RANGE_DEFINED


# --------------------------------------------------------------------------- #
# Trailing + exits
# --------------------------------------------------------------------------- #
def test_ratchet_monotonic_and_trail_exit():
    e, _ = armed_engine(atr_mult=1.0)
    e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))  # entry long
    stops = []
    for c in (mk(4, 2005, 2012, 2004, 2011), mk(5, 2011, 2020, 2010, 2019)):
        e.on_candle(c)
        stops.append(e.position.stop)
    assert stops == sorted(stops)        # never loosens
    stop = e.position.stop
    assert stop > 2001                   # trailed above the range high
    # pullback closing at the stop, still above range high -> trail exit (not reentry)
    sig = e.on_candle(mk(6, stop + 0.5, stop + 0.6, stop - 0.5, stop))
    assert sig.kind is SignalKind.EXIT
    assert sig.reason == "trail_stop"
    assert e.state is State.IDLE


def test_range_reentry_preempts_trail():
    e, _ = armed_engine()
    e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))  # entry long
    sig = e.on_candle(mk(4, 2000, 2000.5, 1997, 1998))  # closes back inside range
    assert sig.kind is SignalKind.EXIT
    assert sig.reason == "range_reentry"
    assert e.state is State.IDLE


def test_session_end_preempts_all():
    e, _ = armed_engine()
    e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))  # entry long
    sig = e.on_candle(mk(60, 2005, 2006, 2004, 2005))  # minute 60 == session end (AFTER)
    assert sig.kind is SignalKind.EXIT
    assert sig.reason == "session_end"
    assert e.state is State.IDLE


# --------------------------------------------------------------------------- #
# Session trade policy
# --------------------------------------------------------------------------- #
def test_one_trade_per_session_blocks_second():
    e, _ = armed_engine()  # default one_trade_per_session=True
    e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    e.on_candle(mk(4, 2000, 2000.5, 1997, 1998))  # reentry exit -> IDLE
    assert e.state is State.IDLE
    sig = e.on_candle(mk(5, 2002, 2007, 2001.5, 2006))  # strong breakout again
    assert sig is None  # no re-entry; stays IDLE for the rest of the session
    assert e.state is State.IDLE


def test_rearm_keep_allows_immediate_second_entry():
    e, _ = armed_engine(one_trade_per_session=False, rearm_after_exit=True,
                        rearm_range="keep")
    s1 = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    e.on_candle(mk(4, 2000, 2000.5, 1997, 1998))  # reentry exit -> RANGE_DEFINED
    assert e.state is State.RANGE_DEFINED
    s2 = e.on_candle(mk(5, 2002, 2007, 2001.5, 2006))
    assert s1.kind is SignalKind.ENTRY and s2.kind is SignalKind.ENTRY


def test_rearm_rebuild_blocks_instant_reentry_and_builds_fresh_range():
    """Default rearm: after an exit the old range is discarded; the next
    range_minutes bars form a new range, and only a breakout of THAT enters."""
    e, _ = armed_engine(one_trade_per_session=False, rearm_after_exit=True)
    s1 = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert s1.kind is SignalKind.ENTRY
    e.on_candle(mk(4, 2000, 2000.5, 1997, 1998))  # reentry exit -> rebuild mode
    assert e.state is State.RANGE_DEFINED

    # next 3 bars build the NEW range (1996..2000); no entries despite moves
    assert e.on_candle(mk(5, 1998, 2000.0, 1996.0, 1997)) is None
    assert e.on_candle(mk(6, 1997, 1999.0, 1996.5, 1998)) is None
    assert e.on_candle(mk(7, 1998, 1999.5, 1996.5, 1999)) is None
    snap = e.snapshot()
    assert snap["range_locked"] and snap["range_high"] == 2000.0
    assert snap["range_low"] == 1996.0

    # breakout of the NEW range enters
    s2 = e.on_candle(mk(8, 2000, 2004.0, 1999.5, 2003))
    assert s2 is not None and s2.kind is SignalKind.ENTRY
    assert s2.direction is Direction.LONG


def test_new_session_resets():
    e, trs = armed_engine()
    next_day = Candle(
        ts=datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc),
        open=3000, high=3000.5, low=2999.5, close=3000,
    )
    e.on_candle(next_day)
    assert "SESSION_RESET" in [t.event for t in trs]
    assert e.snapshot()["session_id"] == "2026-06-11"
    assert e.state is State.RANGE_DEFINED  # opened a fresh range


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #
def test_zero_width_range_flagged():
    cfg = make_cfg()
    trs = []
    e = OrbEngine(cfg, on_transition=trs.append)
    for m in range(3):
        e.on_candle(mk(m, 2000, 2000, 2000, 2000))
    lock = [t for t in trs if t.event == "RANGE_LOCK"][0]
    assert "zero_width_range" in lock.detail


def test_gap_hold_continues():
    e, _ = armed_engine()  # default on_gap=hold, max_gap_bars=3
    sig = e.on_candle(mk(4, 2000, 2000.5, 1999.5, 2000))  # 1-bar gap (skipped min 3)
    assert e.state is State.RANGE_DEFINED
    assert sig is None


def test_gap_reset_rebuilds_range_in_session():
    e, trs = armed_engine(on_gap="reset")
    e.on_candle(mk(4, 2000, 2000.5, 1999.5, 2000))  # any gap triggers reset
    assert e.state is State.RANGE_DEFINED  # in-session: rebuild, don't strand
    assert "GAP_RESET" in [t.event for t in trs]


def test_gap_invalidate_exits_position():
    e, _ = armed_engine(max_gap_bars=3)
    e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))  # entry
    sig = e.on_candle(mk(10, 2005, 2006, 2004, 2005))  # 6-bar gap > max -> invalidate
    assert sig.kind is SignalKind.EXIT
    assert sig.reason == "gap_invalidate"
    assert e.state is State.RANGE_DEFINED  # in-session: rebuilding fresh range


def test_out_of_order_strict_raises():
    e, _ = armed_engine()  # strict_monotonic default True
    with pytest.raises(OutOfOrderError):
        e.on_candle(mk(1, 2000, 2001, 1999, 2000))


def test_out_of_order_non_strict_drops():
    e, _ = armed_engine(strict_monotonic=False)
    assert e.on_candle(mk(1, 2000, 2001, 1999, 2000)) is None


def test_malformed_candle_raises():
    e, _ = armed_engine()
    with pytest.raises(CandleError):
        e.on_candle(Candle(ts=mk(3, 0, 0, 0, 0).ts, open=2000, high=1999, low=2001, close=2000))


def test_same_bar_breakout_no_double_fire():
    e, _ = armed_engine()
    sig = e.on_candle(mk(3, 2002, 2006, 2001.5, 2005))
    assert sig.kind is SignalKind.ENTRY  # exactly one signal; no same-bar exit
    assert e.state is State.BREAKOUT


# --------------------------------------------------------------------------- #
# Position qty + fixed take-profit at RRR
# --------------------------------------------------------------------------- #
def test_short_entry_carries_qty_and_tp_at_1_to_3_rrr():
    import math

    e, _ = armed_engine(qty=0.01, tp_rrr=3.0)
    entry = e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))  # short breakout
    assert entry is not None and entry.kind is SignalKind.ENTRY
    assert entry.direction is Direction.SHORT
    assert entry.qty == 0.01
    assert entry.tp is not None and entry.tp < entry.price
    risk = entry.stop - entry.price          # SL distance (short)
    assert risk > 0
    assert math.isclose(entry.price - entry.tp, 3.0 * risk)  # 1:3 RRR

    # plunge straight through the TP level -> TAKE_PROFIT exit
    exit_sig = e.on_candle(mk(4, 1990, 1991.0, 1955.0, 1956))
    assert exit_sig is not None and exit_sig.kind is SignalKind.EXIT
    assert exit_sig.reason == "take_profit"
    assert exit_sig.qty == 0.01
    assert e.state is State.IDLE


def test_no_tp_when_tp_rrr_unset():
    e, _ = armed_engine()
    entry = e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))
    assert entry.kind is SignalKind.ENTRY
    assert entry.tp is None and entry.qty is None


def test_partial_tp_closes_fraction_and_lets_rest_run():
    """tp_close_frac=0.7: at TP, EXIT signal for 70% of qty; position stays
    open (BREAKOUT) with no further TP; trail manages the remainder."""
    e, _ = armed_engine(qty=0.05, tp_rrr=2.0, tp_close_frac=0.7)
    entry = e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))  # short breakout
    assert entry.kind is SignalKind.ENTRY
    tp = entry.tp

    # plunge through TP -> partial exit, position still open
    sig = e.on_candle(mk(4, 1990, 1991.0, 1955.0, 1956))
    assert sig is not None and sig.kind is SignalKind.EXIT
    assert sig.reason == "take_profit_partial"
    assert abs(sig.qty - 0.035) < 1e-9  # 70% of 0.05
    assert e.state is State.BREAKOUT
    assert e.position is not None and e.position.tp is None  # TP consumed

    # remainder exits later (price snaps back above the range low -> reentry)
    sig2 = e.on_candle(mk(5, 1956, 2003.0, 1955.0, 2002))
    assert sig2 is not None and sig2.kind is SignalKind.EXIT
    assert sig2.reason in ("trail_stop", "range_reentry")
    assert e.position is None


def test_full_tp_unchanged_when_frac_1():
    e, _ = armed_engine(qty=0.05, tp_rrr=2.0)  # tp_close_frac default 1.0
    e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))
    sig = e.on_candle(mk(4, 1990, 1991.0, 1955.0, 1956))
    assert sig.reason == "take_profit"
    assert e.position is None


def test_stop_max_dist_caps_atr_stop():
    """stop_max_dist=2.0 (20 gold pips): SL distance capped, TP scales off
    the capped risk."""
    import math
    e, _ = armed_engine(qty=0.05, tp_rrr=2.0, stop_max_dist=2.0)
    entry = e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))  # short, ATR stop ~5.8
    assert entry.kind is SignalKind.ENTRY
    assert math.isclose(entry.stop - entry.price, 2.0)        # capped at 20 pips
    assert math.isclose(entry.price - entry.tp, 4.0)          # 2 x capped risk


def test_force_flat_syncs_engine_after_server_side_close():
    """Broker SL/TP filled server-side: force_flat clears the ghost position,
    emits a broker_closed EXIT, and rearm logic applies."""
    e, trs = armed_engine(one_trade_per_session=False, rearm_after_exit=True,
                          qty=0.05)
    e.on_candle(mk(3, 1999, 1999.5, 1994.0, 1995))  # short entry
    assert e.state is State.BREAKOUT
    sig = e.force_flat(mk(4, 1996, 1997.0, 1995.0, 1996).ts)
    assert sig.kind is SignalKind.EXIT
    assert sig.reason == "broker_closed"
    assert e.position is None
    assert e.state is State.RANGE_DEFINED  # rearmed (rebuild)
    assert "EXIT_BROKER" in events(trs)


def test_force_flat_noop_when_no_position():
    e, _ = armed_engine()
    assert e.force_flat(mk(3, 2000, 2000.5, 1999.5, 2000).ts) is None


def test_mid_session_gap_rebuilds_range_instead_of_idle():
    """Broker maintenance hole mid-session: engine must rebuild a fresh range
    and keep trading, not strand in IDLE until tomorrow."""
    e, trs = armed_engine(max_gap_bars=3)
    # 10-minute hole (gap > max_gap_bars) inside the session
    assert e.on_candle(mk(15, 2000, 2001.0, 1999.0, 2000)) is None
    assert e.state is State.RANGE_DEFINED  # rebuilding, not IDLE
    # next 2 bars complete the fresh 3-bar range
    e.on_candle(mk(16, 2000, 2002.0, 1999.5, 2001))
    e.on_candle(mk(17, 2001, 2002.5, 2000.0, 2002))
    snap = e.snapshot()
    assert snap["range_locked"] and snap["range_high"] == 2002.5
    # indicators were rebuilt -> warmup blocks entry, but state machine armed
    assert any("rebuild_range" in t.detail for t in trs if t.event == "GAP_RESET")
