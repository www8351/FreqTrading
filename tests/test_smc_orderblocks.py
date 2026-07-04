"""OrderBlockTracker (SMC displacement order blocks) unit tests."""

from datetime import datetime, timedelta, timezone

import pytest

from orb.models import Candle, Direction
from orb.smc.orderblocks import OrderBlock, OrderBlockTracker
from orb.smc.structure import StructureEvent

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def mk(i: int, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(ts=T0 + timedelta(minutes=i), open=o, high=h, low=lo, close=c)


def ev(i: int, d: Direction, kind: str = "BOS") -> StructureEvent:
    return StructureEvent(T0 + timedelta(minutes=i), kind, d, 100.0)


# atr_period=3 in tests: 3 constant-range warmup bars seed ATR at exactly 1.0.
WARM = [(100.0, 101.0, 100.0, 100.5)] * 3        # bullish, range 1.0
BEAR = (100.6, 100.8, 99.8, 100.0)               # opposite candle -> zone 99.8..100.8
# body 1.8 / range 1.95 = 0.923 >= 0.5; range 1.95 >= 1.2 * ATR(1.0) -> displacement
DISP = (100.0, 101.9, 99.95, 101.8)
# body 0.25 / range 1.0 -> not displacement; low 101.4 > zone top, close > zone bottom
HIGH = (101.9, 102.4, 101.4, 102.15)
DIP = (101.5, 101.6, 100.5, 101.2)               # low 100.5 trades into the zone
KILL = (100.5, 100.6, 99.3, 99.5)                # close 99.5 < zone bottom 99.8

ZONE_TOP, ZONE_BOT = 100.8, 99.8
BEAR_I, DISP_I = 3, 4                            # bar indices after 3 warmup bars


def feed(trk: OrderBlockTracker, bars, start: int = 0, events: dict | None = None):
    for i, (o, h, lo, c) in enumerate(bars, start):
        trk.update(mk(i, o, h, lo, c), (events or {}).get(i))


def make_candidate(**kw) -> OrderBlockTracker:
    trk = OrderBlockTracker(atr_period=3, **kw)
    feed(trk, WARM + [BEAR, DISP])
    return trk


def make_active(**kw) -> OrderBlockTracker:
    trk = make_candidate(**kw)
    feed(trk, [HIGH], start=5, events={5: ev(5, Direction.LONG)})
    return trk


# --------------------------------------------------------------------------- #
# 1. candidate detection
# --------------------------------------------------------------------------- #
def test_candidate_zone_is_last_opposite_candle_full_range():
    trk = make_candidate()
    assert len(trk._pending[Direction.LONG]) == 1
    ob = trk._pending[Direction.LONG][-1]
    assert ob.direction is Direction.LONG
    assert ob.top == ZONE_TOP
    assert ob.bottom == ZONE_BOT
    assert ob.ts == T0 + timedelta(minutes=BEAR_I)
    assert ob.mitigated is False
    # candidate is not active yet: no POI
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None


# --------------------------------------------------------------------------- #
# 2. promotion requires a same-direction StructureEvent inside the window
# --------------------------------------------------------------------------- #
def test_structure_event_within_window_activates_block():
    trk = make_active()
    assert len(trk._active[Direction.LONG]) == 1
    assert len(trk._pending[Direction.LONG]) == 0
    ob = trk.poi_at(100.0, 100.5, Direction.LONG)
    assert isinstance(ob, OrderBlock)
    assert (ob.bottom, ob.top) == (ZONE_BOT, ZONE_TOP)


def test_no_event_never_activates_and_candidate_expires():
    trk = make_candidate()                        # confirm_bars default 10
    feed(trk, [HIGH] * 12, start=5)               # 12 quiet bars, no event
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None
    assert len(trk._active[Direction.LONG]) == 0
    assert len(trk._pending[Direction.LONG]) == 0


def test_event_after_confirm_window_does_not_activate():
    trk = make_candidate(confirm_bars=2)
    feed(trk, [HIGH] * 3, start=5,
         events={7: ev(7, Direction.LONG)})       # event 3 bars after candidate
    assert len(trk._active[Direction.LONG]) == 0
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None


def test_opposite_direction_event_does_not_activate():
    trk = make_candidate()
    feed(trk, [HIGH], start=5, events={5: ev(5, Direction.SHORT)})
    assert len(trk._active[Direction.LONG]) == 0


# --------------------------------------------------------------------------- #
# 3. poi_at consumes: first call returns, second call None
# --------------------------------------------------------------------------- #
def test_poi_at_returns_once_then_none():
    trk = make_active()
    assert trk.poi_at(102.0, 103.0, Direction.LONG) is None     # no overlap
    assert trk.poi_at(100.0, 100.5, Direction.SHORT) is None    # wrong direction
    ob = trk.poi_at(100.0, 100.5, Direction.LONG)
    assert ob is not None and ob.mitigated is True
    assert trk.poi_at(100.0, 100.5, Direction.LONG) is None     # consumed


# --------------------------------------------------------------------------- #
# 4. price trading into the zone mitigates
# --------------------------------------------------------------------------- #
def test_bar_touch_mitigates_block():
    trk = make_active()
    feed(trk, [DIP], start=6)                     # low 100.5 <= zone top
    assert len(trk._active[Direction.LONG]) == 1
    assert trk._active[Direction.LONG][0].mitigated is True
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None


def test_block_does_not_self_mitigate_on_activation_bars():
    # displacement bar low 99.95 pokes the zone, HIGH activation bar does not;
    # neither may mitigate: the block must still be a live POI.
    trk = make_active()
    assert trk._active[Direction.LONG][0].mitigated is False


# --------------------------------------------------------------------------- #
# 5. close beyond far edge invalidates (removes)
# --------------------------------------------------------------------------- #
def test_close_below_bottom_removes_long_block():
    trk = make_active()
    feed(trk, [KILL], start=6)                    # close 99.5 < bottom 99.8
    assert len(trk._active[Direction.LONG]) == 0
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None


# --------------------------------------------------------------------------- #
# 6. expiry
# --------------------------------------------------------------------------- #
def test_block_expires_after_expiry_bars():
    trk = make_active(expiry_bars=8)              # born_bar = 5 (displacement bar)
    feed(trk, [HIGH] * 6, start=6)                # bar counter reaches 12: alive
    assert len(trk._active[Direction.LONG]) == 1
    feed(trk, [HIGH], start=12)                   # bar counter 13: 8 bars elapsed
    assert len(trk._active[Direction.LONG]) == 0
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None


# --------------------------------------------------------------------------- #
# 7. registry cap
# --------------------------------------------------------------------------- #
def test_active_registry_capped_at_max_blocks():
    trk = OrderBlockTracker(atr_period=3, disp_atr_mult=0.1, max_blocks=2)
    feed(trk, WARM)
    for k in range(3):                            # 3 confirmed blocks
        i = 3 + 2 * k
        feed(trk, [BEAR, DISP], start=i,
             events={i + 1: ev(i + 1, Direction.LONG)})
    reg = trk._active[Direction.LONG]
    assert reg.maxlen == 2
    assert len(reg) == 2
    # oldest dropped: survivors are the 2nd and 3rd opposite candles
    assert [ob.ts for ob in reg] == [T0 + timedelta(minutes=5),
                                     T0 + timedelta(minutes=7)]


# --------------------------------------------------------------------------- #
# 8. displacement gating
# --------------------------------------------------------------------------- #
def test_no_displacement_before_atr_ready():
    trk = OrderBlockTracker(atr_period=3)
    feed(trk, WARM[:1] + [BEAR, DISP])            # only 2 bars before DISP
    assert len(trk._pending[Direction.LONG]) == 0
    assert len(trk._active[Direction.LONG]) == 0


def test_zero_range_bar_is_not_displacement():
    trk = OrderBlockTracker(atr_period=3)
    feed(trk, WARM + [BEAR, (100.0, 100.0, 100.0, 100.0)])
    assert len(trk._pending[Direction.LONG]) == 0
    assert len(trk._pending[Direction.SHORT]) == 0


def test_small_body_bar_is_not_displacement():
    trk = OrderBlockTracker(atr_period=3)
    feed(trk, WARM + [BEAR, (100.8, 102.4, 100.4, 101.2)])  # body 0.4 / range 2.0
    assert len(trk._pending[Direction.LONG]) == 0


# --------------------------------------------------------------------------- #
# short-side mirror
# --------------------------------------------------------------------------- #
def test_short_block_mirror():
    trk = OrderBlockTracker(atr_period=3)
    warm = [(100.0, 101.0, 100.0, 100.5)] * 3
    bull = (100.2, 101.2, 100.0, 101.0)                       # opposite (bullish)
    disp = (101.0, 101.05, 99.1, 99.2)                        # bearish displacement
    feed(trk, warm + [bull, disp], events={4: ev(4, Direction.SHORT)})
    reg = trk._active[Direction.SHORT]
    assert len(reg) == 1
    assert (reg[0].bottom, reg[0].top) == (100.0, 101.2)
    ob = trk.poi_at(100.5, 100.9, Direction.SHORT)
    assert ob is reg[0] and ob.mitigated is True


# --------------------------------------------------------------------------- #
# reset / validation
# --------------------------------------------------------------------------- #
def test_reset_clears_everything():
    trk = make_active()
    trk.reset()
    assert trk.poi_at(ZONE_BOT, ZONE_TOP, Direction.LONG) is None
    assert len(trk._active[Direction.LONG]) == 0
    assert len(trk._pending[Direction.LONG]) == 0
    # ATR must be cold again: an immediate big bar is not displacement
    feed(trk, [BEAR, DISP])
    assert len(trk._pending[Direction.LONG]) == 0


def test_constructor_validation():
    with pytest.raises(ValueError):
        OrderBlockTracker(max_blocks=0)
    with pytest.raises(ValueError):
        OrderBlockTracker(disp_body_frac=0.0)
    with pytest.raises(ValueError):
        OrderBlockTracker(disp_atr_mult=0.0)
    with pytest.raises(ValueError):
        OrderBlockTracker(confirm_bars=-1)
    with pytest.raises(ValueError):
        OrderBlockTracker(expiry_bars=0)
