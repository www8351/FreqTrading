# Two-Stage Discrete SL Advancement (N+1 Confirmed) — XAUUSD SMC Twins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SMC ladder's continuous swing/ATR trailing stop with a strictly bounded, two-stage discrete SL advancement (breakeven+costs, then one final structural-or-floor lock, then frozen forever), driven by closed-M1-candle N/N+1 confirmation, kept byte-parity between `orb/smc/exits.py` (Python) and `mql5/SmcXau_EA.mq5` (MQL5 copy-trade master), and add non-blocking HMAC-signed copy-trade broadcasting to every EA-side SL modification.

**Architecture:** A single-slot "candidate N" buffer replaces the old multi-timeframe trail context (`TimeframeAggregator`/`StructureTracker`/`WilderATR` imports removed from `orb/smc/exits.py`). Every closed candle shifts the buffer; the position's stage machine (`stage1_done`/`stage2_done` flags, capped at exactly two SL modifications) evaluates against the buffered candle — never the live/current candle — which reproduces the MQL5 twin's shift-2/shift-1/shift-0 read pattern exactly. The EA additionally fixes a latent bug where a breakeven-moved SL corrupts the R-denominator for the partial ladder (fixed by recovering the true initial stop from MT5 deal history), and gains an in-memory bounded broadcast queue drained by a 1-second timer over `WebRequest`.

**Tech Stack:** Python 3.11+ stdlib only (no numpy/pandas) for `orb/`, `scripts/`, `tests/` (pytest); MQL5 (`mql5/SmcXau_EA.mq5`) — stock `<Trade/Trade.mqh>` only, zero DLLs, zero custom includes.

## Global Constraints

- Scope is EA + Python parity only. **Do NOT touch** `orb/babysitter.py`, `orb/engine.py`, `orb/svp/` — `git diff main -- orb/babysitter.py orb/engine.py orb/svp/` must stay empty throughout.
- Stage triggers use exclusively fully **closed M1 candles** with an **N+1 confirmation delay**: trigger candle N closes, wait for N+1 to close, modify at the open of N+2 (Python: return the `Action` from the `on_bar` call driven by N+1's `observe`; EA: shift-2/shift-1/shift-0 on every new M1 bar).
- Exactly **two** SL modifications per position ever (stage 1, then stage 2), then frozen — no continuous trailing.
- Partials (5R/7R) and the final TP (10R) are **unchanged** and stay **intra-candle** (evaluated against the live/current close, not candle N) — an intentional asymmetry vs. the closed-candle stage triggers, documented not fixed.
- Every SL modification must broadcast to the copy-trading backend **without ever blocking** the terminal (EA) or the trading loop (Python — already satisfied by the existing `orb/broadcast.py`, just keep routing intact).
- `be_cost` (price units) = `spread + commission_per_lot / value_per_move`, `value_per_move = tick_value / tick_size` (100 for XAUUSD).
- The repo-state hazards from the original brief (stale branch, concurrent checkout) are **already resolved**: branch `feat/smc-two-stage-exits` was created fresh off `main` (`9142055`, matches `origin/main`) before this plan was written; `mql5/SmcXau_EA.mq5` exists on this branch. No `git checkout`/worktree steps are needed before Task 1.
- `mql5/SmcXau_EA.ex5` (committed binary) will be stale after Task 8-10 — compiling it (MetaEditor F7) is an **owner-manual** action (Task 11), not something to script.
- Wire contract for broadcast events is `docs/copytrade_schema.md` (schema_version 1) — every key present, `null` literal for non-applicable fields, never omitted.
- HMAC reference vector (pin exactly, matches `tests/test_broadcast.py:81-87`): `HMAC_SHA256("test-secret", "1700000000." + "{\"a\":1}")` (message = `1700000000.{"a":1}`, 32 bytes) = `8cb2c3355fca388e9ac2caec004f4d5d7045d74937ab5faad61dc11682247a9f`.

---

### Task 1: `Action.reason` + `Mt5Broker.modify_sl(reason=...)` plumbing

The stage machine needs to tag each SL modification with `"stage1_be"` / `"stage2_lock"` so it flows through to the broadcast payload's `reason` field. `orb.babysitter.Action` is shared by `Babysitter` and `LadderExitManager`; `Mt5Broker.modify_sl` is the call site the live CLI uses to apply `update_sl` actions.

**Files:**
- Modify: `orb/babysitter.py:28-33` (the `Action` dataclass)
- Modify: `orb/broker/mt5.py:391-408` (the `modify_sl` method)
- Modify: `orb/cli.py:776-780` (the live `on_bar` handler's `update_sl` dispatch)
- Test: `tests/test_broker_events.py`

**Interfaces:**
- Produces: `Action(kind, ticket, volume=0.0, sl=0.0, reason="")` — new optional `reason` field, default `""`, backward compatible with all existing `Action(...)` call sites (positional args unaffected).
- Produces: `Mt5Broker.modify_sl(ticket: int, sl: float, reason: str = "") -> dict | None` — forwards `reason or None` into the existing `self._emit("modify_sl", ...)` call (which already accepts a `reason` kwarg via `**extra`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_broker_events.py` (after `test_modify_sl_emits`, before `test_update_stop_emits_modify_sl`):

```python
def test_modify_sl_emits_with_reason():
    b, fake, events = make_broker()
    fake.positions = [SimpleNamespace(ticket=11, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL,
                                      sl=4124.41, tp=0.0)]
    res = b.modify_sl(11, 4120.00, reason="stage1_be")
    assert res is not None
    assert events[0].reason == "stage1_be"


def test_modify_sl_reason_defaults_to_none():
    b, fake, events = make_broker()
    fake.positions = [SimpleNamespace(ticket=11, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL,
                                      sl=4124.41, tp=0.0)]
    b.modify_sl(11, 4120.00)
    assert events[0].reason is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_broker_events.py -k "modify_sl_emits_with_reason or reason_defaults_to_none" -v`
Expected: FAIL — `TypeError: modify_sl() got an unexpected keyword argument 'reason'`

- [ ] **Step 3: Add the `reason` field to `Action`**

In `orb/babysitter.py`, replace:

```python
@dataclass
class Action:
    kind: str          # "partial_close" | "update_sl"
    ticket: int
    volume: float = 0.0
    sl: float = 0.0
```

with:

```python
@dataclass
class Action:
    kind: str          # "partial_close" | "update_sl"
    ticket: int
    volume: float = 0.0
    sl: float = 0.0
    reason: str = ""    # optional producer tag, e.g. "stage1_be" / "stage2_lock"
```

- [ ] **Step 4: Thread `reason` through `modify_sl`**

In `orb/broker/mt5.py`, replace the `modify_sl` method:

```python
    def modify_sl(self, ticket: int, sl: float) -> dict | None:
        m = self._mt5
        mine = [p for p in self.my_positions() if p.ticket == ticket]
        if not mine:
            return None
        p = mine[0]
        if abs((p.sl or 0.0) - sl) < SL_TOLERANCE:
            return None
        request = {
            "action": m.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": p.ticket,
            "sl": round(sl, PRICE_DP),
            "tp": getattr(p, "tp", 0.0) or 0.0,
        }
        res = self._send(request)
        self._emit("modify_sl", request=request, result=res)
        return res
```

with:

```python
    def modify_sl(self, ticket: int, sl: float, reason: str = "") -> dict | None:
        m = self._mt5
        mine = [p for p in self.my_positions() if p.ticket == ticket]
        if not mine:
            return None
        p = mine[0]
        if abs((p.sl or 0.0) - sl) < SL_TOLERANCE:
            return None
        request = {
            "action": m.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": p.ticket,
            "sl": round(sl, PRICE_DP),
            "tp": getattr(p, "tp", 0.0) or 0.0,
        }
        res = self._send(request)
        self._emit("modify_sl", request=request, result=res, reason=reason or None)
        return res
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_broker_events.py -v`
Expected: PASS (all tests in the file, including the two new ones)

- [ ] **Step 6: Wire the live CLI dispatch to forward `Action.reason`**

In `orb/cli.py`, replace (around line 776-780):

```python
                    else:
                        res = broker.modify_sl(act.ticket, act.sl)
                        if res is not None and not args.quiet:
                            print(f"# chase_sl ticket={act.ticket} "
                                  f"sl={act.sl:.{dp}f}", file=sys.stderr)
```

with:

```python
                    else:
                        res = broker.modify_sl(act.ticket, act.sl,
                                               reason=act.reason)
                        if res is not None and not args.quiet:
                            print(f"# chase_sl ticket={act.ticket} "
                                  f"sl={act.sl:.{dp}f}", file=sys.stderr)
```

`Babysitter`'s `Action` never sets `reason` (stays `""` default, guaranteed to exist after Task 1), so this still passes `""` -> `modify_sl` forwards `None` downstream — byte-identical to today for the ORB/SVP live paths.

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: PASS, same count as baseline + 2

- [ ] **Step 8: Commit**

```bash
git add orb/babysitter.py orb/broker/mt5.py orb/cli.py tests/test_broker_events.py
git commit -m "feat(broker): optional reason tag on SL modifications

Action gains an optional reason field (default \"\", backward compatible)
and Mt5Broker.modify_sl forwards it into the trade-event/broadcast payload.
Prep for the SMC two-stage exit refactor, which tags its two SL moves
stage1_be/stage2_lock."
```

---

### Task 2: Two-stage exits — failing tests (`tests/test_smc_exits.py`)

Write the full replacement test suite first (TDD red step). This removes the trail/BE-specific tests and adds the two-stage behaviors. The partials/final/vol-snap/cleanup/emits tests are kept because the ladder ordering logic (steps 1-2 in `on_bar`) is unchanged.

**Files:**
- Modify (full rewrite): `tests/test_smc_exits.py`

**Interfaces:**
- Consumes: `orb.smc.exits.LadderExitManager` (not yet updated — these tests intentionally fail against Task 3's target API).
- Consumes: `orb.babysitter.Action` (from Task 1, already has `.reason`).

- [ ] **Step 1: Replace the whole file**

Replace the full contents of `tests/test_smc_exits.py` with:

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
# 4. SHORT mirror: partials + final
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


# --------------------------------------------------------------------- #
# 5. volume snapping + silent skip of unfillable rungs
# --------------------------------------------------------------------- #
def test_volume_snap_down():
    m = LadderExitManager(final_tp_r=0.0)
    p = pos(volume=0.05)
    a = m.on_bar([p], close=110.0)               # r=5: 0.05*0.40=0.02
    pc = partials(a)
    assert len(pc) == 1 and abs(pc[0].volume - 0.02) < 1e-9


def test_unfillable_partial_skipped_silently():
    m = LadderExitManager(final_tp_r=0.0)
    p = pos(volume=0.01)                         # 0.01*0.40 snaps to 0.0
    a = m.on_bar([p], close=110.0)
    assert partials(a) == []
    a = m.on_bar([p], close=110.0)               # marked filled, stays quiet
    assert partials(a) == []


# --------------------------------------------------------------------- #
# 6. closed-ticket cleanup + fresh recapture
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
# 7. emitted objects are orb.babysitter.Action instances
# --------------------------------------------------------------------- #
def test_emits_babysitter_actions():
    m = LadderExitManager()
    p = pos()
    a = m.on_bar([p], close=110.0)
    assert a and all(isinstance(x, Action) for x in a)


# ======================================================================= #
# Two-stage discrete SL (N/N+1 confirmed, capped at 2, then frozen)
# ======================================================================= #

def _mgr(**ov):
    base = dict(partial_levels=(), final_tp_r=0.0, stage1_at_r=1.0,
                stage2_at_r=2.0, stage2_min_lock_r=1.0, lock_buffer=0.5,
                be_cost=0.0)
    base.update(ov)
    return LadderExitManager(**base)


# --------------------------------------------------------------------- #
# 8. stage 1 requires N+1 confirmation (fires one bar later, not on N's
#    own close)
# --------------------------------------------------------------------- #
def test_stage1_requires_n_plus_1_confirmation():
    m = _mgr(be_cost=0.2)
    p = pos()                                    # long @100, sl 98 -> d=2
    m.observe(candle(0, 101.5, 102.5))           # N candidate: close 102, r=1
    a = m.on_bar([p], close=102.0)                # N+1 not seen yet: no action
    assert sls(a) == []
    m.observe(candle(1, 101.5, 102.5))           # N+1 closes now
    a = m.on_bar([p], close=102.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 100.2) < 1e-9
    assert s[0].reason == "stage1_be"


# --------------------------------------------------------------------- #
# 9. breakeven includes costs, both directions
# --------------------------------------------------------------------- #
def test_stage1_be_includes_costs_long():
    m = _mgr(be_cost=0.2)
    p = pos()                                    # long @100 sl 98 -> d=2
    m.observe(candle(0, 101.5, 102.5))
    m.on_bar([p], close=102.0)
    m.observe(candle(1, 101.5, 102.5))
    a = m.on_bar([p], close=102.0)
    assert abs(sls(a)[0].sl - 100.2) < 1e-9        # entry + be_cost


def test_stage1_be_includes_costs_short():
    m = _mgr(be_cost=0.2)
    p = pos(type=SHORT, price_open=100.0, sl=102.0)  # d=2
    m.observe(candle(0, 97.5, 98.5))              # close 98, r=(100-98)/2=1
    m.on_bar([p], close=98.0)
    m.observe(candle(1, 97.5, 98.5))
    a = m.on_bar([p], close=98.0)
    assert abs(sls(a)[0].sl - 99.8) < 1e-9          # entry - be_cost


# --------------------------------------------------------------------- #
# 10. stage 2: structural level wins when it locks MORE than the floor
# --------------------------------------------------------------------- #
def test_stage2_structural_wins_when_more_favorable():
    m = _mgr()
    p = pos()                                     # long @100 sl98 d=2
    m.observe(candle(0, 101.5, 102.5))            # stage1 candidate: close 102 r=1
    m.on_bar([p], close=102.0)
    m.observe(candle(1, 101.5, 102.5))            # confirm -> stage1 fires
    apply(p, m.on_bar([p], close=102.0))
    assert abs(p.sl - 100.0) < 1e-9                # entry + 0 cost

    m.observe(candle(2, 103.6, 104.6))            # stage2 candidate: close 104.1 r=2.05, low 103.6
    m.on_bar([p], close=104.1)
    m.observe(candle(3, 103.6, 104.6))            # confirm -> stage2 fires
    a = m.on_bar([p], close=104.1)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 103.1) < 1e-9   # structural 103.6-0.5=103.1 > floor 102
    assert s[0].reason == "stage2_lock"


# --------------------------------------------------------------------- #
# 11. stage 2: minimum-lock floor wins when structural is worse
# --------------------------------------------------------------------- #
def test_stage2_floor_wins_when_structural_is_worse():
    m = _mgr()
    p = pos()
    m.observe(candle(0, 101.5, 102.5))
    m.on_bar([p], close=102.0)
    m.observe(candle(1, 101.5, 102.5))
    apply(p, m.on_bar([p], close=102.0))          # stage1 -> sl=100.0

    m.observe(candle(2, 101.0, 107.0))            # close 104 r=2, low 101 -> structural 100.5
    m.on_bar([p], close=104.0)
    m.observe(candle(3, 101.0, 107.0))
    a = m.on_bar([p], close=104.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 102.0) < 1e-9   # floor entry+1*d beats structural 100.5


# --------------------------------------------------------------------- #
# 12. SL frozen after both stages: partials still fire independently
# --------------------------------------------------------------------- #
def test_sl_frozen_after_two_stages_partials_still_fire():
    m = LadderExitManager(final_tp_r=0.0, stage1_at_r=1.0, stage2_at_r=2.0,
                          stage2_min_lock_r=1.0, lock_buffer=0.5, be_cost=0.0)
    p = pos()                                     # long @100 sl98 d=2
    m.observe(candle(0, 101.5, 102.5))
    m.on_bar([p], close=102.0)
    m.observe(candle(1, 101.5, 102.5))
    apply(p, m.on_bar([p], close=102.0))           # stage1 -> sl 100.0
    m.observe(candle(2, 103.6, 104.6))
    m.on_bar([p], close=104.1)
    m.observe(candle(3, 103.6, 104.6))
    apply(p, m.on_bar([p], close=104.1))           # stage2 -> sl 103.1 (frozen)
    frozen_sl = p.sl

    a = m.on_bar([p], close=110.0)                 # r=5
    assert len(partials(a)) == 1
    apply(p, a)
    a = m.on_bar([p], close=114.0)                 # r=7
    assert len(partials(a)) == 1
    apply(p, a)

    m.observe(candle(4, 119.0, 121.0))
    m.on_bar([p], close=120.0)
    m.observe(candle(5, 119.0, 121.0))
    a = m.on_bar([p], close=120.0)
    assert sls(a) == []
    assert p.sl == frozen_sl
    assert p.ticket in m._trades


# --------------------------------------------------------------------- #
# 13. no continuous trailing: many further qualifying candles, zero moves
# --------------------------------------------------------------------- #
def test_no_continuous_trailing_after_stage2():
    m = _mgr()
    p = pos()
    m.observe(candle(0, 101.5, 102.5)); m.on_bar([p], close=102.0)
    m.observe(candle(1, 101.5, 102.5)); apply(p, m.on_bar([p], close=102.0))
    m.observe(candle(2, 103.6, 104.6)); m.on_bar([p], close=104.1)
    m.observe(candle(3, 103.6, 104.6)); apply(p, m.on_bar([p], close=104.1))
    frozen = p.sl
    for i, lo in enumerate([110.0, 115.0, 120.0, 125.0], start=4):
        m.observe(candle(i, lo, lo + 2.0))
        assert sls(m.on_bar([p], close=lo + 1.0)) == []
    assert p.sl == frozen


# --------------------------------------------------------------------- #
# 14. one stage per bar: a gap candle qualifying for both stages while
#     stage 1 is pending fires ONLY stage 1 this bar; stage 2 needs its
#     own later qualifying N/N+1 pair
# --------------------------------------------------------------------- #
def test_gap_candle_fires_only_stage1_not_both():
    m = _mgr()
    p = pos()                                     # long @100 sl98 d=2
    m.observe(candle(0, 119.0, 121.0))            # close 120, r=10 -- qualifies both stages
    m.on_bar([p], close=120.0)                    # N not confirmed yet
    m.observe(candle(1, 119.0, 121.0))            # N+1 confirms
    a = m.on_bar([p], close=120.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 100.0) < 1e-9   # stage1 only, be=entry+0
    apply(p, a)

    m.observe(candle(2, 119.0, 121.0))            # candle1 now serves as N for stage2
    a = m.on_bar([p], close=120.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 118.5) < 1e-9   # structural 119-0.5=118.5


# --------------------------------------------------------------------- #
# 15. SHORT mirror of the full two-stage sequence
# --------------------------------------------------------------------- #
def test_short_two_stage_mirror():
    m = _mgr(be_cost=0.2)
    p = pos(type=SHORT, price_open=100.0, sl=102.0)   # d=2
    m.observe(candle(0, 97.5, 98.5))               # close 98, r=1
    m.on_bar([p], close=98.0)
    m.observe(candle(1, 97.5, 98.5))
    a = m.on_bar([p], close=98.0)
    s = sls(a)
    assert len(s) == 1 and abs(s[0].sl - 99.8) < 1e-9   # entry - be_cost
    apply(p, a)

    m.observe(candle(2, 95.4, 96.4))               # close 95.9, r=2.05, high 96.4
    m.on_bar([p], close=95.9)
    m.observe(candle(3, 95.4, 96.4))
    a = m.on_bar([p], close=95.9)
    s = sls(a)
    # structural = high+buffer = 96.9; floor = entry - 1*d = 98.0; tighter(min) = 96.9
    assert len(s) == 1 and abs(s[0].sl - 96.9) < 1e-9


# --------------------------------------------------------------------- #
# 16. first-sight flag derivation from a restart-recovered sl
# --------------------------------------------------------------------- #
def test_first_sight_derives_stage_flags_from_restart_sl():
    m = _mgr(be_cost=0.2)
    p_be = pos(ticket=1, sl=100.2)                 # entry100, sl already AT be
    a = m.on_bar([p_be], close=101.0)
    assert sls(a) == []
    assert m._trades[1].stage1_done is True
    assert m._trades[1].stage2_done is False

    p_beyond = pos(ticket=2, sl=103.0)             # entry100, sl BEYOND be (100.2)
    m.on_bar([p_beyond], close=101.0)
    assert m._trades[2].stage1_done is True
    assert m._trades[2].stage2_done is True

    # frozen: even a strongly-qualifying N/N+1 pair emits nothing
    m.observe(candle(0, 119.0, 121.0))
    m.on_bar([p_beyond], close=120.0)
    m.observe(candle(1, 119.0, 121.0))
    a = m.on_bar([p_beyond], close=120.0)
    assert sls(a) == []


# --------------------------------------------------------------------- #
# 17. default construction
# --------------------------------------------------------------------- #
def test_default_construction():
    m = LadderExitManager()
    assert m.stage1_at_r == 1.0
    assert m.stage2_at_r == 2.0
    assert m.stage2_min_lock_r == 1.0
    assert m.lock_buffer == 0.5
    assert m.be_cost == 0.0


# --------------------------------------------------------------------- #
# 18. constructor validation
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("kw", [
    {"stage1_at_r": 0.0},
    {"stage1_at_r": 2.0, "stage2_at_r": 2.0},      # not strictly <
    {"stage1_at_r": 3.0, "stage2_at_r": 2.0},      # reversed
    {"stage2_min_lock_r": 0.0},
    {"stage2_min_lock_r": 2.5, "stage2_at_r": 2.0},  # > stage2_at_r
    {"lock_buffer": -0.1},
    {"be_cost": -0.1},
    {"vol_step": 0.0},
    {"vol_min": 0.0},
    {"default_d": 0.0},
])
def test_invalid_construction_raises(kw):
    with pytest.raises(ValueError):
        LadderExitManager(**kw)
```

- [ ] **Step 2: Run the tests to verify they fail for the right reason**

Run: `python -m pytest tests/test_smc_exits.py -v`
Expected: FAIL — `TypeError: LadderExitManager.__init__() got an unexpected keyword argument 'stage1_at_r'` (the class still has the old `be_at_r`/`trail_*` API). This confirms the tests are exercising the not-yet-built API, not a typo.

- [ ] **Step 3: Commit the red tests**

```bash
git add tests/test_smc_exits.py
git commit -m "test(smc): two-stage discrete SL exit tests (red)

Replaces the continuous swing/ATR trail tests with the two-stage
(breakeven+costs, then structural-or-floor lock, then frozen) design.
Keeps the unaffected partials/final/cleanup/emits tests. Fails against
the current LadderExitManager API pending the Task 3 rewrite."
```

---

### Task 3: Two-stage exits — implementation (`orb/smc/exits.py`)

Make Task 2's tests pass.

**Files:**
- Modify (full rewrite): `orb/smc/exits.py`

**Interfaces:**
- Consumes: `orb.babysitter.LONG`, `orb.babysitter.Action` (with `.reason` from Task 1), `orb.models.Candle`.
- Produces: `LadderExitManager(*, partial_levels=((5.0,0.40),(7.0,0.30)), final_tp_r=10.0, stage1_at_r=1.0, stage2_at_r=2.0, stage2_min_lock_r=1.0, lock_buffer=0.5, be_cost=0.0, vol_min=0.01, vol_step=0.01, default_d=2.0)` — same `.observe(candle: Candle) -> None` and `.on_bar(positions, close: float) -> list[Action]` contract as before. `._trades: dict[int, _PosState]` still exposed (tests read it directly), `_PosState` now has `.entry .d .vol0 .filled .stage1_done .stage2_done` (no more `.be_done`).

- [ ] **Step 1: Replace the whole file**

Replace the full contents of `orb/smc/exits.py` with:

```python
"""Ladder exit manager: Babysitter-compatible multi-stage exit layer.

Drop-in replacement for :class:`orb.babysitter.Babysitter` — same
``on_bar(positions, close) -> list[Action]`` consumer contract, emitting the
exact same :class:`orb.babysitter.Action` objects (``partial_close`` with
ticket+volume, ``update_sl`` with ticket+sl+reason) — plus an
``observe(candle)`` feed that tracks the last two CLOSED 1m candles for the
two-stage exit's N/N+1 confirmation delay. Per position, with
d = |entry - initial SL| and r = profit / d:

  - PARTIALS: each ``(r_level, fraction)`` rung closes ``fraction * vol0``
    (snapped DOWN to ``vol_step``), once, evaluated intra-candle against the
    live ``close`` passed to ``on_bar`` (owner scope choice — asymmetric vs.
    the closed-candle stage triggers below). Unfillable rungs (below
    ``vol_min`` or residual would drop under ``vol_min``) are consumed
    silently — fail-safe: skip the order, keep managing.
  - FINAL: at ``final_tp_r`` close the whole remainder and forget the
    ticket. Deterministic gap behavior: rungs are evaluated in order,
    cumulative residual honored, final closes what is left — total closed
    == vol0.
  - TWO-STAGE SL (replaces continuous trailing): bounded to exactly two
    modifications per position, then frozen forever.

    Timing: every closed 1m candle X delivered to ``observe`` closes the
    N/N+1 gap for the PRIOR candle — candidate N = X-1, N+1 = X (closed by
    delivery). The resulting SL update, returned from the very next
    ``on_bar`` call (every consumer calls ``observe`` immediately before
    ``on_bar`` each bar), takes effect starting the NEXT candle — the same
    N -> N+1 -> "act at open of N+2" delay as the MQL5 twin
    (``mql5/SmcXau_EA.mq5``), which reads shift-2/shift-1/shift-0 on every
    new M1 bar.

    Stage 1 (once): candidate N's close reaches ``entry + stage1_at_r*d``
    (dir-adjusted) -> SL = entry +/- ``be_cost`` (spread+commission, both
    directions). Stage 2 (once, only after stage 1): candidate N's close
    reaches ``entry + stage2_at_r*d`` -> SL = the tighter-of {candle N's
    structural extreme -/+ ``lock_buffer``, a minimum-lock floor at
    ``stage2_min_lock_r*d``} — the floor guarantees a minimum locked profit
    even when the structural level is worse. After stage 2 the SL is
    frozen: no further modification, ever, from this class. Only one stage
    evaluates per bar (elif chaining) — a gap candle that qualifies for
    both stages with stage 1 still pending fires ONLY stage 1 this bar;
    stage 2 waits for its own later qualifying N/N+1 pair. A qualifying
    candidate that is not strictly tighter than the position's CURRENT sl
    (e.g. after a restart where the broker's sl already exceeds the
    target) marks the stage done WITHOUT emitting an action — the level is
    already achieved.

  Restart derivation (no persisted state): on first sight of a ticket, the
  stage flags are derived from its current ``sl`` vs. the computed
  breakeven level (entry +/- be_cost): at/beyond breakeven -> stage 1 done;
  strictly beyond -> stage 2 done too (frozen). ``d`` itself is still
  captured as ``abs(entry - sl)`` (or ``default_d`` if unset) exactly as
  before — a live restart AFTER stage 1 has already moved sl to breakeven
  will therefore under-derive ``d`` (small, wrong denominator inflates
  subsequent partial/stage r). This is a documented, accepted risk: the
  Python live path is a follower of the MQL5 EA (the authoritative master,
  which recovers the true initial stop from deal history via
  ``InitialStopFromHistory``); the backtest sim never restarts mid-position
  so it is unaffected.

  Emission failure: if a stage's flag is marked done and the caller's
  ``broker.modify_sl`` subsequently raises, this class does not retry (the
  pending state is already gone) — rare, tighten-only, and a restart
  re-derives the flag from the broker's actual sl per the paragraph above.

Pure stdlib, sync, no I/O. O(1) per bar, bounded memory (state only for open
tickets; closed tickets swept every ``on_bar``).
"""

from __future__ import annotations

import math

from ..babysitter import LONG, Action
from ..models import Candle

_EPS = 1e-9
_STAGE_TOL = 1e-6


def _snap_down(v: float, step: float) -> float:
    """Floor ``v`` to a multiple of ``step``, guarding float artifacts."""
    return round(math.floor(v / step + _EPS) * step, 10)


class _PosState:
    __slots__ = ("entry", "d", "vol0", "filled", "stage1_done", "stage2_done")

    def __init__(self, entry: float, d: float, vol0: float) -> None:
        self.entry = entry
        self.d = d
        self.vol0 = vol0
        self.filled: set[int] = set()
        self.stage1_done = False
        self.stage2_done = False


class LadderExitManager:
    """Ladder partials + two-stage discrete SL lock, Babysitter contract."""

    __slots__ = ("partial_levels", "final_tp_r", "stage1_at_r", "stage2_at_r",
                 "stage2_min_lock_r", "lock_buffer", "be_cost", "vol_min",
                 "vol_step", "default_d", "_prev", "_last", "_trades")

    def __init__(self, *, partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30)),
                 final_tp_r: float = 10.0, stage1_at_r: float = 1.0,
                 stage2_at_r: float = 2.0, stage2_min_lock_r: float = 1.0,
                 lock_buffer: float = 0.5, be_cost: float = 0.0,
                 vol_min: float = 0.01, vol_step: float = 0.01,
                 default_d: float = 2.0) -> None:
        if vol_step <= 0 or vol_min <= 0:
            raise ValueError("vol_step and vol_min must be > 0")
        if default_d <= 0:
            raise ValueError("default_d must be > 0")
        if not (0.0 < stage1_at_r < stage2_at_r):
            raise ValueError("stage1_at_r must be > 0 and < stage2_at_r")
        if not (0.0 < stage2_min_lock_r <= stage2_at_r):
            raise ValueError("stage2_min_lock_r must be in (0, stage2_at_r]")
        if lock_buffer < 0:
            raise ValueError("lock_buffer must be >= 0")
        if be_cost < 0:
            raise ValueError("be_cost must be >= 0")
        self.partial_levels = tuple(partial_levels)
        self.final_tp_r = final_tp_r
        self.stage1_at_r = stage1_at_r
        self.stage2_at_r = stage2_at_r
        self.stage2_min_lock_r = stage2_min_lock_r
        self.lock_buffer = lock_buffer
        self.be_cost = be_cost
        self.vol_min = vol_min
        self.vol_step = vol_step
        self.default_d = default_d
        self._prev: Candle | None = None   # candidate N
        self._last: Candle | None = None   # most recently observed candle
        self._trades: dict[int, _PosState] = {}

    # ------------------------------------------------------------------ #
    def observe(self, c: Candle) -> None:
        """Feed every closed 1m candle; keeps the N/N+1 confirmation buffer."""
        self._prev, self._last = self._last, c

    # ------------------------------------------------------------------ #
    def _first_sight(self, entry: float, sl: float, long_pos: bool) -> tuple:
        """Derive (stage1_done, stage2_done) from a restart-recovered sl."""
        if not sl:
            return False, False
        be = entry + self.be_cost if long_pos else entry - self.be_cost
        if long_pos:
            s1 = sl >= be - _STAGE_TOL
            s2 = sl > be + _STAGE_TOL
        else:
            s1 = sl <= be + _STAGE_TOL
            s2 = sl < be - _STAGE_TOL
        return s1, s2

    # ------------------------------------------------------------------ #
    def _stage_candidate(self, st: "_PosState", long_pos: bool) -> tuple:
        """(candidate_sl, reason) from candle N, or (None, "") if no stage
        qualifies this bar. elif-chained: never both in one call, so at
        most one modification is proposed per bar."""
        cn = self._prev
        if cn is None:
            return None, ""
        n_close = cn.close
        profit_n = (n_close - st.entry) if long_pos else (st.entry - n_close)
        r_n = profit_n / st.d

        if not st.stage1_done:
            if r_n >= self.stage1_at_r:
                be = st.entry + self.be_cost if long_pos else st.entry - self.be_cost
                return be, "stage1_be"
            return None, ""
        if not st.stage2_done:
            if r_n >= self.stage2_at_r:
                structural = (cn.low - self.lock_buffer if long_pos
                              else cn.high + self.lock_buffer)
                floor = (st.entry + self.stage2_min_lock_r * st.d if long_pos
                         else st.entry - self.stage2_min_lock_r * st.d)
                cand = max(structural, floor) if long_pos else min(structural, floor)
                return cand, "stage2_lock"
        return None, ""

    # ------------------------------------------------------------------ #
    def on_bar(self, positions, close: float) -> list[Action]:
        """positions: iterable with .ticket .type .volume .price_open .sl"""
        actions: list[Action] = []
        seen = set()
        for p in positions:
            seen.add(p.ticket)
            st = self._trades.get(p.ticket)
            long_pos = p.type == LONG
            if st is None:
                d = abs(p.price_open - p.sl) if p.sl else self.default_d
                if d <= 0:                     # SL at entry: unusable distance
                    d = self.default_d
                st = self._trades[p.ticket] = _PosState(p.price_open, d,
                                                        p.volume)
                st.stage1_done, st.stage2_done = self._first_sight(
                    p.price_open, p.sl or 0.0, long_pos)
            profit = (close - st.entry) if long_pos else (st.entry - close)
            r = profit / st.d

            # 1. partial rungs, in order, cumulative residual this bar
            emitted = 0.0
            for i, (r_lvl, frac) in enumerate(self.partial_levels):
                if i in st.filled or r < r_lvl:
                    continue
                st.filled.add(i)               # once, fillable or not
                vol = _snap_down(st.vol0 * frac, self.vol_step)
                residual = p.volume - emitted - vol
                if vol < self.vol_min - _EPS or residual < self.vol_min - _EPS:
                    continue                   # fail-safe: skip, keep managing
                actions.append(Action("partial_close", p.ticket, volume=vol))
                emitted += vol

            # 2. final target: close the remainder, forget the ticket
            if self.final_tp_r > 0 and r >= self.final_tp_r:
                remainder = p.volume - emitted
                if remainder > _EPS:
                    actions.append(Action("partial_close", p.ticket,
                                          volume=remainder))
                del self._trades[p.ticket]
                continue

            # 3. two-stage discrete SL: candle-N/N+1 confirmed, capped at 2
            cand, reason = self._stage_candidate(st, long_pos)
            if cand is not None:
                cur = p.sl or 0.0
                tighter = (cur == 0.0 or
                           (long_pos and cand > cur) or
                           (not long_pos and cand < cur))
                if reason == "stage1_be":
                    st.stage1_done = True
                else:
                    st.stage2_done = True
                if tighter:
                    actions.append(Action("update_sl", p.ticket, sl=cand,
                                          reason=reason))

        # forget closed tickets
        for t in list(self._trades):
            if t not in seen:
                del self._trades[t]
        return actions
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `python -m pytest tests/test_smc_exits.py -v`
Expected: PASS — all 18 tests (or however many were written in Task 2).

- [ ] **Step 3: Commit**

```bash
git add orb/smc/exits.py
git commit -m "feat(smc): two-stage discrete SL advancement, drop continuous trail

LadderExitManager: replaces the swing/ATR continuous trail with a
strictly bounded two-stage SL lock (breakeven+costs, then one
structural-or-floor advancement, then frozen forever), gated on N/N+1
closed-candle confirmation. Drops TimeframeAggregator/StructureTracker/
WilderATR — the manager now only buffers the last two observed candles."
```

---

### Task 4: `SmcConfig` field delta (`orb/smc/config.py` + tests)

**Files:**
- Modify: `orb/smc/config.py:62-66` (fields), `:85` (validation loop), `:90-91` (trail_mode check), `:114-119` (be_at_r/trail_start_r checks)
- Modify: `tests/test_smc_config.py`

**Interfaces:**
- Produces: `SmcConfig` fields `stage1_at_r: float = 1.0`, `stage2_at_r: float = 2.0`, `stage2_min_lock_r: float = 1.0`, `lock_buffer: float = 0.5`, `be_cost: float = 0.17` (replacing `be_at_r`, `trail_start_r`, `trail_mode`, `trail_atr_mult`, `trail_buffer`).

- [ ] **Step 1: Update the failing/changed config tests**

In `tests/test_smc_config.py`, replace the `assert cfg.trail_mode == "swing"` line in `test_defaults_construct` with:

```python
    assert cfg.stage1_at_r == 1.0
    assert cfg.stage2_at_r == 2.0
    assert cfg.stage2_min_lock_r == 1.0
    assert cfg.lock_buffer == 0.5
    assert cfg.be_cost == 0.17
```

In the `test_invalid_raises` parametrize list, replace:

```python
    {"trail_atr_mult": 0.0},
```
```python
    {"trail_buffer": -0.1},
```
```python
    # trail_mode enum
    {"trail_mode": "fixed"},
    {"trail_mode": ""},
```
```python
    # be_at_r / trail_start_r > 0
    {"be_at_r": 0.0},
    {"be_at_r": -1.0},
    {"trail_start_r": 0.0},
    {"trail_start_r": -0.5},
```

with:

```python
    # stage1_at_r > 0 and < stage2_at_r
    {"stage1_at_r": 0.0},
    {"stage1_at_r": -1.0},
    {"stage1_at_r": 2.0, "stage2_at_r": 2.0},
    {"stage1_at_r": 3.0, "stage2_at_r": 2.0},
    # stage2_min_lock_r in (0, stage2_at_r]
    {"stage2_min_lock_r": 0.0},
    {"stage2_min_lock_r": -1.0},
    {"stage2_min_lock_r": 2.5, "stage2_at_r": 2.0},
    # lock_buffer / be_cost >= 0
    {"lock_buffer": -0.1},
    {"be_cost": -0.1},
```

In the `test_valid_edges` parametrize list, replace:

```python
    {"trail_mode": "atr"},
    {"trail_start_r": 1.0, "be_at_r": 2.0},                  # independent, both > 0
```

with:

```python
    {"stage2_min_lock_r": 2.0, "stage2_at_r": 2.0},          # == stage2_at_r, allowed
    {"lock_buffer": 0.0},
    {"be_cost": 0.0},
    {"stage1_at_r": 0.5, "stage2_at_r": 5.0},
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_smc_config.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'stage1_at_r'` and `AttributeError` on `cfg.stage1_at_r` (fields don't exist yet).

- [ ] **Step 3: Apply the field delta to `SmcConfig`**

In `orb/smc/config.py`, replace:

```python
    # exits (ladder): ((r_multiple, close_fraction), ...) ascending in r
    partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30))
    final_tp_r: float = 10.0         # 0 = no final TP (runner trails out)
    be_at_r: float = 2.0             # move stop to entry at this R
    trail_start_r: float = 2.0       # start trailing at this R (independent of BE)
    trail_mode: str = "swing"        # "swing" | "atr"
    trail_atr_mult: float = 2.5
    trail_buffer: float = 0.5        # beyond the trailed swing
```

with:

```python
    # exits (ladder): ((r_multiple, close_fraction), ...) ascending in r
    partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30))
    final_tp_r: float = 10.0         # 0 = no final TP (runner trails out)
    # two-stage discrete SL lock (replaces continuous trailing, D-029)
    stage1_at_r: float = 1.0         # stage 1: lock breakeven+costs at this R
    stage2_at_r: float = 2.0         # stage 2: lock structural/floor at this R (frozen after)
    stage2_min_lock_r: float = 1.0   # stage 2 minimum locked profit floor, in R
    lock_buffer: float = 0.5         # $ beyond the stage-2 structural extreme
    be_cost: float = 0.17            # $ = spread + commission/lot-value (real XAUUSD basis)
```

Then replace:

```python
        for nm in ("disp_atr_mult", "vol_mult", "trail_atr_mult"):
```

with:

```python
        for nm in ("disp_atr_mult", "vol_mult"):
```

Then replace:

```python
        if self.trail_mode not in ("swing", "atr"):
            raise SmcConfigError("trail_mode must be 'swing' or 'atr'")
```

by deleting it (no replacement — the trail mode concept no longer exists).

Then replace:

```python
        if self.be_at_r <= 0:
            raise SmcConfigError("be_at_r must be > 0")
        if self.trail_start_r <= 0:
            raise SmcConfigError("trail_start_r must be > 0")
        if self.trail_buffer < 0:
            raise SmcConfigError("trail_buffer must be >= 0")
```

with:

```python
        if not (0.0 < self.stage1_at_r < self.stage2_at_r):
            raise SmcConfigError("stage1_at_r must be > 0 and < stage2_at_r")
        if not (0.0 < self.stage2_min_lock_r <= self.stage2_at_r):
            raise SmcConfigError("stage2_min_lock_r must be in (0, stage2_at_r]")
        if self.lock_buffer < 0:
            raise SmcConfigError("lock_buffer must be >= 0")
        if self.be_cost < 0:
            raise SmcConfigError("be_cost must be >= 0")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_smc_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add orb/smc/config.py tests/test_smc_config.py
git commit -m "feat(smc): SmcConfig two-stage exit fields, drop trail fields

be_at_r/trail_start_r/trail_mode/trail_atr_mult/trail_buffer ->
stage1_at_r/stage2_at_r/stage2_min_lock_r/lock_buffer/be_cost."
```

---

### Task 5: `orb/cli.py` wiring (config assembly, ladder construction, flags)

**Files:**
- Modify: `orb/cli.py:200-207` (`build_smc_config`), `:682-693` (`LadderExitManager` construction + banner), `:866-887` (`_add_smc_flags`)
- Modify: `tests/test_smc_cli.py`

**Interfaces:**
- Consumes: `SmcConfig` (Task 4), `LadderExitManager` (Task 3).
- Produces: `--smc-stage1-r`, `--smc-stage2-r`, `--smc-min-lock-r`, `--smc-lock-buffer`, `--smc-be-cost` CLI flags (replacing `--smc-trail-mode`).

- [ ] **Step 1: Update `tests/test_smc_cli.py`**

Replace the `_smc_args` helper:

```python
def _smc_args(**ov):
    base = dict(session_len=None, smc_min_confluences=None, smc_risk_pct=None,
                smc_disp_atr_mult=None, smc_poc_tol=None, smc_stop_max_dist=None,
                smc_max_trades_per_day=None, smc_trail_mode=None, smc_final_tp_r=None,
                long_only=False, short_only=False, session_open=None)
    base.update(ov)
    return Namespace(**base)
```

with:

```python
def _smc_args(**ov):
    base = dict(session_len=None, smc_min_confluences=None, smc_risk_pct=None,
                smc_disp_atr_mult=None, smc_poc_tol=None, smc_stop_max_dist=None,
                smc_max_trades_per_day=None, smc_stage1_r=None, smc_stage2_r=None,
                smc_min_lock_r=None, smc_lock_buffer=None, smc_be_cost=None,
                smc_final_tp_r=None,
                long_only=False, short_only=False, session_open=None)
    base.update(ov)
    return Namespace(**base)
```

Replace `test_build_smc_config_maps_flags`:

```python
def test_build_smc_config_maps_flags():
    cfg = build_smc_config(_smc_args(
        smc_min_confluences=4, smc_risk_pct=1.5, smc_disp_atr_mult=1.5,
        smc_poc_tol=3.0, smc_stop_max_dist=12.0, smc_max_trades_per_day=1,
        smc_trail_mode="atr", smc_final_tp_r=8.0))
    assert cfg.min_confluences == 4 and cfg.risk_pct == 1.5
    assert cfg.disp_atr_mult == 1.5 and cfg.poc_tol == 3.0
    assert cfg.stop_max_dist == 12.0 and cfg.max_trades_per_day == 1
    assert cfg.trail_mode == "atr" and cfg.final_tp_r == 8.0
```

with:

```python
def test_build_smc_config_maps_flags():
    cfg = build_smc_config(_smc_args(
        smc_min_confluences=4, smc_risk_pct=1.5, smc_disp_atr_mult=1.5,
        smc_poc_tol=3.0, smc_stop_max_dist=12.0, smc_max_trades_per_day=1,
        smc_stage1_r=0.5, smc_stage2_r=1.5, smc_min_lock_r=0.5,
        smc_lock_buffer=0.25, smc_be_cost=0.2, smc_final_tp_r=8.0))
    assert cfg.min_confluences == 4 and cfg.risk_pct == 1.5
    assert cfg.disp_atr_mult == 1.5 and cfg.poc_tol == 3.0
    assert cfg.stop_max_dist == 12.0 and cfg.max_trades_per_day == 1
    assert cfg.stage1_at_r == 0.5 and cfg.stage2_at_r == 1.5
    assert cfg.stage2_min_lock_r == 0.5 and cfg.lock_buffer == 0.25
    assert cfg.be_cost == 0.2 and cfg.final_tp_r == 8.0
```

Replace `test_build_smc_config_defaults`:

```python
def test_build_smc_config_defaults():
    cfg = build_smc_config(_smc_args())
    d = SmcConfig()
    assert cfg.min_confluences == d.min_confluences
    assert cfg.risk_pct == d.risk_pct
    assert cfg.trail_mode == d.trail_mode
    assert cfg.final_tp_r == d.final_tp_r
```

with:

```python
def test_build_smc_config_defaults():
    cfg = build_smc_config(_smc_args())
    d = SmcConfig()
    assert cfg.min_confluences == d.min_confluences
    assert cfg.risk_pct == d.risk_pct
    assert cfg.stage1_at_r == d.stage1_at_r
    assert cfg.stage2_at_r == d.stage2_at_r
    assert cfg.final_tp_r == d.final_tp_r
```

Replace `test_ladder_exit_manager_built_from_smc_config`:

```python
def test_ladder_exit_manager_built_from_smc_config():
    cfg = SmcConfig()
    ladder = LadderExitManager(
        partial_levels=cfg.partial_levels, final_tp_r=cfg.final_tp_r,
        be_at_r=cfg.be_at_r, trail_start_r=cfg.trail_start_r,
        trail_mode=cfg.trail_mode, trail_atr_mult=cfg.trail_atr_mult,
        trail_buffer=cfg.trail_buffer, swing_lookback=cfg.swing_lookback,
        atr_period=cfg.atr_period, trail_tf_min=cfg.trigger_tf_min)
    # observe() is the smc-specific method absent on Babysitter
    assert hasattr(ladder, "observe")
    ladder.observe(Candle(ts=TS, open=2000.0, high=2001.0, low=1999.0,
                          close=2000.5, volume=10.0))
    # no positions -> no actions, clean pass
    assert ladder.on_bar([], 2000.5) == []
```

with:

```python
def test_ladder_exit_manager_built_from_smc_config():
    cfg = SmcConfig()
    ladder = LadderExitManager(
        partial_levels=cfg.partial_levels, final_tp_r=cfg.final_tp_r,
        stage1_at_r=cfg.stage1_at_r, stage2_at_r=cfg.stage2_at_r,
        stage2_min_lock_r=cfg.stage2_min_lock_r, lock_buffer=cfg.lock_buffer,
        be_cost=cfg.be_cost)
    # observe() is the smc-specific method absent on Babysitter
    assert hasattr(ladder, "observe")
    ladder.observe(Candle(ts=TS, open=2000.0, high=2001.0, low=1999.0,
                          close=2000.5, volume=10.0))
    # no positions -> no actions, clean pass
    assert ladder.on_bar([], 2000.5) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_smc_cli.py -v`
Expected: FAIL — `build_smc_config` still maps `trail_mode`, and `LadderExitManager` still expects the old kwargs (`TypeError`).

- [ ] **Step 3: Update `build_smc_config`**

In `orb/cli.py`, replace:

```python
    setif("max_trades_per_day", getattr(args, "smc_max_trades_per_day", None))
    setif("trail_mode", getattr(args, "smc_trail_mode", None))
    setif("final_tp_r", getattr(args, "smc_final_tp_r", None))
    return SmcConfig(**base)
```

with:

```python
    setif("max_trades_per_day", getattr(args, "smc_max_trades_per_day", None))
    setif("stage1_at_r", getattr(args, "smc_stage1_r", None))
    setif("stage2_at_r", getattr(args, "smc_stage2_r", None))
    setif("stage2_min_lock_r", getattr(args, "smc_min_lock_r", None))
    setif("lock_buffer", getattr(args, "smc_lock_buffer", None))
    setif("be_cost", getattr(args, "smc_be_cost", None))
    setif("final_tp_r", getattr(args, "smc_final_tp_r", None))
    return SmcConfig(**base)
```

- [ ] **Step 4: Update the live `LadderExitManager` construction + banner**

Replace:

```python
            sitter = LadderExitManager(
                partial_levels=smc_cfg.partial_levels,
                final_tp_r=smc_cfg.final_tp_r, be_at_r=smc_cfg.be_at_r,
                trail_start_r=smc_cfg.trail_start_r, trail_mode=smc_cfg.trail_mode,
                trail_atr_mult=smc_cfg.trail_atr_mult,
                trail_buffer=smc_cfg.trail_buffer,
                swing_lookback=smc_cfg.swing_lookback, atr_period=smc_cfg.atr_period,
                trail_tf_min=smc_cfg.trigger_tf_min)
            print(f"# ladder exits: partials {smc_cfg.partial_levels} "
                  f"final={smc_cfg.final_tp_r}R be={smc_cfg.be_at_r}R "
                  f"trail={smc_cfg.trail_mode}@{smc_cfg.trail_start_r}R",
                  file=sys.stderr)
```

with:

```python
            sitter = LadderExitManager(
                partial_levels=smc_cfg.partial_levels,
                final_tp_r=smc_cfg.final_tp_r,
                stage1_at_r=smc_cfg.stage1_at_r, stage2_at_r=smc_cfg.stage2_at_r,
                stage2_min_lock_r=smc_cfg.stage2_min_lock_r,
                lock_buffer=smc_cfg.lock_buffer, be_cost=smc_cfg.be_cost)
            print(f"# ladder exits: partials {smc_cfg.partial_levels} "
                  f"final={smc_cfg.final_tp_r}R stage1={smc_cfg.stage1_at_r}R "
                  f"stage2={smc_cfg.stage2_at_r}R(min_lock={smc_cfg.stage2_min_lock_r}R) "
                  f"be_cost={smc_cfg.be_cost}",
                  file=sys.stderr)
```

- [ ] **Step 5: Update `_add_smc_flags`**

Replace:

```python
    p.add_argument("--smc-trail-mode", dest="smc_trail_mode",
                   choices=("swing", "atr"),
                   help="SMC ladder trail: swing (default) or atr")
    p.add_argument("--smc-final-tp-r", dest="smc_final_tp_r", type=float,
                   help="SMC ladder final take-profit in R; 0 = runner trails out "
                        "(default 10.0)")
```

with:

```python
    p.add_argument("--smc-stage1-r", dest="smc_stage1_r", type=float,
                   help="SMC stage-1 SL lock (breakeven+costs) trigger in R "
                        "(default 1.0)")
    p.add_argument("--smc-stage2-r", dest="smc_stage2_r", type=float,
                   help="SMC stage-2 SL lock (structural, frozen after) trigger "
                        "in R (default 2.0)")
    p.add_argument("--smc-min-lock-r", dest="smc_min_lock_r", type=float,
                   help="SMC stage-2 minimum locked-profit floor in R (default 1.0)")
    p.add_argument("--smc-lock-buffer", dest="smc_lock_buffer", type=float,
                   help="SMC stage-2 structural level buffer, $ (default 0.5)")
    p.add_argument("--smc-be-cost", dest="smc_be_cost", type=float,
                   help="SMC stage-1 breakeven cost buffer, $ = spread + "
                        "commission/lot-value (default 0.17)")
    p.add_argument("--smc-final-tp-r", dest="smc_final_tp_r", type=float,
                   help="SMC ladder final take-profit in R; 0 = runner trails out "
                        "(default 10.0)")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_smc_cli.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add orb/cli.py tests/test_smc_cli.py
git commit -m "feat(cli): wire two-stage SMC exit flags, drop --smc-trail-mode"
```

---

### Task 6: `scripts/sim_realistic.py` wiring (backtest harness)

**Files:**
- Modify: `scripts/sim_realistic.py:356-390` (`run_smc`), `:531-548` (argparse flags), `:608-627` (`smc_ov` dict)
- Modify: `tests/test_sim_smc.py`

**Interfaces:**
- Consumes: `SmcConfig`, `LadderExitManager` (Tasks 3-4).
- Produces: `run_smc(..., **smc_ov)` still accepts and filters arbitrary `SmcConfig`-field overrides; `be_cost` auto-derives from `spread + comm/value_per_move` unless the caller passes `be_cost` explicitly via `smc_ov`.

- [ ] **Step 1: Add the smoke test**

Append to `tests/test_sim_smc.py` (after `test_run_smc_accepts_overrides`):

```python
def test_run_smc_accepts_stage_overrides():
    candles = _trending_candles()
    closed = sr.run_smc(candles, spread=1.10, comm=7.0, start_balance=1000.0,
                        stage1_at_r=0.5, stage2_at_r=1.5, stage2_min_lock_r=0.5,
                        lock_buffer=0.25)
    assert isinstance(closed, list)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_sim_smc.py -k stage_overrides -v`
Expected: FAIL — `SmcConfigError` / `TypeError` (fields don't exist on `SmcConfig` yet from this script's call path... actually Task 4 already added them; this will fail here only if Task 6's `run_smc` still filters them out incorrectly or if run before Task 4 — run this AFTER Task 4 is done, so the real failure mode is `LadderExitManager.__init__()` still expecting old kwargs). Expected: FAIL with `TypeError` from the old `LadderExitManager(...)` call inside `run_smc`.

- [ ] **Step 3: Update `run_smc`**

Replace:

```python
def run_smc(candles: list[Candle], *, risk_pct: float = 2.0, spread: float = 1.10,
            comm: float = 7.0, max_daily_loss_pct: float = 10.0,
            start_balance: float = 1000.0,
            value_per_move: float = USD_PER_LOT_PER_DOLLAR,
            vol_min: float = 0.01, vol_step: float = 0.01, vol_max: float = 50.0,
            **smc_ov) -> list[dict]:
    """Execution-true SMC backtest: multi-timeframe Smart Money Concepts A+
    entries (M15 decision / H4 bias / D1 veto), structural stops, dynamic
    risk_pct sizing, ladder exits (partials + BE + swing/ATR trail). Byte-for-byte
    harness parity with :func:`run_svp` — only the engine and the exit sitter
    differ; the Sim, spike-cancel, and daily-halt plumbing are identical.

    ``smc_ov`` is filtered to valid SmcConfig fields, so unrelated kwargs are
    dropped rather than raising. The new market entry is opened AFTER on_bar so
    it is never SL-checked on its own bar.
    """
    from dataclasses import fields

    from orb.smc import SmcConfig, SmcEngine
    from orb.smc.exits import LadderExitManager
    from orb.svp import compute_lot

    valid = {f.name for f in fields(SmcConfig)}
    cfg = SmcConfig(risk_pct=risk_pct,
                    **{k: v for k, v in smc_ov.items() if k in valid})
    engine = SmcEngine(cfg)
    sim = Sim(qty=0.0, spread=spread, commission_rt=comm,
              value_per_move=value_per_move)
    sitter = LadderExitManager(
        partial_levels=cfg.partial_levels, final_tp_r=cfg.final_tp_r,
        be_at_r=cfg.be_at_r, trail_start_r=cfg.trail_start_r,
        trail_mode=cfg.trail_mode, trail_atr_mult=cfg.trail_atr_mult,
        trail_buffer=cfg.trail_buffer, swing_lookback=cfg.swing_lookback,
        atr_period=cfg.atr_period, trail_tf_min=cfg.trigger_tf_min,
        vol_min=vol_min, vol_step=vol_step)
    spike = SpikeCancel(ratio=2.5)
```

with:

```python
def run_smc(candles: list[Candle], *, risk_pct: float = 2.0, spread: float = 1.10,
            comm: float = 7.0, max_daily_loss_pct: float = 10.0,
            start_balance: float = 1000.0,
            value_per_move: float = USD_PER_LOT_PER_DOLLAR,
            vol_min: float = 0.01, vol_step: float = 0.01, vol_max: float = 50.0,
            **smc_ov) -> list[dict]:
    """Execution-true SMC backtest: multi-timeframe Smart Money Concepts A+
    entries (M15 decision / H4 bias / D1 veto), structural stops, dynamic
    risk_pct sizing, ladder exits (partials + two-stage discrete SL lock).
    Byte-for-byte harness parity with :func:`run_svp` — only the engine and
    the exit sitter differ; the Sim, spike-cancel, and daily-halt plumbing
    are identical.

    ``smc_ov`` is filtered to valid SmcConfig fields, so unrelated kwargs are
    dropped rather than raising. ``be_cost`` auto-derives from
    ``spread + comm/value_per_move`` unless the caller overrides it via
    ``smc_ov``. The new market entry is opened AFTER on_bar so it is never
    SL-checked on its own bar.
    """
    from dataclasses import fields

    from orb.smc import SmcConfig, SmcEngine
    from orb.smc.exits import LadderExitManager
    from orb.svp import compute_lot

    valid = {f.name for f in fields(SmcConfig)}
    smc_kw = {k: v for k, v in smc_ov.items() if k in valid}
    smc_kw.setdefault("be_cost", spread + comm / value_per_move)
    cfg = SmcConfig(risk_pct=risk_pct, **smc_kw)
    engine = SmcEngine(cfg)
    sim = Sim(qty=0.0, spread=spread, commission_rt=comm,
              value_per_move=value_per_move)
    sitter = LadderExitManager(
        partial_levels=cfg.partial_levels, final_tp_r=cfg.final_tp_r,
        stage1_at_r=cfg.stage1_at_r, stage2_at_r=cfg.stage2_at_r,
        stage2_min_lock_r=cfg.stage2_min_lock_r, lock_buffer=cfg.lock_buffer,
        be_cost=cfg.be_cost, vol_min=vol_min, vol_step=vol_step)
    spike = SpikeCancel(ratio=2.5)
```

- [ ] **Step 4: Update the argparse flags**

Replace:

```python
    ap.add_argument("--smc-trail-mode", dest="smc_trail_mode",
                    choices=("swing", "atr"),
                    help="runner trail: swing structure or ATR (default swing)")
```

with:

```python
    ap.add_argument("--smc-stage1-r", dest="smc_stage1_r", type=float,
                    help="stage-1 SL lock (breakeven+costs) trigger in R "
                         "(default 1.0)")
    ap.add_argument("--smc-stage2-r", dest="smc_stage2_r", type=float,
                    help="stage-2 SL lock (structural, frozen after) trigger "
                         "in R (default 2.0)")
    ap.add_argument("--smc-min-lock-r", dest="smc_min_lock_r", type=float,
                    help="stage-2 minimum locked-profit floor in R (default 1.0)")
    ap.add_argument("--smc-lock-buffer", dest="smc_lock_buffer", type=float,
                    help="stage-2 structural level buffer, $ (default 0.5)")
```

- [ ] **Step 5: Update the `smc_ov` dict in `main()`**

Replace:

```python
        smc_ov = {k: v for k, v in (
            ("min_confluences", args.smc_min_confluences),
            ("disp_atr_mult", args.smc_disp_atr_mult),
            ("poc_tol", args.smc_poc_tol),
            ("stop_max_dist", args.smc_stop_max_dist),
            ("max_trades_per_day", args.smc_max_trades_per_day),
            ("trail_mode", args.smc_trail_mode),
            ("final_tp_r", args.smc_final_tp_r),
        ) if v is not None}
```

with:

```python
        smc_ov = {k: v for k, v in (
            ("min_confluences", args.smc_min_confluences),
            ("disp_atr_mult", args.smc_disp_atr_mult),
            ("poc_tol", args.smc_poc_tol),
            ("stop_max_dist", args.smc_stop_max_dist),
            ("max_trades_per_day", args.smc_max_trades_per_day),
            ("stage1_at_r", args.smc_stage1_r),
            ("stage2_at_r", args.smc_stage2_r),
            ("stage2_min_lock_r", args.smc_min_lock_r),
            ("lock_buffer", args.smc_lock_buffer),
            ("final_tp_r", args.smc_final_tp_r),
        ) if v is not None}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_sim_smc.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/sim_realistic.py tests/test_sim_smc.py
git commit -m "feat(sim): wire two-stage SMC exit flags into the backtest harness

be_cost auto-derives from --spread/--commission unless overridden."
```

---

### Task 7: Full Python suite verification + grep gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, no failures, no errors.

- [ ] **Step 2: Confirm the byte-parity gate on untouched files**

Run: `git diff main -- orb/babysitter.py orb/engine.py orb/svp/`
Expected: empty output (no diff).

- [ ] **Step 3: Confirm the old trail/BE vocabulary is fully gone from the Python side**

Run: `grep -rnE "trail_start_r|trail_mode|trail_atr_mult|trail_buffer|be_at_r" orb/ scripts/ tests/`
Expected: zero hits (note: `orb/engine.py`'s unrelated ORB trail fields use different names and are untouched, so this must come back empty; if it doesn't, something outside the SMC scope was accidentally touched — investigate before proceeding).

- [ ] **Step 4: No commit** (verification-only task; if Step 1 fails, return to the task that introduced the regression, fix it there, and re-run this task from Step 1).

---

### Task 8: EA — inputs, `CostBuffer()`, `InitialStopFromHistory()`, stage-state scaffolding

**Files:**
- Modify: `mql5/SmcXau_EA.mq5:17-19` (header comment), `:56-68` (Entry/exits input group), add a new "Two-stage exit" + "Copy-trade broadcast" input group in their place
- Modify: `mql5/SmcXau_EA.mq5:75-90` (globals) — add `StageState` struct + `g_stage[]` array
- Modify: `mql5/SmcXau_EA.mq5:821-838` — delete `TrailCandidate()`
- Modify: `mql5/SmcXau_EA.mq5:855-870` area — add `InitialStopFromHistory()` next to the existing `OriginalVolumeFromHistory()`

No local MT5 compiler is available in this environment — this task and Tasks 9-10 are verified by careful code review against the MQL5 language reference and the pinned HMAC vector (Task 10), with the actual F7 compile happening in Task 11 (owner-manual).

**Interfaces:**
- Produces: `double CostBuffer()` — live spread + commission-per-lot converted to price units.
- Produces: `double InitialStopFromHistory(ulong position_id, double fallback)` — the position's true initial SL recovered from deal/order history, or `fallback` if unavailable.
- Produces: `struct StageState { ulong ticket; bool s1; bool s2; double d; }` + `StageState g_stage[]`, `int FindStageIndex(ulong ticket)`, `int EnsureStageState(ulong ticket, double entry, double sl_now, int dir, double init_stop)`, `void SweepClosedStageState()`.

- [ ] **Step 1: Update the header comment**

Replace lines 17-19:

```
//|   * Structural SL beyond the invalidation wick / OB far edge;     |
//|     2% risk sizing; layered partials 5R/7R + 10R runner; BE lock  |
//|     and swing/ATR trail, both armed only at +2R; tighten-only.    |
```

with:

```
//|   * Structural SL beyond the invalidation wick / OB far edge;     |
//|     2% risk sizing; layered partials 5R/7R + 10R runner; two-     |
//|     stage discrete SL lock (BE+costs at stage1, structural-or-    |
//|     floor lock at stage2, frozen after) — N+1-confirmed on M1,    |
//|     tighten-only, capped at exactly 2 modifications per trade.    |
//|     Every SL modification broadcasts to the copy-trade leader     |
//|     over HTTP (non-blocking, HMAC-signed) when InpBroadcastUrl    |
//|     is set.                                                       |
```

- [ ] **Step 2: Replace the "Entry / exits" trail inputs with the two-stage + broadcast groups**

Replace:

```
input double  InpBeAtR            = 2.0;        // lock breakeven at this R
input double  InpTrailStartR      = 2.0;        // start trailing at this R
input int     InpTrailMode        = 0;          // 0 = swing, 1 = ATR
input double  InpTrailAtrMult      = 2.5;
input double  InpTrailBuffer       = 0.5;

input group "Misc"
```

with:

```
input group "Two-stage exit"
input double  InpStage1AtR        = 1.0;        // stage 1: lock breakeven+costs at this R
input double  InpStage2AtR        = 2.0;        // stage 2: lock structural level at this R (frozen after)
input double  InpMinLockR         = 1.0;        // stage 2 minimum locked profit floor, in R
input double  InpLockBuffer       = 0.5;        // $ beyond the stage-2 structural extreme
input double  InpCommissionPerLot = 7.0;        // $ round-trip commission per 1.0 lot (breakeven cost basis)

input group "Copy-trade broadcast"
input string  InpBroadcastUrl       = "";       // leader endpoint e.g. http://127.0.0.1:8787/events (empty = disabled)
input string  InpBroadcastSecret    = "";       // HMAC shared secret (must match COPYTRADE_SECRET on the leader)
input int     InpBroadcastTimeoutMs = 1500;     // WebRequest timeout per attempt, ms
input string  InpNodeLabel          = "smc-ea"; // source.node label in broadcast events

input group "Misc"
```

(This leaves `InpPartialR1`/`InpPartialFrac1`/`InpPartialR2`/`InpPartialFrac2`/`InpFinalTpR` untouched immediately above.)

- [ ] **Step 3: Add the `StageState` scaffolding to the globals block**

After the existing globals (`g_last_m15_bar`, `g_last_m1_bar`, `g_cur_day`, `g_trades_today`, `g_day_start_bal`, `g_day_halted`), add:

```
// Per-position two-stage exit state, recomputed on first sight after a
// restart (see EnsureStageState) — never persisted to disk.
struct StageState { ulong ticket; bool s1; bool s2; double d; };
StageState g_stage[];
```

- [ ] **Step 4: Delete `TrailCandidate()`**

Delete the entire function:

```
double TrailCandidate(int dir, double px)
{
   if(InpTrailMode == 1)   // ATR
   {
      double atr = AtrOnTf(PERIOD_M15, InpAtrPeriod);
      if(atr <= 0.0) return EMPTY_VALUE;
      return (dir == DIR_LONG) ? px - InpTrailAtrMult*atr : px + InpTrailAtrMult*atr;
   }
   // swing: last confirmed M15 swing +/- buffer
   StructResult m15 = ScanStructure(PERIOD_M15, InpLookbackBars);
   if(dir == DIR_LONG)
   {
      if(!m15.last_low.valid) return EMPTY_VALUE;
      return m15.last_low.price - InpTrailBuffer;
   }
   if(!m15.last_high.valid) return EMPTY_VALUE;
   return m15.last_high.price + InpTrailBuffer;
}
```

`Tighter()` and `IsStrictlyTighter()` immediately below it are kept as-is (still used by Task 9's rewrite).

- [ ] **Step 5: Add `CostBuffer()` and `InitialStopFromHistory()`**

Immediately after the existing `OriginalVolumeFromHistory()` function (in the "Deal-history helpers" section), add:

```
//====================================================================
//  Breakeven cost buffer: live spread + commission, price units.
//  cost = spread + commission_per_lot / (tick_value/tick_size)
//====================================================================
double CostBuffer()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread = ask - bid;
   double tick_val  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double val_per_price = (tick_size > 0.0) ? tick_val / tick_size : 0.0;
   double comm_price = (val_per_price > 0.0) ? InpCommissionPerLot / val_per_price : 0.0;
   return spread + comm_price;
}

//====================================================================
//  Recover the position's TRUE initial stop from deal/order history.
//  Fixes the classic bug: after stage 1 moves sl to breakeven, reading
//  the CURRENT sl for d = |entry-sl| collapses the R denominator and
//  makes every later partial/stage fire almost immediately.
//====================================================================
double InitialStopFromHistory(ulong position_id, double fallback)
{
   // 1) DEAL_SL of the position-opening deal (authoritative)
   if(HistorySelectByPosition(position_id))
   {
      int deals = HistoryDealsTotal();
      for(int i = 0; i < deals; i++)
      {
         ulong dticket = HistoryDealGetTicket(i);
         if(dticket == 0) continue;
         if(HistoryDealGetInteger(dticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
         {
            double dsl = HistoryDealGetDouble(dticket, DEAL_SL);
            if(dsl > 0.0) return dsl;
            break;
         }
      }
   }
   // 2) fallback: the entry order's originally-requested SL
   if(HistorySelect(0, TimeGMT()))
   {
      int orders = HistoryOrdersTotal();
      for(int i = 0; i < orders; i++)
      {
         ulong oticket = HistoryOrderGetTicket(i);
         if(oticket == 0) continue;
         if((ulong)HistoryOrderGetInteger(oticket, ORDER_POSITION_ID) != position_id) continue;
         ENUM_ORDER_TYPE otype = (ENUM_ORDER_TYPE)HistoryOrderGetInteger(oticket, ORDER_TYPE);
         if(otype != ORDER_TYPE_BUY && otype != ORDER_TYPE_SELL) continue;
         double osl = HistoryOrderGetDouble(oticket, ORDER_SL);
         if(osl > 0.0) return osl;
      }
   }
   // 3) last resort: caller-supplied fallback (current server-side SL)
   return fallback;
}

//====================================================================
//  Per-position stage-flag state, derived on first sight (restart-safe,
//  nothing persisted). tol absorbs float/broker-rounding noise around
//  the breakeven level.
//====================================================================
int FindStageIndex(ulong ticket)
{
   for(int i = 0; i < ArraySize(g_stage); i++)
      if(g_stage[i].ticket == ticket) return i;
   return -1;
}

int EnsureStageState(ulong ticket, double entry, double sl_now, int dir, double init_stop)
{
   int idx = FindStageIndex(ticket);
   if(idx >= 0) return idx;

   double d = MathAbs(entry - init_stop);
   if(d <= 0.0) d = MathAbs(entry - sl_now);   // last-ditch fallback

   double be = (dir == DIR_LONG) ? entry + CostBuffer() : entry - CostBuffer();
   double tol = 2.0 * _Point;
   bool s1, s2;
   if(dir == DIR_LONG)
   {
      s1 = (sl_now > 0.0) && (sl_now >= be - tol);
      s2 = (sl_now > 0.0) && (sl_now >  be + tol);
   }
   else
   {
      s1 = (sl_now > 0.0) && (sl_now <= be + tol);
      s2 = (sl_now > 0.0) && (sl_now <  be - tol);
   }

   StageState st; st.ticket = ticket; st.s1 = s1; st.s2 = s2; st.d = d;
   int sz = ArraySize(g_stage);
   ArrayResize(g_stage, sz + 1);
   g_stage[sz] = st;
   return sz;
}

void SweepClosedStageState()
{
   for(int i = ArraySize(g_stage) - 1; i >= 0; i--)
   {
      if(!PositionSelectByTicket(g_stage[i].ticket))
      {
         int last = ArraySize(g_stage) - 1;
         g_stage[i] = g_stage[last];
         ArrayResize(g_stage, last);
      }
   }
}
```

- [ ] **Step 6: Review checklist (manual, no compiler available)**

- [ ] Every `Inp*` referenced by the deleted trail block (`InpTrailMode`, `InpTrailAtrMult`, `InpTrailBuffer`, `InpTrailStartR`, `InpBeAtR`) no longer appears anywhere else in the file: `grep -n "InpTrailMode\|InpTrailAtrMult\|InpTrailBuffer\|InpTrailStartR\|InpBeAtR" mql5/SmcXau_EA.mq5` must be empty (Task 9 will still reference `InpStage1AtR` etc., which is expected).
- [ ] `StageState`/`g_stage` declared once, before first use.
- [ ] No new `#include` added — only the stock `<Trade/Trade.mqh>` from line 30.

- [ ] **Step 7: Commit**

```bash
git add mql5/SmcXau_EA.mq5
git commit -m "feat(ea): two-stage exit inputs + CostBuffer + InitialStopFromHistory

Scaffolding for the two-stage discrete SL lock: new input groups,
StageState per-position array, breakeven cost helper, and the deal-
history stop recovery that fixes the R-denominator corruption bug.
ManageOpenPositions() itself is rewritten in the next commit."
```

---

### Task 9: EA — rewrite `ManageOpenPositions()` (two-stage SL logic)

**Files:**
- Modify: `mql5/SmcXau_EA.mq5` — replace the entire `ManageOpenPositions()` function (originally lines 754-819, now shifted by Task 8's edits — locate by function name, not line number).

**Interfaces:**
- Consumes: `CostBuffer()`, `InitialStopFromHistory()`, `EnsureStageState()`, `SweepClosedStageState()`, `g_stage[]` (Task 8); `Tighter()`, `IsStrictlyTighter()`, `SnapVol()`, `MinVol()`, `OriginalVolumeFromHistory()` (kept from before).
- Produces: calls `BroadcastModifySl(ulong ticket, int dir, double newsl, string reason)` on every successful `PositionModify` — this function is added in Task 10; declare it as a forward reference is unnecessary in MQL5 (whole-file compilation), but Task 9 alone will **not compile** until Task 10 adds `BroadcastModifySl`. Both tasks land together before Task 11's compile check.

- [ ] **Step 1: Replace `ManageOpenPositions()`**

Replace the full function body:

```
void ManageOpenPositions()
{
   int total = PositionsTotal();
   for(int idx = total-1; idx >= 0; idx--)
   {
      ulong ticket = PositionGetTicket(idx);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long   ptype   = PositionGetInteger(POSITION_TYPE);
      int    dir     = (ptype == POSITION_TYPE_BUY) ? DIR_LONG : DIR_SHORT;
      double entry   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl      = PositionGetDouble(POSITION_SL);
      double vol_now = PositionGetDouble(POSITION_VOLUME);
      double px      = (dir == DIR_LONG)
                       ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                       : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      // risk distance d from the current (server-side) SL; fall back if none
      double d = (sl > 0.0) ? MathAbs(entry - sl) : 0.0;
      if(d <= 0.0) continue;                    // cannot size R without a stop
      double profit = (dir == DIR_LONG) ? (px - entry) : (entry - px);
      double r = profit / d;

      double vol0 = OriginalVolumeFromHistory(ticket, vol_now);

      // ---- partials -------------------------------------------------
      // filled levels are inferred from how much has already been closed.
      double closed_frac = 1.0 - (vol_now / MathMax(vol0, 1e-9));
      bool p1_done = closed_frac >= (InpPartialFrac1 - 1e-6);
      bool p2_done = closed_frac >= (InpPartialFrac1 + InpPartialFrac2 - 1e-6);

      if(InpFinalTpR > 0.0 && r >= InpFinalTpR)
      {
         trade.PositionClosePartial(ticket, vol_now);   // close the runner
         continue;
      }
      if(!p1_done && r >= InpPartialR1)
      {
         double v = SnapVol(vol0 * InpPartialFrac1);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }
      else if(!p2_done && r >= InpPartialR2)
      {
         double v = SnapVol(vol0 * InpPartialFrac2);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }

      // ---- BE lock + trail (both armed only at >= their R) ----------
      double new_sl = sl;
      if(InpBeAtR > 0.0 && r >= InpBeAtR)
         new_sl = Tighter(dir, new_sl, entry);          // breakeven floor

      if(r >= InpTrailStartR)
      {
         double cand = TrailCandidate(dir, px);
         if(cand != EMPTY_VALUE)
            new_sl = Tighter(dir, new_sl, cand);
      }
      // emit modify only when strictly tighter (never widen)
      if(IsStrictlyTighter(dir, sl, new_sl))
         trade.PositionModify(ticket, NormalizeDouble(new_sl, _Digits), 0.0);
   }
}
```

with:

```
void ManageOpenPositions()
{
   if(Bars(_Symbol, PERIOD_M1) < 3) return;      // not enough history yet

   datetime t0 = iTime(_Symbol, PERIOD_M1, 0);
   datetime t1 = iTime(_Symbol, PERIOD_M1, 1);
   bool have_n1 = (t0 > 0 && t1 > 0 && t0 > t1);  // N+1 (shift1) genuinely closed
   double nH = iHigh (_Symbol, PERIOD_M1, 2);      // candidate N (shift 2)
   double nL = iLow  (_Symbol, PERIOD_M1, 2);
   double nC = iClose(_Symbol, PERIOD_M1, 2);

   int total = PositionsTotal();
   for(int idx = total-1; idx >= 0; idx--)
   {
      ulong ticket = PositionGetTicket(idx);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      long   ptype   = PositionGetInteger(POSITION_TYPE);
      int    dir     = (ptype == POSITION_TYPE_BUY) ? DIR_LONG : DIR_SHORT;
      double entry   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl      = PositionGetDouble(POSITION_SL);
      double vol_now = PositionGetDouble(POSITION_VOLUME);
      double px      = (dir == DIR_LONG)
                       ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                       : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double init_stop = InitialStopFromHistory(ticket, sl);
      int sidx = EnsureStageState(ticket, entry, sl, dir, init_stop);
      double d = g_stage[sidx].d;
      if(d <= 0.0) continue;                    // cannot size R without a stop

      double profit = (dir == DIR_LONG) ? (px - entry) : (entry - px);
      double r = profit / d;

      double vol0 = OriginalVolumeFromHistory(ticket, vol_now);

      // ---- partials (unchanged: intra-tick, off the corrected d) ----
      double closed_frac = 1.0 - (vol_now / MathMax(vol0, 1e-9));
      bool p1_done = closed_frac >= (InpPartialFrac1 - 1e-6);
      bool p2_done = closed_frac >= (InpPartialFrac1 + InpPartialFrac2 - 1e-6);

      if(InpFinalTpR > 0.0 && r >= InpFinalTpR)
      {
         trade.PositionClosePartial(ticket, vol_now);   // close the runner
         continue;
      }
      if(!p1_done && r >= InpPartialR1)
      {
         double v = SnapVol(vol0 * InpPartialFrac1);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }
      else if(!p2_done && r >= InpPartialR2)
      {
         double v = SnapVol(vol0 * InpPartialFrac2);
         if(v > 0.0 && (vol_now - v) >= MinVol()) trade.PositionClosePartial(ticket, v);
      }

      // ---- two-stage discrete SL: candle-N/N+1 confirmed, capped at 2 --
      if(!have_n1) continue;                    // no confirmed N yet this run

      double n_profit = (dir == DIR_LONG) ? (nC - entry) : (entry - nC);
      double n_r = n_profit / d;

      double cand = EMPTY_VALUE;
      string reason = "";
      if(!g_stage[sidx].s1)
      {
         if(n_r >= InpStage1AtR)
         {
            cand   = (dir == DIR_LONG) ? entry + CostBuffer() : entry - CostBuffer();
            reason = "stage1_be";
         }
      }
      else if(!g_stage[sidx].s2)
      {
         if(n_r >= InpStage2AtR)
         {
            double structural = (dir == DIR_LONG) ? nL - InpLockBuffer : nH + InpLockBuffer;
            double floor      = (dir == DIR_LONG) ? entry + InpMinLockR * d : entry - InpMinLockR * d;
            cand   = (dir == DIR_LONG) ? MathMax(structural, floor) : MathMin(structural, floor);
            reason = "stage2_lock";
         }
      }
      if(cand == EMPTY_VALUE) continue;

      if(!IsStrictlyTighter(dir, sl, cand))
      {
         // level already achieved (e.g. after a restart) -> mark done, no send
         if(reason == "stage1_be") g_stage[sidx].s1 = true; else g_stage[sidx].s2 = true;
         continue;
      }

      double stops_level  = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL)  * _Point;
      double freeze_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * _Point;
      double min_dist = MathMax(stops_level, freeze_level);
      bool broker_ok = (dir == DIR_LONG) ? (px - cand >= min_dist)
                                         : (cand - px >= min_dist);
      if(!broker_ok) continue;                  // flag stays false, retry next qualifying N

      double newsl = NormalizeDouble(cand, _Digits);
      if(trade.PositionModify(ticket, newsl, 0.0))
      {
         if(reason == "stage1_be") g_stage[sidx].s1 = true; else g_stage[sidx].s2 = true;
         BroadcastModifySl(ticket, dir, newsl, reason);
      }
      // failure (requote/10025/...): flags stay false, clean retry next qualifying N
   }
   SweepClosedStageState();
}
```

- [ ] **Step 2: Review checklist (manual, no compiler available)**

- [ ] `d` for BOTH partials and the stage block now comes from `g_stage[sidx].d` (history-recovered), never from the raw current `sl` — this is the bug fix from the brief.
- [ ] Exactly one of `s1`/`s2` can be set per call (the `if/else if` chain) — matches "one stage per bar."
- [ ] `have_n1` guards the ENTIRE stage block (partials/final are unaffected, still intra-tick).
- [ ] `grep -n "InpBeAtR\|InpTrailStartR\|InpTrailMode\|TrailCandidate" mql5/SmcXau_EA.mq5` is empty (confirms Task 8's deletions are fully unreferenced).

- [ ] **Step 3: Commit**

```bash
git add mql5/SmcXau_EA.mq5
git commit -m "feat(ea): rewrite ManageOpenPositions for the two-stage SL lock

d for partials AND stage triggers now comes from deal-history-recovered
InitialStopFromHistory, fixing the R-denominator corruption bug a
breakeven-moved SL used to cause. Stage triggers are gated on the
closed-candle N/N+1 pair (shift 2 / shift 1 vs shift 0), capped at
exactly 2 modifications, then frozen. Depends on BroadcastModifySl
from the next commit for a clean compile."
```

---

### Task 10: EA — copy-trade broadcast block

**Files:**
- Modify: `mql5/SmcXau_EA.mq5` — add near the end of the file (after `MinVol()`), or any point after `OnInit`/globals: the broadcast queue, HMAC implementation, timer, and `BroadcastModifySl`.
- Modify: `mql5/SmcXau_EA.mq5` — `OnInit()` (self-test + timer registration) and `OnDeinit()` (kill timer).

**Interfaces:**
- Produces: `void BroadcastModifySl(ulong ticket, int dir, double newsl, string reason)` — called by Task 9's `ManageOpenPositions()`.
- Produces: `bool HmacSelfTest()` — pins the reference vector from the Global Constraints section; gates whether the timer/broadcast path is armed at all.

- [ ] **Step 1: Update `OnInit()` and add `OnTimer()`**

Replace:

```
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);   // resolves to IOC on JustMarkets .ecn
   trade.SetDeviationInPoints(20);

   g_cur_day       = DayOfYearUTC(TimeGMT());
   g_day_start_bal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_day_halted    = false;
   g_trades_today  = CountTradesTodayFromHistory();

   if(InpVerbose)
      PrintFormat("SmcXau_EA init: symbol=%s magic=%d risk=%.2f%% minConf=%d",
                  _Symbol, (int)InpMagic, InpRiskPct, InpMinConfluences);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {}
```

with:

```
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);   // resolves to IOC on JustMarkets .ecn
   trade.SetDeviationInPoints(20);

   g_cur_day       = DayOfYearUTC(TimeGMT());
   g_day_start_bal = AccountInfoDouble(ACCOUNT_BALANCE);
   g_day_halted    = false;
   g_trades_today  = CountTradesTodayFromHistory();
   MathSrand((int)TimeLocal());

   g_hmac_ok = HmacSelfTest();
   if(InpBroadcastUrl != "")
   {
      if(!g_hmac_ok && InpVerbose)
         Print("SmcXau_EA: HMAC self-test FAILED -- broadcast disabled");
      else
         EventSetTimer(1);
   }

   if(InpVerbose)
      PrintFormat("SmcXau_EA init: symbol=%s magic=%d risk=%.2f%% minConf=%d",
                  _Symbol, (int)InpMagic, InpRiskPct, InpMinConfluences);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

//====================================================================
//  Copy-trade broadcast: bounded queue, drained by a 1s timer over
//  WebRequest. The trade path only ever calls BroadcastEnqueue() --
//  never network I/O directly, so a slow/unreachable leader can NEVER
//  delay ManageOpenPositions()/TryEnter().
//====================================================================
void OnTimer()
{
   if(InpBroadcastUrl == "" || !g_hmac_ok) return;
   if(MQLInfoInteger(MQL_TESTER)) return;      // WebRequest unavailable in tester
   int sent = 0;
   while(ArraySize(g_bq) > 0 && sent < 3)
   {
      if(!SendSigned(g_bq[0])) break;          // failure: keep head queued, stop this tick
      int n = ArraySize(g_bq);
      for(int i = 1; i < n; i++) g_bq[i-1] = g_bq[i];
      ArrayResize(g_bq, n-1);
      sent++;
   }
}
```

- [ ] **Step 2: Add the broadcast globals**

Immediately after the `StageState g_stage[];` line added in Task 8, add:

```
// Copy-trade broadcast: bounded drop-oldest queue of pre-built JSON bodies.
#define BQ_CAP 64
string g_bq[];
int    g_seq     = 0;
bool   g_hmac_ok = false;
```

- [ ] **Step 3: Add the queue, HMAC, and event-building functions**

Add near the end of the file (after `MinVol()`):

```
//====================================================================
//  Bounded broadcast queue (drop-oldest at BQ_CAP)
//====================================================================
void BroadcastEnqueue(string json)
{
   int n = ArraySize(g_bq);
   if(n >= BQ_CAP)
   {
      for(int i = 1; i < n; i++) g_bq[i-1] = g_bq[i];
      ArrayResize(g_bq, n-1);
      n--;
   }
   ArrayResize(g_bq, n+1);
   g_bq[n] = json;
}

//====================================================================
//  HMAC-SHA256 per RFC 2104, built on MQL5's raw CryptEncode(SHA256).
//  Verified against the reference vector in HmacSelfTest() below.
//====================================================================
string HmacSha256Hex(string key, string message)
{
   uchar keybytes[];
   int klen = StringToCharArray(key, keybytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   ArrayResize(keybytes, MathMax(klen, 0));

   uchar msgbytes[];
   int mlen = StringToCharArray(message, msgbytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   ArrayResize(msgbytes, MathMax(mlen, 0));

   uchar k[]; ArrayResize(k, 64); ArrayInitialize(k, 0);
   if(ArraySize(keybytes) > 64)
   {
      uchar kh[]; uchar dummy0[];
      CryptEncode(CRYPT_HASH_SHA256, keybytes, dummy0, kh);
      for(int i = 0; i < ArraySize(kh) && i < 64; i++) k[i] = kh[i];
   }
   else
   {
      for(int i = 0; i < ArraySize(keybytes); i++) k[i] = keybytes[i];
   }

   uchar ipad[]; ArrayResize(ipad, 64);
   uchar opad[]; ArrayResize(opad, 64);
   for(int i = 0; i < 64; i++) { ipad[i] = (uchar)(k[i] ^ 0x36); opad[i] = (uchar)(k[i] ^ 0x5C); }

   uchar inner_in[]; ArrayResize(inner_in, 64 + ArraySize(msgbytes));
   for(int i = 0; i < 64; i++) inner_in[i] = ipad[i];
   for(int i = 0; i < ArraySize(msgbytes); i++) inner_in[64+i] = msgbytes[i];

   uchar inner_hash[]; uchar dummy1[];
   CryptEncode(CRYPT_HASH_SHA256, inner_in, dummy1, inner_hash);

   uchar outer_in[]; ArrayResize(outer_in, 64 + ArraySize(inner_hash));
   for(int i = 0; i < 64; i++) outer_in[i] = opad[i];
   for(int i = 0; i < ArraySize(inner_hash); i++) outer_in[64+i] = inner_hash[i];

   uchar final_hash[]; uchar dummy2[];
   CryptEncode(CRYPT_HASH_SHA256, outer_in, dummy2, final_hash);

   string hex = "";
   for(int i = 0; i < ArraySize(final_hash); i++)
      hex += StringFormat("%02x", final_hash[i]);
   return hex;
}

bool HmacSelfTest()
{
   string sig = HmacSha256Hex("test-secret", "1700000000." + "{\"a\":1}");
   bool ok = (sig == "8cb2c3355fca388e9ac2caec004f4d5d7045d74937ab5faad61dc11682247a9f");
   if(InpVerbose) PrintFormat("SmcXau_EA HMAC self-test: %s", ok ? "OK" : "FAILED");
   return ok;
}

//====================================================================
//  Sign and POST one queued JSON body. Never called from the trade
//  path directly -- only from OnTimer(). On any failure the caller
//  keeps the payload queued and retries next tick.
//====================================================================
bool SendSigned(string body)
{
   uchar bodybytes[];
   int blen = StringToCharArray(body, bodybytes, 0, WHOLE_ARRAY, CP_UTF8) - 1;  // strip trailing '\0'
   if(blen < 0) return false;
   ArrayResize(bodybytes, blen);

   long tsec = (long)TimeGMT();
   string ts = IntegerToString(tsec);
   string sig = HmacSha256Hex(InpBroadcastSecret, ts + "." + body);

   string headers = "Content-Type: application/json\r\n"
                    "X-Timestamp: " + ts + "\r\n"
                    "X-Signature: " + sig + "\r\n";
   uchar result[]; string result_headers;
   ResetLastError();
   int rc = WebRequest("POST", InpBroadcastUrl, headers, InpBroadcastTimeoutMs,
                       bodybytes, result, result_headers);
   if(rc == -1)
   {
      if(InpVerbose) PrintFormat("SmcXau_EA broadcast failed err=%d (whitelist %s?)",
                                 GetLastError(), InpBroadcastUrl);
      return false;
   }
   return (rc >= 200 && rc < 300);
}

string RandomHex32()
{
   string hex = "";
   for(int i = 0; i < 32; i++)
      hex += StringFormat("%x", MathRand() % 16);
   return hex;
}

string JsonEscape(string s)
{
   string o = s;
   StringReplace(o, "\\", "\\\\");
   StringReplace(o, "\"", "\\\"");
   return o;
}

string IsoTimestampUTC()
{
   datetime now = TimeGMT();
   MqlDateTime s; TimeToStruct(now, s);
   long usec = (long)(GetMicrosecondCount() % 1000000);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d.%06d+00:00",
                       s.year, s.mon, s.day, s.hour, s.min, s.sec, usec);
}

//====================================================================
//  Build + enqueue one schema-v1 modify_sl event (docs/copytrade_schema.md).
//  Every key present; nulls literal. Called only after a successful
//  trade.PositionModify -- never blocks the trade path (enqueue only).
//====================================================================
void BroadcastModifySl(ulong ticket, int dir, double newsl, string reason)
{
   if(InpBroadcastUrl == "" || !g_hmac_ok) return;

   string base_symbol = _Symbol;
   int dot = StringFind(base_symbol, ".");
   if(dot >= 0) base_symbol = StringSubstr(base_symbol, 0, dot);

   long account = AccountInfoInteger(ACCOUNT_LOGIN);
   string dirstr = (dir == DIR_LONG) ? "long" : "short";
   g_seq++;

   string body = "{"
      + "\"schema_version\":1,"
      + "\"event_id\":\"" + RandomHex32() + "\","
      + "\"seq\":" + IntegerToString(g_seq) + ","
      + "\"ts\":\"" + IsoTimestampUTC() + "\","
      + "\"source\":{\"node\":\"" + JsonEscape(InpNodeLabel) + "\",\"account\":" + IntegerToString(account)
        + ",\"strategy\":\"smc\",\"magic\":" + IntegerToString((int)InpMagic) + "},"
      + "\"symbol\":\"" + JsonEscape(_Symbol) + "\","
      + "\"base_symbol\":\"" + JsonEscape(base_symbol) + "\","
      + "\"action\":\"modify_sl\","
      + "\"ticket\":" + IntegerToString((long)ticket) + ","
      + "\"order\":null,"
      + "\"deal\":null,"
      + "\"direction\":\"" + dirstr + "\","
      + "\"volume\":null,"
      + "\"price_requested\":null,"
      + "\"price_filled\":null,"
      + "\"slippage\":null,"
      + "\"sl\":" + DoubleToString(newsl, _Digits) + ","
      + "\"tp\":null,"
      + "\"reason\":\"" + JsonEscape(reason) + "\","
      + "\"rr_planned\":null,"
      + "\"rr_achieved\":null,"
      + "\"risk_inflation_r\":null,"
      + "\"pnl\":null,"
      + "\"retcode\":" + IntegerToString(trade.ResultRetcode())
      + "}";

   BroadcastEnqueue(body);
}
```

- [ ] **Step 4: Review checklist (manual, no compiler available)**

- [ ] `HmacSha256Hex("test-secret", "1700000000.{\"a\":1}")` traced by hand (or via the offline Python check in Task 11) equals `8cb2c3355fca388e9ac2caec004f4d5d7045d74937ab5faad61dc11682247a9f`.
- [ ] Every field in the schema's field reference table appears in `BroadcastModifySl`'s `body` string, in the exact key names from `docs/copytrade_schema.md` section 1.
- [ ] `WebRequest` is called ONLY from `SendSigned`, which is called ONLY from `OnTimer` — never from `TryEnter()` or `ManageOpenPositions()` (grep: `grep -n "WebRequest" mql5/SmcXau_EA.mq5` shows exactly one call site).
- [ ] `BroadcastEnqueue` is called ONLY from `BroadcastModifySl`, which does no I/O itself.
- [ ] Body bytes signed in `SendSigned` are the exact bytes sent (`bodybytes`, after stripping the `StringToCharArray` trailing `\0`) — never re-serialized between signing and sending.

- [ ] **Step 5: Commit**

```bash
git add mql5/SmcXau_EA.mq5
git commit -m "feat(ea): non-blocking HMAC-signed copy-trade broadcast

Bounded in-memory queue (cap 64, drop-oldest) drained by a 1s OnTimer
over WebRequest, 3 sends/tick max. RFC-2104 HMAC-SHA256 built on
CryptEncode, gated by a self-test pinned to the same vector as
tests/test_broadcast.py. Schema-v1 modify_sl events per
docs/copytrade_schema.md, wired from ManageOpenPositions()'s
successful PositionModify call."
```

---

### Task 11: EA compile verification (owner-manual) + lifecycle docs

**Files:**
- Owner-manual: `mql5/SmcXau_EA.mq5` (compile), `mql5/SmcXau_EA.ex5` (regenerated binary, or removed from the repo — owner's call, do not delete it yourself)
- Modify: `STATUS.md`, `PROGRESS.md`, `DECISIONS.md` (per the repo's `CLAUDE.md` lifecycle protocol)

- [ ] **Step 1: Offline HMAC cross-check (no MT5 needed)**

Run:

```bash
python3 -c "
import hmac, hashlib
sig = hmac.new(b'test-secret', b'1700000000.{\"a\":1}', hashlib.sha256).hexdigest()
print(sig)
assert sig == '8cb2c3355fca388e9ac2caec004f4d5d7045d74937ab5faad61dc11682247a9f'
print('OK')
"
```

Expected: prints the hex digest then `OK`. This is the same message/vector `HmacSelfTest()` checks in-EA — a green run here means the EA's self-test will also pass once compiled (assuming the RFC-2104 construction in Task 10 is implemented correctly).

- [ ] **Step 2: Owner compiles the EA**

Hand off to the owner (cannot be scripted from this environment):
1. Open `mql5/SmcXau_EA.mq5` in MetaEditor.
2. Press F7 (Compile). Expect zero errors, zero warnings.
3. Confirm the Journal tab shows only `<Trade/Trade.mqh>` in the include list — no DLLs.
4. Attach to a DEMO XAUUSD.ecn M15 chart; confirm the Experts log prints `SmcXau_EA HMAC self-test: OK` (or `FAILED` if `InpBroadcastUrl` is empty and the block is skipped — check the `OnInit` gating logic from Task 10 Step 1 prints regardless of URL, so this line should always appear).
5. If `InpBroadcastUrl` is set, whitelist it under *Tools → Options → Expert Advisors → Allow WebRequest for listed URL* before attaching, or `WebRequest` returns `-1`/error 4014.
6. Decide whether to commit the regenerated `mql5/SmcXau_EA.ex5` or remove the stale binary from the repo — either way, do this in its own commit, not mixed with source changes.

- [ ] **Step 3: Optional live round-trip smoke (owner, if a leader is available)**

```bash
export COPYTRADE_SECRET=test-secret
python -m leader --port 8787 --store leader_events.jsonl
```

Whitelist `http://127.0.0.1:8787/events` in MT5, trigger one stage modification (e.g. in strategy-tester-free forward demo), and confirm the leader journal logs `action=modify_sl` and `GET /events/latest` returns it.

- [ ] **Step 4: Update `STATUS.md`**

Append a new dated entry at the top of `STATUS.md` (above the `## 2026-07-04 (latest)` entry), following the file's existing format, summarizing: two-stage discrete SL replaces continuous trailing (Python `orb/smc/exits.py` + MQL5 `mql5/SmcXau_EA.mq5`), the EA `d`-corruption bug fix, the new EA-side copy-trade broadcast, and the current test count from Task 7's `python -m pytest -q` run.

- [ ] **Step 5: Append to `PROGRESS.md`**

Add a dated entry (today's date) describing what was built in Tasks 1-10, what passed (`python -m pytest -q` count), and what's owner-pending (Task 11 Steps 2-3, EA compile + optional live smoke).

- [ ] **Step 6: Append `D-029` to `DECISIONS.md`**

Document: continuous swing/ATR trailing removed in favor of the two-stage discrete SL lock; the N+1 closed-candle confirmation rule; the EA `d`-denominator bug fix (recover initial stop from deal history instead of trusting the live, possibly-already-moved, SL); EA-side broadcast added. Rejected alternatives: intra-candle stage triggers (asymmetric with the owner's explicit closed-candle requirement), unlimited/continuous SL moves (the whole point of the redesign was to bound modifications to exactly two). Mark as **final** (owner-locked via the Q&A referenced in this plan's brief) unless owner revisits.

- [ ] **Step 7: Commit the lifecycle docs**

```bash
git add STATUS.md PROGRESS.md DECISIONS.md
git commit -m "docs: lifecycle sync for the two-stage SMC exit refactor (D-029)"
```

---

## Verification (final, whole-plan)

- [ ] `python -m pytest -q` — full suite green (baseline count + new tests from Tasks 1-2, 4-6).
- [ ] `git diff main -- orb/babysitter.py orb/engine.py orb/svp/` — empty.
- [ ] `grep -rnE "trail_start_r|trail_mode|trail_atr_mult|trail_buffer|be_at_r" orb/ scripts/ tests/` — zero hits.
- [ ] `grep -n "InpBeAtR\|InpTrailStartR\|InpTrailMode\|InpTrailAtrMult\|InpTrailBuffer\|TrailCandidate" mql5/SmcXau_EA.mq5` — zero hits.
- [ ] Backtest smoke: `python scripts/sim_realistic.py data/xauusd_1m_*.csv --strategy smc --spread 1.10 --start-balance 1000` runs clean (trade-count delta vs. the pre-refactor baseline is expected — behavioral change by design).
- [ ] EA compiles clean in MetaEditor (owner, Task 11 Step 2), journal shows the HMAC self-test passing.
