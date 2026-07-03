"""SmcEngine — the multi-timeframe Smart Money Concepts A+ entry state machine.

A sibling of :class:`orb.svp.strategy.SvpEngine` (NOT a subclass): same public
surface (``on_candle`` / ``position`` / ``snapshot`` / ``force_flat`` /
``reset`` / ``replay`` / ``state``) so the async stream and the cli on_bar sync
drive it unchanged, but completely separate logic and state. Pure, sync, stdlib
only. O(1)/bar, bounded memory.

The engine consumes native 1m :class:`orb.models.Candle` bars and internally
aggregates to a trigger timeframe (M15), an HTF (H4) and D1. Only a COMPLETED
M15 bar is a DECISION bar. It emits ENTRY signals with a STRUCTURAL stop (just
beyond the invalidation wick / OB far edge) and ``qty=None`` — position sizing
is injected downstream in cli.on_signal. Exits are owned by the ladder /
babysitter, so the engine only emits EXIT via ``force_flat`` after the broker
closes a position.

State mapping (reuses the ORB :class:`State` enum so cli state checks need no
edit):
    IDLE           warming / dormant — no HTF bias yet or no data
    RANGE_DEFINED  ARMED — bias present, flat, watching the trigger TF
    BREAKOUT       IN_POSITION — a trade is open

Confluence model (6 checks; ``min_confluences`` must be True AND ``htf_poi``
is MANDATORY): htf_poi, ltf_sweep, displacement, cisd, ltf_alignment,
premium_discount. Direction is ALWAYS the HTF bias (never counter-trend).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime

from ..indicators import VolumeSMA, WilderATR
from ..models import (
    Candle,
    Direction,
    OutOfOrderError,
    PositionState,
    Signal,
    SignalKind,
    State,
    StateTransition,
    validate,
)
from ..svp.levels import PriorProfile
from ..svp.profile import VolumeProfile
from .config import SmcConfig
from .mtf import TimeframeAggregator
from .orderblocks import OrderBlockTracker
from .structure import StructureTracker

log = logging.getLogger("orb.smc.strategy")


class SmcEngine:
    def __init__(self, config: SmcConfig, on_transition=None, on_signal=None) -> None:
        self.config = config
        self._on_transition = on_transition
        self._on_signal = on_signal

        # timeframe aggregators (1m -> M15 / H4 / D1)
        self._agg_m15 = TimeframeAggregator(config.trigger_tf_min)
        self._agg_h4 = TimeframeAggregator(config.htf_min)
        self._agg_d1 = TimeframeAggregator(config.d1_min)

        # per-TF market structure
        self._struct_m15 = StructureTracker(config.swing_lookback, config.max_swings)
        self._struct_h4 = StructureTracker(config.swing_lookback, config.max_swings)
        self._struct_d1 = StructureTracker(config.swing_lookback, config.max_swings)

        # per-HTF order blocks
        self._ob_h4 = self._new_ob()
        self._ob_d1 = self._new_ob()

        # M15 indicators
        self._atr = WilderATR(config.atr_period)
        self._vsma = VolumeSMA(config.vol_sma_period)

        # developing-day volume profile + prior-day carry
        self._day_profile = self._new_profile()
        self._prior: PriorProfile | None = None

        # last two completed M15 bars (for CISD) + prior completed H4 hi/lo (ERL)
        self._m15_hist: list[Candle] = []
        self._prior_h4_high: float | None = None
        self._prior_h4_low: float | None = None

        # state
        self._state = State.IDLE
        self._position: PositionState | None = None
        self._last_ts: datetime | None = None
        self._traded_today = 0
        self._cur_date: date | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> State:
        return self._state

    @property
    def position(self) -> PositionState | None:
        return self._position

    @property
    def prior(self) -> PriorProfile | None:
        return self._prior

    def snapshot(self) -> dict:
        bias = self._htf_bias
        return {
            "strategy": "smc",
            "state": self._state.value,
            "date": None if self._cur_date is None else str(self._cur_date),
            "bias": None if bias is None else bias.value,
            "h4_trend": None if self._struct_h4.trend is None else self._struct_h4.trend.value,
            "d1_trend": None if self._struct_d1.trend is None else self._struct_d1.trend.value,
            "traded_today": self._traded_today,
            "profile_ready": self._day_profile.ready,
            "prior": None if self._prior is None else {
                "session_id": self._prior.session_id,
                "poc": self._prior.poc, "vah": self._prior.vah,
                "val": self._prior.val, "shape": self._prior.shape.value,
            },
            "position": None if self._position is None else {
                "direction": self._position.direction.value,
                "entry_price": self._position.entry_price,
                "stop": self._position.stop,
                "bars_held": self._position.bars_held,
            },
        }

    def reset(self) -> None:
        cfg = self.config
        self._agg_m15 = TimeframeAggregator(cfg.trigger_tf_min)
        self._agg_h4 = TimeframeAggregator(cfg.htf_min)
        self._agg_d1 = TimeframeAggregator(cfg.d1_min)
        self._struct_m15 = StructureTracker(cfg.swing_lookback, cfg.max_swings)
        self._struct_h4 = StructureTracker(cfg.swing_lookback, cfg.max_swings)
        self._struct_d1 = StructureTracker(cfg.swing_lookback, cfg.max_swings)
        self._ob_h4 = self._new_ob()
        self._ob_d1 = self._new_ob()
        self._atr = WilderATR(cfg.atr_period)
        self._vsma = VolumeSMA(cfg.vol_sma_period)
        self._day_profile = self._new_profile()
        self._prior = None
        self._m15_hist = []
        self._prior_h4_high = None
        self._prior_h4_low = None
        self._state = State.IDLE
        self._position = None
        self._last_ts = None
        self._traded_today = 0
        self._cur_date = None

    def replay(self, candles: Iterable[Candle]) -> list[Signal]:
        out: list[Signal] = []
        for c in candles:
            sig = self.on_candle(c)
            if sig is not None:
                out.append(sig)
        return out

    def force_flat(self, ts: datetime) -> Signal | None:
        """Sync after a server-side close: drop the ghost position, emit a
        broker_closed EXIT, and re-arm for another setup. Mirrors
        :meth:`SvpEngine.force_flat`."""
        pos = self._position
        if pos is None:
            return None
        self._transition(ts, State.BREAKOUT, State.RANGE_DEFINED, "EXIT_BROKER",
                         f"bars={pos.bars_held}")
        sig = self._emit(ts, SignalKind.EXIT, pos.direction, pos.stop,
                         State.BREAKOUT, State.RANGE_DEFINED,
                         reason="broker_closed", stop=pos.stop,
                         bars_held=pos.bars_held)
        self._position = None
        self._state = State.RANGE_DEFINED
        return sig

    def on_candle(self, c: Candle) -> Signal | None:
        cfg = self.config
        validate(c)

        # 1. timestamp ordering (mirror SvpEngine strict-monotonic contract)
        if self._last_ts is not None and c.ts <= self._last_ts:
            if cfg.strict_monotonic:
                raise OutOfOrderError(f"ts {c.ts} <= last {self._last_ts}")
            log.warning("smc_dropped_oob ts=%s last=%s", c.ts, self._last_ts)
            return None
        self._last_ts = c.ts

        # 2. UTC-day rollover: snapshot developing profile -> prior POC, reset
        d = c.ts.date()
        if d != self._cur_date:
            if self._cur_date is not None and self._day_profile.ready:
                self._prior = self._snapshot_prior(self._cur_date)
            self._day_profile = self._new_profile()
            self._traded_today = 0
            self._cur_date = d

        # 3. feed developing-day profile
        self._day_profile.update(c)

        # 4. feed aggregators; process any COMPLETED higher-TF candles
        d1c = self._agg_d1.update(c)
        if d1c is not None:
            d1_event = self._struct_d1.update(d1c)
            self._ob_d1.update(d1c, d1_event)

        h4c = self._agg_h4.update(c)
        if h4c is not None:
            h4_event = self._struct_h4.update(h4c)
            self._ob_h4.update(h4c, h4_event)
            # carry the just-completed H4 hi/lo as prior-HTF ERL reference
            self._prior_h4_high = h4c.high
            self._prior_h4_low = h4c.low

        sig: Signal | None = None
        m15c = self._agg_m15.update(c)
        if m15c is not None:
            m15_event = self._struct_m15.update(m15c)
            self._atr.update(m15c.high, m15c.low, m15c.close)
            if m15c.volume > 0:
                self._vsma.update(m15c.volume)
            sig = self._on_decision(m15c, m15_event)
            # keep the last two completed M15 bars for CISD (append AFTER decide)
            self._m15_hist.append(m15c)
            if len(self._m15_hist) > 3:
                self._m15_hist.pop(0)

        # position bar accounting (exits owned downstream)
        if self._position is not None:
            self._position.bars_held += 1

        return sig

    # ------------------------------------------------------------------ #
    # Bias
    # ------------------------------------------------------------------ #
    @property
    def _htf_bias(self) -> Direction | None:
        """H4 trend, vetoed to None when D1 trend is present and opposite."""
        base = self._struct_h4.trend
        if base is None:
            return None
        d1 = self._struct_d1.trend
        if d1 is not None and d1 is not base:
            return None      # D1 veto
        return base

    # ------------------------------------------------------------------ #
    # Decision bar (M15 completion)
    # ------------------------------------------------------------------ #
    def _on_decision(self, m15c: Candle, m15_event) -> Signal | None:
        # keep state in sync with bias/position for the cli force_flat check
        bias = self._htf_bias
        if self._position is not None:
            return None                          # in a trade: no new entry
        if bias is None:
            if self._state is not State.IDLE:
                self._transition(m15c.ts, self._state, State.IDLE, "DISARM",
                                 "no_bias")
                self._state = State.IDLE
            return None
        # bias present + flat -> armed
        if self._state is not State.RANGE_DEFINED:
            self._transition(m15c.ts, self._state, State.RANGE_DEFINED, "ARM",
                             f"bias={bias.value}")
            self._state = State.RANGE_DEFINED
        if self._traded_today >= self.config.max_trades_per_day:
            return None
        return self._confluence(m15c, m15_event, bias)

    def _confluence(self, m15c: Candle, m15_event, bias: Direction) -> Signal | None:
        """Evaluate the 6-check confluence on the just-completed M15 bar.

        htf_poi is MANDATORY and evaluated LAST because ``OrderBlockTracker.
        poi_at`` MUTATES (marks the returned OB mitigated). We must not consume
        an OB when the trade will not fire, so: (1) count the five non-POI
        confluences first with pure read-only checks; (2) short-circuit if even
        WITH a satisfied poi the total could not reach ``min_confluences``;
        (3) only THEN resolve htf_poi — POC distance is read-only, and the
        OB-based poi_at consume happens strictly at this final step, once every
        cheaper gate has already passed. This keeps the mandatory-POI gate
        correct while never mutating an OB on a trade that will not enter.
        """
        cfg = self.config

        # ---- five non-POI confluences (all read-only) ---------------------
        ltf_sweep, sweep_level = self._check_sweep(m15c, bias)
        displacement = self._check_displacement(m15c)
        cisd = self._check_cisd(m15c, bias)
        ltf_alignment = self._check_alignment(m15_event, bias)
        premium_discount = self._check_premium_discount(m15c, bias)

        fired: list[str] = []
        if ltf_sweep:
            fired.append("sweep")
        if displacement:
            fired.append("disp")
        if cisd:
            fired.append("cisd")
        if ltf_alignment:
            fired.append("align")
        if premium_discount:
            fired.append("pd")
        non_poi = len(fired)

        # If poi could not lift us to the threshold, bail WITHOUT touching an OB.
        if non_poi + 1 < cfg.min_confluences:
            return None

        # ---- htf_poi (MANDATORY, evaluated LAST) --------------------------
        # POC distance is read-only; the OB-based check consumes via poi_at and
        # only runs here, after every other gate already passed.
        poi_present, poi_desc, ob = self._resolve_poi(m15c, bias)
        if not poi_present:
            return None

        fired.append("poi")
        total = len(fired)
        if total < cfg.min_confluences:
            return None    # unreachable given the pre-gate, but explicit & safe

        return self._enter(m15c, bias, fired, poi_desc, ob, sweep_level)

    # ---- individual confluence checks ------------------------------------ #
    def _check_sweep(self, m15c: Candle, bias: Direction):
        """(bool, swept_level|None). LTF liquidity sweep-and-reclaim of the last
        M15 swing, OR a prior-H4 ERL sweep of the prior completed H4 hi/lo."""
        if bias is Direction.LONG:
            lvl = self._struct_m15.swept_low(m15c)
            if lvl is not None:
                return True, lvl
            plo = self._prior_h4_low
            if plo is not None and m15c.low < plo and m15c.close > plo:
                return True, plo
            return False, None
        # SHORT
        lvl = self._struct_m15.swept_high(m15c)
        if lvl is not None:
            return True, lvl
        phi = self._prior_h4_high
        if phi is not None and m15c.high > phi and m15c.close < phi:
            return True, phi
        return False, None

    def _check_displacement(self, m15c: Candle) -> bool:
        """Body/range and range/ATR gate plus a volume gate (bypassed when the
        bar has no tick volume, which is the backtest-history case)."""
        cfg = self.config
        rng = m15c.high - m15c.low
        if rng <= 0:
            return False
        body = abs(m15c.close - m15c.open)
        if body / rng < cfg.disp_body_frac:
            return False
        if not self._atr.ready or rng < cfg.disp_atr_mult * self._atr.value:
            return False
        # volume gate: only enforced when real volume + a ready SMA exist
        if m15c.volume > 0 and self._vsma.ready and self._vsma.value:
            if m15c.volume < cfg.vol_mult * self._vsma.value:
                return False
        return True

    def _check_cisd(self, m15c: Candle, bias: Direction) -> bool:
        """Exact Pine port on the last two completed M15 bars.

        bull = prev_close < prev_open AND m15c.close > prev_open AND
               prev2_close <= prev_open
        bear mirrors. Needs >=2 prior completed M15 bars in history.
        """
        if len(self._m15_hist) < 2:
            return False
        prev = self._m15_hist[-1]
        prev2 = self._m15_hist[-2]
        if bias is Direction.LONG:
            return (prev.close < prev.open and m15c.close > prev.open
                    and prev2.close <= prev.open)
        return (prev.close > prev.open and m15c.close < prev.open
                and prev2.close >= prev.open)

    def _check_alignment(self, m15_event, bias: Direction) -> bool:
        """LTF trend already matches bias, OR a CHOCH toward bias fired this bar."""
        if self._struct_m15.trend is bias:
            return True
        if m15_event is not None and m15_event.kind == "CHOCH" \
                and m15_event.direction is bias:
            return True
        return False

    def _check_premium_discount(self, m15c: Candle, bias: Direction) -> bool:
        """Trade from discount (long) / premium (short) relative to the
        developing-day equilibrium (mid of the day's range so far). Falls back
        to the developing POC if available. Fail-open only if neither the range
        nor POC is available (which cannot happen once the profile has seen a
        bar, so this is effectively always evaluable)."""
        eq = self._day_equilibrium()
        if eq is None:
            poc = self._day_profile.poc
            if poc is None:
                return True                     # fail-open soft check
            eq = poc
        if bias is Direction.LONG:
            return m15c.close <= eq
        return m15c.close >= eq

    def _day_equilibrium(self) -> float | None:
        """Mid of the developing day's high/low range, from the profile's
        occupied span. None only before any bar has been folded."""
        lv = self._day_profile.levels()
        if lv is not None:
            return (lv.vah + lv.val) / 2.0
        return None

    def _resolve_poi(self, m15c: Candle, bias: Direction):
        """(present, desc, ob|None). Read-only POC check first; the OB-based
        check consumes via poi_at and runs only here (see _confluence docstring).
        """
        cfg = self.config
        # POC confluence (read-only): developing day POC preferred, then prior.
        poc = self._day_profile.poc
        if poc is not None and abs(m15c.close - poc) <= cfg.poc_tol:
            return True, f"poc@{poc:.{cfg.instrument_dp}f}", None
        if self._prior is not None \
                and abs(m15c.close - self._prior.poc) <= cfg.poc_tol:
            return True, f"prior_poc@{self._prior.poc:.{cfg.instrument_dp}f}", None
        # OB confluence (MUTATING consume) — D1 first (stronger), then H4.
        ob = self._ob_d1.poi_at(m15c.low, m15c.high, bias)
        if ob is not None:
            return True, "d1_ob", ob
        ob = self._ob_h4.poi_at(m15c.low, m15c.high, bias)
        if ob is not None:
            return True, "h4_ob", ob
        return False, "", None

    # ------------------------------------------------------------------ #
    # Entry
    # ------------------------------------------------------------------ #
    def _enter(self, m15c: Candle, direction: Direction, fired: list[str],
               poi_desc: str, ob, sweep_level) -> Signal | None:
        cfg = self.config
        # structural stop: beyond the true invalidation (swept wick and/or OB
        # far edge), never tightened. Fail-safe: bad geometry -> no entry.
        if direction is Direction.LONG:
            floor = m15c.low
            if ob is not None:
                floor = min(floor, ob.bottom)
            if sweep_level is not None:
                floor = min(floor, sweep_level)
            stop = floor - cfg.stop_buffer
        else:
            ceil = m15c.high
            if ob is not None:
                ceil = max(ceil, ob.top)
            if sweep_level is not None:
                ceil = max(ceil, sweep_level)
            stop = ceil + cfg.stop_buffer

        stop_dist = abs(m15c.close - stop)
        if stop_dist <= 0 or stop_dist > cfg.stop_max_dist:
            return None                         # fail-safe: do NOT tighten

        atr_val = self._atr.value or 0.0
        reason = (f"smc_{direction.value.lower()} conf={'+'.join(fired)} "
                  f"{len(fired)}/6 poi={poi_desc}")
        self._position = PositionState(
            direction=direction, entry_ts=m15c.ts, entry_price=m15c.close,
            stop=stop, atr_at_entry=atr_val, tp=None,
        )
        self._traded_today += 1
        event = "ENTRY_LONG" if direction is Direction.LONG else "ENTRY_SHORT"
        self._transition(m15c.ts, State.RANGE_DEFINED, State.BREAKOUT, event,
                         f"px={m15c.close} stop={stop} {reason}")
        self._state = State.BREAKOUT
        return self._emit(m15c.ts, SignalKind.ENTRY, direction, m15c.close,
                          State.RANGE_DEFINED, State.BREAKOUT, reason=reason,
                          stop=stop, atr=atr_val, bars_held=0)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _new_ob(self) -> OrderBlockTracker:
        cfg = self.config
        return OrderBlockTracker(
            disp_body_frac=cfg.disp_body_frac, disp_atr_mult=cfg.disp_atr_mult,
            confirm_bars=cfg.ob_confirm_bars, max_blocks=cfg.max_blocks,
            expiry_bars=cfg.ob_expiry_bars, atr_period=cfg.atr_period)

    def _new_profile(self) -> VolumeProfile:
        cfg = self.config
        return VolumeProfile(cfg.row_size, min_bars=cfg.min_profile_bars,
                             tpo_fallback=True)

    def _snapshot_prior(self, prev_date: date) -> PriorProfile | None:
        lv = self._day_profile.levels()
        if lv is None:
            return None
        return PriorProfile(session_id=str(prev_date), poc=lv.poc, vah=lv.vah,
                            val=lv.val, shape=lv.shape)

    # ------------------------------------------------------------------ #
    # Emit
    # ------------------------------------------------------------------ #
    def _transition(self, ts, state_from, state_to, event, detail=""):
        tr = StateTransition(ts, state_from, state_to, event, detail)
        log.info("SMC %s->%s %s %s", state_from.value, state_to.value, event, detail)
        if self._on_transition is not None:
            self._on_transition(tr)

    def _emit(self, ts, kind, direction, price, state_from, state_to, *, reason,
              stop=None, atr=None, bars_held=None) -> Signal:
        sig = Signal(
            ts=ts, kind=kind, direction=direction, price=price,
            state_from=state_from, state_to=state_to, reason=reason,
            stop=stop, tp=None, qty=None, atr=atr, bars_held=bars_held,
        )
        log.info("SMC SIGNAL %s %s reason=%s", kind.value,
                 direction.value if direction else "-", reason)
        if self._on_signal is not None:
            self._on_signal(sig)
        return sig
