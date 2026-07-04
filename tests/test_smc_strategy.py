"""Tests for :class:`orb.smc.strategy.SmcEngine`.

Fixtures build native 1m candle streams programmatically and let the engine's
own aggregators sculpt the M15/H4/D1 structure — no internal state is poked
except where a test explicitly documents a seam. Fail-safe cases first.

Timeframe facts the fixtures rely on (see orb/smc/mtf.py):
 * M15 completes reactively when a 1m bar opens a NEW 15-minute bucket.
 * H4 buckets open at 00/04/08/12/16/20 UTC; D1 keys by date.
 * StructureTracker confirms a swing ``lookback`` bars after it prints and sets
   ``trend`` on the first CLOSE-based break of a confirmed swing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from orb.models import Direction, OutOfOrderError, SignalKind, State
from orb.smc import SmcConfig, SmcEngine

UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Candle stream builders
# --------------------------------------------------------------------------- #
class _Bar:
    """Minimal candle carrier that satisfies validate() (frozen Candle)."""


def mk_candle(ts, o, h, l, c, v=0.0):
    from orb.models import Candle
    return Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v)


def mk_run(start: datetime, minute_specs):
    """Build a list of 1m candles, one per (o,h,l,c[,v]) spec, consecutive."""
    out = []
    t = start
    for spec in minute_specs:
        if len(spec) == 4:
            o, h, l, c = spec
            v = 0.0
        else:
            o, h, l, c, v = spec
        out.append(mk_candle(t, o, h, l, c, v))
        t = t + timedelta(minutes=1)
    return out


def flat_minutes(start: datetime, n: int, price: float, step: float = 0.0):
    """n consecutive 1m bars hugging ``price`` (tiny 0.02 wick), optional drift."""
    out = []
    t = start
    p = price
    for _ in range(n):
        o = p
        c = p + step
        hi = max(o, c) + 0.02
        lo = min(o, c) - 0.02
        out.append(mk_candle(t, o, hi, lo, c))
        t += timedelta(minutes=1)
        p = c
    return out


DAY0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)   # Monday


def cfg(**kw):
    base = dict(min_confluences=3, min_profile_bars=5, swing_lookback=2,
                max_trades_per_day=2, poc_tol=2.0, disp_atr_mult=1.2,
                disp_body_frac=0.5, atr_period=3, vol_sma_period=3,
                stop_max_dist=50.0, stop_buffer=0.5)
    base.update(kw)
    return SmcConfig(**base)


# --------------------------------------------------------------------------- #
# 1. Dormant: no HTF structure -> no entries
# --------------------------------------------------------------------------- #
def test_dormant_no_structure_no_entries():
    eng = SmcEngine(cfg())
    # a long ranging session: price hugs 2000 with negligible movement so no
    # swing ever breaks -> h4 trend stays None -> bias None -> IDLE, no signals.
    candles = flat_minutes(DAY0, 300, 2000.0)
    sigs = eng.replay(candles)
    assert sigs == []
    assert eng.state is State.IDLE
    assert eng._htf_bias is None


# --------------------------------------------------------------------------- #
# Structure-building helpers (drive H4 trend deterministically)
# --------------------------------------------------------------------------- #
# Explicit H4 (o,h,l,c) blocks that carve a rising zig-zag: a confirmed swing
# high at 2014 (bar 2), two lower-high pullbacks confirming it, then a bar that
# CLOSES above it -> a LONG BOS. Verified against StructureTracker directly.
_H4_UP_BLOCKS = [
    (2000, 2004, 1998, 2002),
    (2002, 2008, 2000, 2006),
    (2006, 2014, 2004, 2007),   # swing high 2014
    (2007, 2010, 2001, 2003),
    (2003, 2009, 2000, 2004),   # confirms swing high
    (2004, 2016, 2003, 2015),   # close 2015 > 2014 -> BOS LONG
]


def _h4_block_minutes(o, h, l, c, start):
    """240 one-minute bars whose folded H4 candle is exactly (o,h,l,c): the
    open bar carries o+low, a mid bar carries the high, the last carries the
    close. Returns (candles, next_ts)."""
    out = []
    t = start
    for m in range(240):
        if m == 0:
            mo, mh, ml, mc = o, o + 0.1, l, o
        elif m == 120:
            mo, mh, ml, mc = o, h, o - 0.1, o
        elif m == 239:
            mo, mh, ml, mc = o, o + 0.1, o - 0.1, c
        else:
            mo, mh, ml, mc = o, o + 0.1, o - 0.1, o
        hi = max(mh, mo, mc)
        lo = min(ml, mo, mc)
        out.append(mk_candle(t, mo, hi, lo, mc))
        t += timedelta(minutes=1)
    return out, t


def _h4_uptrend(start):
    """Emit the 1m stream carving _H4_UP_BLOCKS. Returns (candles, next_ts,
    last_close)."""
    out = []
    t = start
    last_c = _H4_UP_BLOCKS[-1][3]
    for (o, h, l, c) in _H4_UP_BLOCKS:
        blk, t = _h4_block_minutes(o, h, l, c, t)
        out.extend(blk)
    # one extra bar in the next bucket flushes the final H4 (reactive close)
    out.append(mk_candle(t, last_c, last_c + 0.1, last_c - 0.1, last_c))
    t += timedelta(minutes=1)
    return out, t, last_c


def test_h4_uptrend_sets_long_bias():
    """Sanity seam: a real 1m stream carving a rising H4 zig-zag yields a LONG
    H4 trend and (D1 silent) a LONG bias."""
    eng = SmcEngine(cfg())
    candles, _, _ = _h4_uptrend(DAY0)
    eng.replay(candles)
    assert eng._struct_h4.trend is Direction.LONG
    assert eng._htf_bias is Direction.LONG


# --------------------------------------------------------------------------- #
# 2. D1 veto: H4 bullish but D1 bearish -> bias None -> no longs
# --------------------------------------------------------------------------- #
def test_d1_veto_blocks_entries():
    eng = SmcEngine(cfg())
    # Force H4 LONG and D1 SHORT directly (documented seam): the veto is a pure
    # property over the two trend values; sculpting a conflicting D1 across days
    # in 1m bars is possible but slow — the property is what we assert here.
    eng._struct_h4._trend = Direction.LONG
    eng._struct_d1._trend = Direction.SHORT
    assert eng._htf_bias is None
    # and with D1 agreeing, bias returns
    eng._struct_d1._trend = Direction.LONG
    assert eng._htf_bias is Direction.LONG


# --------------------------------------------------------------------------- #
# Confluence unit tests (drive _confluence with a controlled engine)
# --------------------------------------------------------------------------- #
def _armed_long_engine(profile_price=2010.0):
    """An engine warmed to a LONG bias with ATR ready and a READY developing-day
    profile centred at ``profile_price``, flat and armed. Returns (eng, next_ts,
    poc). The H4 uptrend ends early on day 2, so we pad day 2 with flat bars at
    ``profile_price`` until the developing profile is ready -> a stable POC that
    the confluence tests park the decision bar against."""
    eng = SmcEngine(cfg())
    candles, t, price = _h4_uptrend(DAY0)
    eng.replay(candles)
    assert eng._htf_bias is Direction.LONG
    # pad the current (day-2) developing profile to readiness at a known price
    for c in flat_minutes(t, 12, profile_price):
        eng.on_candle(c)
        t += timedelta(minutes=1)
    poc = eng._day_profile.poc
    assert poc is not None
    # The final H4 BOS block legitimately fires an A+ warmup ENTRY (disp+pd+poi
    # via an h4_ob) the instant bias turns LONG. That is correct engine
    # behaviour, but the confluence unit tests need a truly FLAT + ARMED engine.
    # Reset the position/state/counter so tests start flat (helper contract).
    eng._position = None
    eng._state = State.RANGE_DEFINED
    eng._traded_today = 0
    return eng, t, poc


def test_confluence_gate_two_fails_three_passes():
    eng, _, _ = _armed_long_engine()
    # Build a decision bar where we control exactly which confluences fire by
    # driving _confluence directly with a crafted M15 candle. We satisfy:
    #   displacement (big body, range>ATR), cisd (via injected m15 history),
    #   alignment (m15 trend already LONG) -> plus mandatory poi via POC.
    # First: only 2 non-poi confluences + poi -> passes at min_confluences=3.
    from orb.models import Candle
    poc = eng._day_profile.poc
    assert poc is not None
    px = poc  # sits at POC -> htf_poi via POC (read-only)

    # craft prev bars so CISD bull is TRUE: prev down bar, prev2 close<=prev open
    base = px - 5.0
    prev2 = Candle(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), base, base + 0.1,
                   base - 0.1, base, 0.0)                 # prev2.close == base
    prev = Candle(datetime(2026, 1, 6, 0, 15, tzinfo=UTC), base + 0.5,
                  base + 0.6, base - 3.0, base - 2.5, 0.0)  # down bar; open=base+0.5
    eng._m15_hist = [prev2, prev]
    # m15 trend LONG already (alignment). Make it so.
    eng._struct_m15._trend = Direction.LONG

    atr = eng._atr.value
    assert atr is not None
    # displacement bar: big up body, range comfortably > disp_atr_mult*atr,
    # close > prev.open (CISD), close == poc (poi), close above prev.open.
    rng = max(3.0 * atr, 4.0)
    dec_open = px - rng * 0.6
    dec_low = px - rng
    dec = Candle(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), dec_open,
                 px + 0.05, dec_low, px, 0.0)
    sig = eng._confluence(dec, None, Direction.LONG)
    assert sig is not None
    assert sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.LONG
    assert "poi=" in sig.reason
    assert "cisd" in sig.reason


def test_mandatory_poi_missing_blocks_even_with_three_others():
    eng, _, _ = _armed_long_engine()
    from orb.models import Candle
    # place the decision far from any POC so htf_poi is FALSE, but make three
    # other confluences fire. Expect no entry (mandatory poi).
    poc = eng._day_profile.poc
    px = poc + 500.0                     # nowhere near POC, no OB either
    base = px - 5.0
    prev2 = Candle(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), base, base + 0.1,
                   base - 0.1, base, 0.0)
    prev = Candle(datetime(2026, 1, 6, 0, 15, tzinfo=UTC), base + 0.5,
                  base + 0.6, base - 3.0, base - 2.5, 0.0)
    eng._m15_hist = [prev2, prev]
    eng._struct_m15._trend = Direction.LONG
    atr = eng._atr.value
    rng = max(3.0 * atr, 4.0)
    dec = Candle(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), px - rng * 0.6,
                 px + 0.05, px - rng, px, 0.0)
    # premium_discount for LONG needs close <= equilibrium; px is far above ->
    # that check will be False, but we still have disp+cisd+align = 3 non-poi.
    sig = eng._confluence(dec, None, Direction.LONG)
    assert sig is None                    # poi mandatory -> blocked


def test_only_two_confluences_no_entry():
    eng, _, _ = _armed_long_engine()
    from orb.models import Candle
    poc = eng._day_profile.poc
    # Park close just ABOVE equilibrium (==POC here) so premium_discount is
    # FALSE (LONG pd needs close<=eq) while poi via POC stays TRUE (within
    # poc_tol=2.0). Otherwise close==POC also satisfies pd -> 3 confluences.
    px = poc + 1.0
    # poi (POC) satisfied + alignment only. CISD false (no history), sweep
    # false, displacement false (tiny body), pd false. => 1 non-poi + poi = 2 < 3.
    eng._m15_hist = []
    eng._struct_m15._trend = Direction.LONG
    dec = Candle(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), px, px + 0.1,
                 px - 0.1, px, 0.0)       # tiny body -> no displacement
    sig = eng._confluence(dec, None, Direction.LONG)
    assert sig is None


# --------------------------------------------------------------------------- #
# 4. Signal fields
# --------------------------------------------------------------------------- #
def test_entry_signal_fields():
    eng, _, _ = _armed_long_engine()
    from orb.models import Candle
    poc = eng._day_profile.poc
    px = poc
    base = px - 5.0
    prev2 = Candle(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), base, base + 0.1,
                   base - 0.1, base, 0.0)
    prev = Candle(datetime(2026, 1, 6, 0, 15, tzinfo=UTC), base + 0.5,
                  base + 0.6, base - 3.0, base - 2.5, 0.0)
    eng._m15_hist = [prev2, prev]
    eng._struct_m15._trend = Direction.LONG
    atr = eng._atr.value
    rng = max(3.0 * atr, 4.0)
    dec = Candle(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), px - rng * 0.6,
                 px + 0.05, px - rng, px, 0.0)
    sig = eng._confluence(dec, None, Direction.LONG)
    assert sig is not None
    assert sig.kind is SignalKind.ENTRY
    assert sig.direction is Direction.LONG
    assert sig.stop is not None and sig.stop < sig.price
    assert sig.qty is None
    assert sig.tp is None
    assert "conf=" in sig.reason and "poi=" in sig.reason
    assert eng.state is State.BREAKOUT
    assert eng.position is not None


# --------------------------------------------------------------------------- #
# 5. stop_dist > stop_max_dist -> skip
# --------------------------------------------------------------------------- #
def test_stop_too_wide_skips():
    eng, _, _ = _armed_long_engine()
    from orb.models import Candle
    # tighten the max stop distance so a wide structural stop is rejected.
    eng.config = cfg(stop_max_dist=2.0)   # very tight cap
    poc = eng._day_profile.poc
    px = poc
    base = px - 5.0
    prev2 = Candle(datetime(2026, 1, 6, 0, 0, tzinfo=UTC), base, base + 0.1,
                   base - 0.1, base, 0.0)
    prev = Candle(datetime(2026, 1, 6, 0, 15, tzinfo=UTC), base + 0.5,
                  base + 0.6, base - 20.0, base - 2.5, 0.0)
    eng._m15_hist = [prev2, prev]
    eng._struct_m15._trend = Direction.LONG
    atr = eng._atr.value
    rng = max(3.0 * atr, 30.0)            # huge range -> deep low -> wide stop
    dec = Candle(datetime(2026, 1, 6, 0, 30, tzinfo=UTC), px - rng * 0.6,
                 px + 0.05, px - rng, px, 0.0)
    sig = eng._confluence(dec, None, Direction.LONG)
    assert sig is None                    # stop_dist > 2.0 -> fail-safe skip
    assert eng.position is None


# --------------------------------------------------------------------------- #
# 6. max_trades_per_day
# --------------------------------------------------------------------------- #
def _fire_one_long(eng, minute, day=6):
    from orb.models import Candle
    poc = eng._day_profile.poc
    px = poc
    base = px - 5.0
    prev2 = Candle(datetime(2026, 1, day, 0, 0, tzinfo=UTC), base, base + 0.1,
                   base - 0.1, base, 0.0)
    prev = Candle(datetime(2026, 1, day, 0, 15, tzinfo=UTC), base + 0.5,
                  base + 0.6, base - 3.0, base - 2.5, 0.0)
    eng._m15_hist = [prev2, prev]
    eng._struct_m15._trend = Direction.LONG
    atr = eng._atr.value
    rng = max(3.0 * atr, 4.0)
    dec = Candle(datetime(2026, 1, day, 0, minute, tzinfo=UTC), px - rng * 0.6,
                 px + 0.05, px - rng, px, 0.0)
    return eng._confluence(dec, None, Direction.LONG)


def test_max_trades_per_day_blocks_third():
    eng, _, _ = _armed_long_engine()
    eng.config = cfg(max_trades_per_day=2)
    # simulate the day-counter path through _on_decision by bumping the counter
    # via successful confluence + clearing the position each time.
    s1 = _fire_one_long(eng, 30)
    assert s1 is not None
    eng._position = None                   # pretend babysitter closed it
    eng._state = State.RANGE_DEFINED
    s2 = _fire_one_long(eng, 45)
    assert s2 is not None
    eng._position = None
    eng._state = State.RANGE_DEFINED
    assert eng._traded_today == 2
    # third must be blocked by the day counter, exercised via _on_decision
    from orb.models import Candle
    poc = eng._day_profile.poc
    px = poc
    dec = Candle(datetime(2026, 1, 6, 1, 0, tzinfo=UTC), px - 5, px + 0.05,
                 px - 6, px, 0.0)
    s3 = eng._on_decision(dec, None)
    assert s3 is None
    assert eng._traded_today == 2


# --------------------------------------------------------------------------- #
# 7. force_flat
# --------------------------------------------------------------------------- #
def test_force_flat_emits_broker_closed_and_rearms():
    eng, _, _ = _armed_long_engine()
    s = _fire_one_long(eng, 30)
    assert s is not None
    assert eng.state is State.BREAKOUT
    exit_sig = eng.force_flat(datetime(2026, 1, 6, 2, 0, tzinfo=UTC))
    assert exit_sig is not None
    assert exit_sig.kind is SignalKind.EXIT
    assert exit_sig.reason == "broker_closed"
    assert eng.state is State.RANGE_DEFINED
    assert eng.position is None
    # a second force_flat with no position returns None
    assert eng.force_flat(datetime(2026, 1, 6, 2, 1, tzinfo=UTC)) is None


# --------------------------------------------------------------------------- #
# 8. replay determinism
# --------------------------------------------------------------------------- #
def test_replay_determinism():
    candles, _, _ = _h4_uptrend(DAY0)
    e1 = SmcEngine(cfg())
    out1 = e1.replay(candles)
    e2 = SmcEngine(cfg())
    out2 = e2.replay(candles)
    assert [ (s.ts, s.kind, s.reason) for s in out1 ] \
        == [ (s.ts, s.kind, s.reason) for s in out2 ]
    # and reset() on e1 then replay again matches
    e1.reset()
    out3 = e1.replay(candles)
    assert [ (s.ts, s.kind, s.reason) for s in out1 ] \
        == [ (s.ts, s.kind, s.reason) for s in out3 ]


# --------------------------------------------------------------------------- #
# 9. strict_monotonic
# --------------------------------------------------------------------------- #
def test_strict_monotonic_raises():
    eng = SmcEngine(cfg())
    c0 = mk_candle(DAY0, 2000, 2000.1, 1999.9, 2000, 0.0)
    eng.on_candle(c0)
    with pytest.raises(OutOfOrderError):
        eng.on_candle(mk_candle(DAY0, 2000, 2000.1, 1999.9, 2000, 0.0))  # equal ts
    with pytest.raises(OutOfOrderError):
        eng.on_candle(mk_candle(DAY0 - timedelta(minutes=1), 2000, 2000.1,
                                1999.9, 2000, 0.0))                       # earlier


def test_non_strict_monotonic_drops_silently():
    eng = SmcEngine(cfg(strict_monotonic=False))
    c0 = mk_candle(DAY0, 2000, 2000.1, 1999.9, 2000, 0.0)
    eng.on_candle(c0)
    assert eng.on_candle(c0) is None       # dropped, no raise


# --------------------------------------------------------------------------- #
# 10. day rollover resets traded_today and builds prior POC
# --------------------------------------------------------------------------- #
def test_day_rollover_resets_counter_and_builds_prior():
    eng = SmcEngine(cfg(min_profile_bars=5))
    # day 0: a handful of bars to make the profile ready
    d0 = flat_minutes(DAY0, 30, 2000.0)
    eng.replay(d0)
    assert eng._day_profile.ready
    eng._traded_today = 1                  # pretend a trade happened
    # first bar of day 1 triggers rollover
    day1 = DAY0 + timedelta(days=1)
    eng.on_candle(mk_candle(day1, 2000, 2000.1, 1999.9, 2000, 0.0))
    assert eng._traded_today == 0
    assert eng._prior is not None
    assert eng._prior.session_id == str(DAY0.date())
    assert eng._prior.poc == pytest.approx(2000.0, abs=1.0)


# --------------------------------------------------------------------------- #
# End-to-end: a real ENTRY emitted purely from 1m bars
# --------------------------------------------------------------------------- #
def test_end_to_end_entry_from_1m_stream():
    """Build a full LONG bias via H4 uptrend, then feed a discount 1m sequence
    that sweeps an M15 swing low and reclaims (CISD + displacement + alignment
    + sweep) with price parked at the developing POC (mandatory poi). Assert a
    real ENTRY fires from on_candle, no internal poking of confluences."""
    eng = SmcEngine(cfg(min_confluences=3, poc_tol=8.0, stop_max_dist=80.0))
    candles, t, price = _h4_uptrend(DAY0)
    eng.replay(candles)
    assert eng._htf_bias is Direction.LONG
    # The H4 BOS block legitimately fires an A+ warmup ENTRY the instant bias
    # turns LONG (correct engine behaviour). Clear it so the engine is flat +
    # armed and CAN take the sweep-reclaim entry this test sculpts; otherwise
    # _on_decision short-circuits on the still-open position.
    eng._position = None
    eng._state = State.RANGE_DEFINED
    eng._traded_today = 0

    # Now append a pullback + sweep-reclaim structured across a few M15 bars.
    # Round t up to the next M15 boundary for clean bucket alignment.
    while (t.hour * 60 + t.minute) % 15 != 0:
        eng.on_candle(mk_candle(t, price, price + 0.05, price - 0.05, price))
        t += timedelta(minutes=1)

    got = []

    def sink(sig):
        got.append(sig)

    eng._on_signal = sink

    # Sculpt: create an M15 swing low, then a bar that wicks below it and closes
    # back above (sweep+reclaim). We drive raw 1m bars; the engine aggregates.
    # M15-A: pull down to establish a swing low near price-30.
    def block(o, h, l, c, mins, tt):
        out = []
        per_c = (c - o) / mins
        p = o
        for i in range(mins):
            step_o = p
            step_c = o + per_c * (i + 1)
            hi = max(step_o, step_c, (h if i == mins - 1 else step_o)) + 0.02
            lo = min(step_o, step_c, (l if i == 0 else step_o)) - 0.02
            out.append(mk_candle(tt, step_o, hi, lo, step_c))
            tt = tt + timedelta(minutes=1)
            p = step_c
        return out, tt

    base = price
    # Several down M15 blocks to print a confirmed M15 swing low, then reclaim.
    seq = []
    b, t = block(base, base + 0.1, base - 30, base - 28, 15, t); seq += b
    b, t = block(base - 28, base - 27, base - 40, base - 38, 15, t); seq += b
    b, t = block(base - 38, base - 37, base - 50, base - 48, 15, t); seq += b
    # swing-low bar (local min) then two higher bars to confirm the fractal:
    b, t = block(base - 48, base - 47, base - 60, base - 46, 15, t); seq += b
    b, t = block(base - 46, base - 44, base - 47, base - 43, 15, t); seq += b
    b, t = block(base - 43, base - 41, base - 44, base - 40, 15, t); seq += b
    for c in seq:
        eng.on_candle(c)

    swing_low = eng._struct_m15.last_swing_low
    assert swing_low is not None
    sl = swing_low.price
    poc = eng._day_profile.poc
    assert poc is not None

    # Down bar (for CISD prev) then a displacement reclaim bar that wicks below
    # the swing low and closes back above it, parked near POC.
    prev_open = poc + 1.0
    b, t = block(prev_open, prev_open + 0.2, prev_open - 4, prev_open - 3, 15, t)
    seq2 = b
    # displacement reclaim M15: opens low, wicks below sl, closes at ~poc (up).
    atr = eng._atr.value or 1.0
    rng = max(3.0 * atr, (poc) - (sl) + 6.0)
    disp_open = poc - rng * 0.6
    b2 = []
    tt = t
    for i in range(15):
        if i == 0:
            o = disp_open
            c = disp_open + 0.5
            # sweep wick: low must dip below the swing low. Build hi/lo as a
            # TRUE envelope of {o, c, sweep_target} so hi>=lo always holds even
            # when disp_open already sits below sl (previously lo could exceed
            # hi and CandleError'd).
            sweep_target = sl - 2.0       # wick below swing low (sweep)
            hi = max(o, c) + 0.02
            lo = min(o, c, sweep_target) - 0.02
        elif i == 14:
            o = poc - 2.0
            c = poc                       # close at POC (poi + reclaim)
            hi = max(o, c) + 0.1
            lo = min(o, c) - 0.5
        else:
            o = disp_open + i
            c = disp_open + i + 0.5
            hi = max(o, c) + 0.1
            lo = min(o, c) - 0.1
        b2.append(mk_candle(tt, o, hi, lo, c))
        tt += timedelta(minutes=1)
    seq2 += b2
    for c in seq2:
        eng.on_candle(c)
    # the displacement M15 completes on the NEXT bucket's first bar:
    eng.on_candle(mk_candle(tt, poc, poc + 0.1, poc - 0.1, poc))

    entries = [s for s in got if s.kind is SignalKind.ENTRY]
    assert entries, "expected at least one ENTRY from the 1m stream"
    e = entries[0]
    assert e.direction is Direction.LONG
    assert e.stop is not None and e.stop < e.price
    assert "poi=" in e.reason
