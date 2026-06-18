"""SvpEngine state-machine transitions."""

from datetime import datetime, timedelta, timezone

from pytest import approx

from orb.models import Direction, SignalKind, State
from orb.svp import SvpConfig, SvpEngine
from orb.svp.levels import Shape

BASE = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
# ticks_per_row=100 * tick_size=0.01 -> row_size 1.0; buffer 8*0.01 = 0.08
CFG = dict(min_session_bars=10, ticks_per_row=100, tick_size=0.01)


def c(minute: int, lo: float, hi: float, close: float, vol: float = 100.0):
    from orb import Candle
    return Candle(ts=BASE + timedelta(minutes=minute), open=close, high=hi, low=lo,
                  close=close, volume=vol)


def armed(**ov):
    """Engine after a warm, locked D-shape profile (POC 1998.5, VA rows 1996.5..2000.5)."""
    cfg = SvpConfig(**{**CFG, **ov})
    trs: list = []
    e = SvpEngine(cfg, on_transition=trs.append)
    bell = [
        (1995.0, 1995.4, 10), (1996.1, 1996.4, 30), (1997.1, 1997.4, 60),
        (1998.1, 1998.4, 80), (1999.1, 1999.4, 60), (2000.1, 2000.4, 30),
        (2001.1, 2001.4, 10),
    ]
    m = 0
    for lo, hi, v in bell:
        e.on_candle(c(m, lo, hi, (lo + hi) / 2, v))
        m += 1
    for _ in range(3):  # filler at the POC row -> >= min_session_bars, keeps D
        e.on_candle(c(m, 1998.1, 1998.4, 1998.25, 5))
        m += 1
    return e, trs, cfg


# --------------------------------------------------------------------------- #
# Arming
# --------------------------------------------------------------------------- #
def test_arms_and_builds_d_profile():
    e, _, _ = armed()
    assert e.state is State.RANGE_DEFINED
    assert e.profile.ready
    assert e.profile.shape() is Shape.D
    assert e.profile.poc == approx(1998.5)
    assert e.profile.vah == approx(2000.5)
    assert e.profile.val == approx(1996.5)


# --------------------------------------------------------------------------- #
# Edge Rotation entries
# --------------------------------------------------------------------------- #
def test_edge_rotation_vah_fade_short():
    e, _, _ = armed()
    vah = e.profile.vah
    sig = e.on_candle(c(20, 1999.0, 2001.0, 2000.0, vol=1))  # tag VAH, close inside
    assert sig is not None and sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.SHORT
    assert sig.reason == "edge_rot_vah_fade"
    assert e.state is State.BREAKOUT
    assert sig.stop > vah          # structural stop just beyond VAH
    assert sig.qty is None          # sizing injected downstream
    assert sig.tp is None


def test_edge_rotation_val_fade_long():
    e, _, _ = armed()
    val = e.profile.val
    sig = e.on_candle(c(20, 1995.0, 1997.5, 1997.0, vol=1))  # tag VAL, close inside
    assert sig is not None and sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.LONG
    assert sig.reason == "edge_rot_val_fade"
    assert sig.stop < val
    assert e.state is State.BREAKOUT


def test_no_entry_mid_value():
    e, _, _ = armed()
    sig = e.on_candle(c(20, 1998.0, 1999.0, 1998.5, vol=1))  # sits at POC, no edge tag
    assert sig is None
    assert e.state is State.RANGE_DEFINED


def test_short_only_blocks_vah_long_setup_path():
    e, _, _ = armed(allow_long=False)
    # VAL tag would be a long -> blocked; engine stays armed
    sig = e.on_candle(c(20, 1995.0, 1997.5, 1997.0, vol=1))
    assert sig is None
    assert e.state is State.RANGE_DEFINED


# --------------------------------------------------------------------------- #
# Trend (I-shape) suppresses mean reversion
# --------------------------------------------------------------------------- #
def test_i_shape_suppresses_edge_rotation():
    cfg = SvpConfig(**CFG)
    e = SvpEngine(cfg)
    for i in range(12):  # uniform single-row bars -> I shape
        lo = 2000.0 + i + 0.1
        e.on_candle(c(i, lo, lo + 0.3, lo + 0.15, 20))
    assert e.profile.shape() is Shape.I
    vah = e.profile.vah
    sig = e.on_candle(c(20, vah - 0.5, vah + 1.0, vah, vol=1))  # tag VAH on a trend day
    assert sig is None
    assert e.state is State.RANGE_DEFINED


# --------------------------------------------------------------------------- #
# In-position hold + force_flat sync
# --------------------------------------------------------------------------- #
def test_holds_in_position_then_force_flat():
    e, _, _ = armed()
    e.on_candle(c(20, 1999.0, 2001.0, 2000.0, vol=1))   # short entry
    assert e.state is State.BREAKOUT
    held = e.on_candle(c(21, 1999.5, 2000.5, 2000.0, vol=1))  # benign bar
    assert held is None and e.state is State.BREAKOUT
    assert e.position.bars_held == 1
    sig = e.force_flat(c(22, 2000.0, 2000.5, 2000.0).ts)
    assert sig.kind is SignalKind.EXIT and sig.reason == "broker_closed"
    assert e.position is None
    assert e.state is State.RANGE_DEFINED  # re-armed for another rotation


def test_max_trades_per_session_caps_entries():
    e, _, _ = armed(max_trades_per_session=1)
    s1 = e.on_candle(c(20, 1999.0, 2001.0, 2000.0, vol=1))  # entry 1
    assert s1.kind is SignalKind.ENTRY
    e.force_flat(c(21, 2000.0, 2000.5, 2000.0).ts)          # back to armed
    s2 = e.on_candle(c(22, 1999.0, 2001.0, 2000.0, vol=1))  # would be entry 2
    assert s2 is None
    assert e.state is State.RANGE_DEFINED


# --------------------------------------------------------------------------- #
# Session boundary + prior carryover
# --------------------------------------------------------------------------- #
def test_session_rollover_carries_prior_value_area():
    e, _, _ = armed()
    poc1, vah1, val1 = e.profile.poc, e.profile.vah, e.profile.val
    nxt = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
    from orb import Candle
    e.on_candle(Candle(ts=nxt, open=2010, high=2010.5, low=2009.5, close=2010, volume=50))
    assert e.prior is not None
    assert e.prior.session_id == "2026-06-10"
    assert e.prior.poc == approx(poc1)
    assert e.prior.vah == approx(vah1) and e.prior.val == approx(val1)
    # developing profile reset for the new session
    assert e.snapshot()["session_id"] == "2026-06-11"


def test_session_end_exits_open_position():
    e, _, _ = armed()
    e.on_candle(c(20, 1999.0, 2001.0, 2000.0, vol=1))  # short entry
    assert e.state is State.BREAKOUT
    from orb import Candle
    after = Candle(ts=BASE + timedelta(minutes=1440), open=2000, high=2000.5,
                   low=1999.5, close=2000, volume=10)
    sig = e.on_candle(after)
    assert sig is not None and sig.kind is SignalKind.EXIT
    assert sig.reason == "session_end"
    assert e.state is State.IDLE


# --------------------------------------------------------------------------- #
# LVN break (behind flag)
# --------------------------------------------------------------------------- #
def test_lvn_break_short_when_enabled():
    cfg = SvpConfig(min_session_bars=6, ticks_per_row=100, tick_size=0.01,
                    enable_edge_rotation=False, enable_lvn=True)
    e = SvpEngine(cfg)
    # bimodal profile: HVN low (rows 0,1), LVN gap (rows 2,3), HVN high (rows 4,5)
    rows = [(2000.1, 2000.4, 50), (2001.1, 2001.4, 50), (2002.1, 2002.4, 5),
            (2003.1, 2003.4, 5), (2004.1, 2004.4, 50), (2005.1, 2005.4, 50)]
    for m, (lo, hi, v) in enumerate(rows):
        e.on_candle(c(m, lo, hi, (lo + hi) / 2, v))
    lvns = e.profile.lvns()
    assert lvns, "expected at least one LVN between the two HVNs"
    lvn = lvns[0]
    # last profile close (~2005) sits above the LVN; this bar closes below it
    # -> a close-confirmed break DOWN through the unfair-price gap.
    sig = e.on_candle(c(6, lvn - 0.6, lvn - 0.2, lvn - 0.4, vol=1))
    assert sig is not None and sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.SHORT
    assert sig.reason == "lvn_break_short"
    assert sig.stop > lvn
