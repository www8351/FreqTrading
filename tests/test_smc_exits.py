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


def candle(i, lo, hi, close=None):
    mid = (lo + hi) / 2.0 if close is None else close
    return Candle(ts=T0 + timedelta(minutes=i), open=mid, high=hi,
                  low=lo, close=mid if close is None else close)


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


def bar(close, i=0, lo=None, hi=None):
    """A flat candle at ``close`` (partials/final are close-only checks)."""
    lo = close - 0.5 if lo is None else lo
    hi = close + 0.5 if hi is None else hi
    return candle(i, lo, hi, close=close)


# --------------------------------------------------------------------- #
# 1. first partial fires once
# --------------------------------------------------------------------- #
def test_first_partial_fires_once_at_5r():
    m = LadderExitManager()
    p = pos()                                    # long @100, sl 98 -> d=2
    a = m.on_bar([p], bar(110.0, 0))             # r=5
    pc = partials(a)
    assert len(pc) == 1
    assert abs(pc[0].volume - 0.40) < 1e-9
    apply(p, a)
    a2 = m.on_bar([p], bar(110.0, 1))            # same level: no repeat
    assert partials(a2) == []


# --------------------------------------------------------------------- #
# 2. second partial then final closes remainder and forgets state
# --------------------------------------------------------------------- #
def test_ladder_then_final_close():
    m = LadderExitManager()
    p = pos()
    apply(p, m.on_bar([p], bar(110.0, 0)))       # r=5: 0.40 off
    a = m.on_bar([p], bar(114.0, 1))             # r=7
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    apply(p, a)
    a = m.on_bar([p], bar(120.0, 2))             # r=10: final
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
    a = m.on_bar([p], bar(120.0, 0))             # r=10 first sight
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
# 4. stage1 (BE + costs) fires once candle N confirms >= stage1_at_r,
#    persists, never widens
# --------------------------------------------------------------------- #
def test_stage1_locks_and_persists_long():
    m = LadderExitManager(spread=0.10, comm_per_lot=7.0, value_per_move=100.0)
    p = pos()                                    # long @100 sl 98 -> d=2
    # candle 0 (N) closes at r=1 (entry+2); no candidate yet -> no SL action
    a = m.on_bar([p], bar(102.0, 0))
    assert sls(a) == []
    # candle 1 (X): candidate N=candle0 confirms r_n=1 >= stage1_at_r(1.0)
    a = m.on_bar([p], bar(103.0, 1))
    s = sls(a)
    assert len(s) == 1
    expected = 100.0 + 0.10 + 7.0 / 100.0        # entry + spread + comm/value
    assert abs(s[0].sl - expected) < 1e-9
    apply(p, a)
    # a later bar drifting back down must not widen the stage1 floor
    a = m.on_bar([p], bar(101.0, 2))
    assert sls(a) == []


def test_stage1_skipped_silently_when_invalid_vs_price():
    """Price collapses back below the computed stage1 level: skip, no mark."""
    m = LadderExitManager(spread=0.10, comm_per_lot=7.0, value_per_move=100.0)
    p = pos()
    m.on_bar([p], bar(102.0, 0))                  # seed candidate N (r=1)
    # X's close collapses to just above entry, below the stage1 level
    a = m.on_bar([p], bar(100.05, 1))
    assert sls(a) == []
    st = m._trades[p.ticket]
    assert st.stage1_done is False                 # not marked: retry later
    # the collapse bar is now the candidate (r too low): still nothing
    a = m.on_bar([p], bar(103.0, 2))
    assert sls(a) == []
    # next candidate (r=1.5, close=103.0) confirms on the following bar
    a = m.on_bar([p], bar(103.5, 3))
    assert len(sls(a)) == 1


# --------------------------------------------------------------------- #
# 5. below stage1_at_r: nothing
# --------------------------------------------------------------------- #
def test_no_stage_action_below_stage1_at_r():
    m = LadderExitManager()
    p = pos()                                    # d=2
    a = m.on_bar([p], bar(101.5, 0))             # r=0.75 < 1.0
    a = m.on_bar([p], bar(101.5, 1))
    assert a == []


# --------------------------------------------------------------------- #
# 6. stage2 (final profit lock) computed from candle N's low, floored,
#    freezes the SL forever after
# --------------------------------------------------------------------- #
def test_stage2_locks_from_candle_n_low_and_freezes():
    m = LadderExitManager(stage2_buffer=0.5, stage2_min_lock_r=1.0)
    p = pos()                                    # long @100 sl 98 -> d=2
    # candle 0 (N): low=103.0, closes at r=2 (entry+4=104)
    m.on_bar([p], bar(104.0, 0, lo=103.0, hi=104.5))
    # candle 1 (X): confirms N -> stage2 SL = 103.0 - 0.5 = 102.5
    a = m.on_bar([p], bar(104.2, 1))
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 102.5) < 1e-9
    apply(p, a)
    st = m._trades[p.ticket]
    assert st.stage1_done is True and st.stage2_done is True
    # frozen: even a strongly favorable later candle must not move it again
    m.on_bar([p], bar(110.0, 2, lo=108.0, hi=110.5))
    a = m.on_bar([p], bar(112.0, 3))
    assert sls(a) == []


def test_stage2_floored_to_min_lock_r():
    """Candle N's low sits BELOW entry+min_lock*d: SL floors there instead."""
    m = LadderExitManager(stage2_buffer=0.5, stage2_min_lock_r=1.0)
    p = pos()                                    # entry 100, d=2 -> floor=102.0
    # candle 0 (N): low=100.2 (low-buffer=99.7 < floor 102.0), close r=2
    m.on_bar([p], bar(104.0, 0, lo=100.2, hi=104.5))
    a = m.on_bar([p], bar(104.2, 1))
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 102.0) < 1e-9   # floored, not 99.7


def test_stage2_can_fire_directly_on_a_gap_without_stage1_modify():
    """A gap straight past both thresholds on candle N: one SL update only,
    both flags marked, never two modifications in the same bar."""
    m = LadderExitManager()
    p = pos()                                    # d=2
    m.on_bar([p], bar(105.0, 0, lo=104.0, hi=105.5))   # N: r=2.5 (gap past both)
    a = m.on_bar([p], bar(105.2, 1))
    assert len(sls(a)) == 1
    st = m._trades[p.ticket]
    assert st.stage1_done is True and st.stage2_done is True


# --------------------------------------------------------------------- #
# 7. SHORT mirror
# --------------------------------------------------------------------- #
def test_short_ladder_and_final():
    m = LadderExitManager()
    p = pos(type=SHORT, price_open=100.0, sl=102.0)      # d=2
    a = m.on_bar([p], bar(90.0, 0))               # r=5
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.40) < 1e-9
    apply(p, a)
    a = m.on_bar([p], bar(86.0, 1))               # r=7
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    apply(p, a)
    a = m.on_bar([p], bar(80.0, 2))               # r=10: final remainder
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.30) < 1e-9
    assert p.ticket not in m._trades


def test_short_stage1_tightens_down():
    m = LadderExitManager(spread=0.10, comm_per_lot=7.0, value_per_move=100.0)
    p = pos(type=SHORT, price_open=100.0, sl=102.0)      # d=2
    m.on_bar([p], bar(98.0, 0))                   # N: r=1
    a = m.on_bar([p], bar(97.0, 1))                # X confirms
    s = sls(a)
    assert len(s) == 1
    expected = 100.0 - 0.10 - 7.0 / 100.0
    assert abs(s[0].sl - expected) < 1e-9
    apply(p, a)
    a = m.on_bar([p], bar(99.0, 2))                # drifts back: floor holds
    assert sls(a) == []


def test_short_stage2_locks_from_candle_n_high():
    m = LadderExitManager(stage2_buffer=0.5, stage2_min_lock_r=1.0)
    p = pos(type=SHORT, price_open=100.0, sl=102.0)      # d=2
    m.on_bar([p], bar(96.0, 0, lo=95.5, hi=97.0))  # N: r=2, high=97.0
    a = m.on_bar([p], bar(95.8, 1))                 # X confirms
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 97.5) < 1e-9    # 97.0 + 0.5


# --------------------------------------------------------------------- #
# 8. volume snapping + silent skip of unfillable rungs
# --------------------------------------------------------------------- #
def test_volume_snap_down():
    m = LadderExitManager(final_tp_r=0.0)
    p = pos(volume=0.05)
    a = m.on_bar([p], bar(110.0, 0))              # r=5: 0.05*0.40=0.02
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.02) < 1e-9


def test_unfillable_partial_skipped_silently():
    m = LadderExitManager(final_tp_r=0.0)
    p = pos(volume=0.01)                          # 0.01*0.40 snaps to 0.0
    a = m.on_bar([p], bar(110.0, 0))
    assert partials(a) == []
    a = m.on_bar([p], bar(110.0, 1))               # marked filled, stays quiet
    assert partials(a) == []


# --------------------------------------------------------------------- #
# 9. closed-ticket cleanup + fresh recapture
# --------------------------------------------------------------------- #
def test_closed_tickets_forgotten_and_recaptured():
    m = LadderExitManager()
    p = pos()
    m.on_bar([p], bar(101.0, 0))
    assert p.ticket in m._trades
    m.on_bar([], bar(101.0, 1))
    assert p.ticket not in m._trades
    p2 = pos(sl=97.0)                             # same ticket, new sl -> d=3
    m.on_bar([p2], bar(101.0, 2))
    assert abs(m._trades[p2.ticket].d - 3.0) < 1e-9


# --------------------------------------------------------------------- #
# 10. emitted objects are orb.babysitter.Action instances
# --------------------------------------------------------------------- #
def test_emits_babysitter_actions():
    m = LadderExitManager()
    p = pos()
    a = m.on_bar([p], bar(110.0, 0))
    assert a and all(isinstance(x, Action) for x in a)


def test_supports_candle_flag():
    assert LadderExitManager.SUPPORTS_CANDLE is True
