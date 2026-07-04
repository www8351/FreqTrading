from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from orb.babysitter import Action
from orb.models import Candle
from orb.smc.exits import LadderExitManager

LONG = 0
SHORT = 1

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def pos(ticket=1, type=LONG, volume=1.0, price_open=100.0, sl=98.0):
    return SimpleNamespace(ticket=ticket, type=type, volume=volume,
                           price_open=price_open, sl=sl)


def candle(i, lo, hi):
    mid = (lo + hi) / 2.0
    return Candle(ts=T0 + timedelta(minutes=i), open=mid, high=hi,
                  low=lo, close=mid)


def apply(p, actions):
    """Mimic the Sim: reduce volume on partials, tighten SL on updates."""
    for a in actions:
        if a.kind == "partial_close":
            p.volume = round(p.volume - a.volume, 8)
        else:
            p.sl = a.sl


def partials(actions):
    return [a for a in actions if a.kind == "partial_close"]


def sls(actions):
    return [a for a in actions if a.kind == "update_sl"]


# --------------------------------------------------------------------- #
# 1. first partial fires once
# --------------------------------------------------------------------- #
def test_first_partial_fires_once_at_5r():
    m = LadderExitManager()
    p = pos()                                    # long @100, sl 98 -> d=2
    a = m.on_bar([p], close=110.0)               # r=5
    pc = partials(a)
    assert len(pc) == 1
    assert abs(pc[0].volume - 0.40) < 1e-9
    apply(p, a)
    a2 = m.on_bar([p], close=110.0)              # same level: no repeat
    assert partials(a2) == []


# --------------------------------------------------------------------- #
# 2. second partial then final closes remainder and forgets state
# --------------------------------------------------------------------- #
def test_ladder_then_final_close():
    m = LadderExitManager()
    p = pos()
    apply(p, m.on_bar([p], close=110.0))         # r=5: 0.40 off
    a = m.on_bar([p], close=114.0)               # r=7
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    apply(p, a)
    a = m.on_bar([p], close=120.0)               # r=10: final
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    assert p.ticket not in m._trades             # state forgotten


# --------------------------------------------------------------------- #
# 3. gap straight to final: rungs in order, final closes remainder,
#    total closed == vol0 (documented deterministic behavior)
# --------------------------------------------------------------------- #
def test_gap_to_final_closes_everything_cumulatively():
    m = LadderExitManager()
    p = pos()                                    # vol 1.00
    a = m.on_bar([p], close=120.0)               # r=10 first sight
    pc = partials(a)
    assert len(pc) == 3
    vols = [x.volume for x in pc]
    assert abs(vols[0] - 0.40) < 1e-9
    assert abs(vols[1] - 0.30) < 1e-9
    assert abs(vols[2] - 0.30) < 1e-9
    assert abs(sum(vols) - 1.00) < 1e-9
    assert sls(a) == []                          # final short-circuits SL work
    assert p.ticket not in m._trades


# --------------------------------------------------------------------- #
# 4. breakeven lock persists, never widens
# --------------------------------------------------------------------- #
def test_breakeven_locks_and_persists_long():
    m = LadderExitManager()
    p = pos()                                    # long @100 sl 98
    a = m.on_bar([p], close=104.0)               # r=2: BE
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 100.0) < 1e-9
    apply(p, a)
    a = m.on_bar([p], close=103.0)               # r back to 1.5
    assert sls(a) == []                          # floor persists, no widen


# --------------------------------------------------------------------- #
# 5. below trail_start_r (and be_at_r): nothing
# --------------------------------------------------------------------- #
def test_no_trail_below_trail_start_r():
    m = LadderExitManager(trail_tf_min=1)
    for i, lo in enumerate([96.0, 95.0, 94.0, 95.0, 96.0, 97.0]):
        m.observe(candle(i, lo, lo + 1.0))       # swing low 94 confirmed
    p = pos()
    a = m.on_bar([p], close=103.8)               # r=1.9 < 2
    assert a == []


# --------------------------------------------------------------------- #
# 6. swing trail tightens, never loosens on a later lower swing
# --------------------------------------------------------------------- #
def test_swing_trail_tightens_never_loosens():
    m = LadderExitManager(partial_levels=(), final_tp_r=0.0, be_at_r=0.0,
                          trail_mode="swing", trail_buffer=0.5,
                          swing_lookback=2, trail_tf_min=1)
    lows = [104.0, 103.5, 103.0, 103.5, 104.0, 105.0]
    for i, lo in enumerate(lows):
        m.observe(candle(i, lo, lo + 1.0))       # swing low 103 confirmed
    p = pos()                                    # long @100 sl 98
    a = m.on_bar([p], close=110.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 102.5) < 1e-9   # 103 - 0.5
    apply(p, a)
    lows2 = [102.0, 101.0, 100.5, 101.0, 102.0, 103.0]
    for j, lo in enumerate(lows2):
        m.observe(candle(len(lows) + j, lo, lo + 1.0))   # lower swing 100.5
    a = m.on_bar([p], close=110.0)
    assert sls(a) == []                          # 100.0 would loosen 102.5


# --------------------------------------------------------------------- #
# 7. atr trail when ready; silent when not
# --------------------------------------------------------------------- #
def test_atr_trail_when_ready():
    m = LadderExitManager(partial_levels=(), final_tp_r=0.0, be_at_r=0.0,
                          trail_mode="atr", trail_atr_mult=2.5,
                          atr_period=2, trail_tf_min=1)
    for i in range(3):                           # 2 completed TF bars, TR=2
        m.observe(candle(i, 99.0, 101.0))        # ATR = 2.0, ready
    p = pos()
    a = m.on_bar([p], close=110.0)               # r=5 >= trail_start
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 105.0) < 1e-9   # 110 - 2.5*2


def test_atr_not_ready_no_trail():
    m = LadderExitManager(partial_levels=(), final_tp_r=0.0, be_at_r=0.0,
                          trail_mode="atr", atr_period=2, trail_tf_min=1)
    m.observe(candle(0, 99.0, 101.0))            # 0 completed -> not ready
    p = pos()
    assert m.on_bar([p], close=110.0) == []


# --------------------------------------------------------------------- #
# 8. SHORT mirror: partials, final, BE tightens DOWN
# --------------------------------------------------------------------- #
def test_short_ladder_and_final():
    m = LadderExitManager()
    p = pos(type=SHORT, price_open=100.0, sl=102.0)      # d=2
    a = m.on_bar([p], close=90.0)                # r=5
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.40) < 1e-9
    apply(p, a)
    a = m.on_bar([p], close=86.0)                # r=7
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    apply(p, a)
    a = m.on_bar([p], close=80.0)                # r=10: final remainder
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    assert p.ticket not in m._trades


def test_short_breakeven_tightens_down():
    m = LadderExitManager()
    p = pos(type=SHORT, price_open=100.0, sl=102.0)
    a = m.on_bar([p], close=96.0)                # r=2: BE
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 100.0) < 1e-9   # 100 < 102: tighter
    apply(p, a)
    a = m.on_bar([p], close=97.0)                # r=1.5: floor holds
    assert sls(a) == []


# --------------------------------------------------------------------- #
# 9. volume snapping + silent skip of unfillable rungs
# --------------------------------------------------------------------- #
def test_volume_snap_down():
    m = LadderExitManager(final_tp_r=0.0, be_at_r=0.0)
    p = pos(volume=0.05)
    a = m.on_bar([p], close=110.0)               # r=5: 0.05*0.40=0.02
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.02) < 1e-9


def test_unfillable_partial_skipped_silently():
    m = LadderExitManager(final_tp_r=0.0, be_at_r=0.0)
    p = pos(volume=0.01)                         # 0.01*0.40 snaps to 0.0
    a = m.on_bar([p], close=110.0)
    assert partials(a) == []
    a = m.on_bar([p], close=110.0)               # marked filled, stays quiet
    assert partials(a) == []


# --------------------------------------------------------------------- #
# 10. closed-ticket cleanup + fresh recapture
# --------------------------------------------------------------------- #
def test_closed_tickets_forgotten_and_recaptured():
    m = LadderExitManager()
    p = pos()
    m.on_bar([p], close=101.0)
    assert p.ticket in m._trades
    m.on_bar([], close=101.0)
    assert p.ticket not in m._trades
    p2 = pos(sl=97.0)                            # same ticket, new sl -> d=3
    m.on_bar([p2], close=101.0)
    assert abs(m._trades[p2.ticket].d - 3.0) < 1e-9


# --------------------------------------------------------------------- #
# 11. emitted objects are orb.babysitter.Action instances
# --------------------------------------------------------------------- #
def test_emits_babysitter_actions():
    m = LadderExitManager()
    p = pos()
    a = m.on_bar([p], close=110.0)
    assert a and all(isinstance(x, Action) for x in a)
