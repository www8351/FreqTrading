"""SvpEngine — the Session Volume Profile "Edge Rotation" state machine.

A sibling of :class:`orb.engine.OrbEngine` (NOT a subclass): same public surface
(``on_candle`` / ``position`` / ``snapshot`` / ``force_flat`` / ``reset``) so the
async stream and the cli on_bar sync drive it unchanged, but completely separate
logic and state. Pure, sync, stdlib only.

The engine emits ENTRY signals with a STRUCTURAL stop (just beyond the relevant
HVN/VAH/VAL shelf) and ``qty=None`` — position sizing is injected downstream in
cli.on_signal. Exits are owned by the babysitter (70% off at +2R, then chase),
so the engine only emits EXIT on session end or when synced flat via
``force_flat`` after the broker closes a position.

State mapping (reuses the ORB :class:`State` enum so cli state checks need no edit):
    IDLE           pre-session / profile warming
    RANGE_DEFINED  ARMED — profile ready, flat, watching the edges
    BREAKOUT       IN_POSITION — a trade is open
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

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
from ..models import OrbConfig
from ..session import SessionClock, Zone
from .config import SvpConfig
from .levels import PriorProfile, ProfileLevels, Shape
from .profile import VolumeProfile
from .structure import SwingStructure

log = logging.getLogger("orb.svp.strategy")


class SvpEngine:
    def __init__(self, config: SvpConfig, on_transition=None, on_signal=None) -> None:
        self.config = config
        self._on_transition = on_transition
        self._on_signal = on_signal
        # SessionClock only reads session_open_utc / range_minutes / session_len_min.
        self._clock = SessionClock(OrbConfig(
            session_open_utc=config.session_open_utc,
            session_len_min=config.session_len_min,
            range_minutes=1,
        ))
        self._developing = self._new_profile()
        self._vsma = VolumeSMA(config.absorb_lookback)
        # risk/filter helpers (all inert unless enabled in config)
        self._atr = WilderATR(config.atr_period)
        self._swing = SwingStructure(config.swing_lookback)
        self._session_open_px: float | None = None
        self._state = State.IDLE
        self._pos: PositionState | None = None
        self._prior: PriorProfile | None = None
        self._traded = 0
        self._prev_close: float | None = None
        self._last_ts: datetime | None = None
        self._session_id: str | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> State:
        return self._state

    @property
    def position(self) -> PositionState | None:
        return self._pos

    @property
    def profile(self) -> VolumeProfile:
        return self._developing

    @property
    def prior(self) -> PriorProfile | None:
        return self._prior

    def snapshot(self) -> dict:
        lv = self._developing.levels()
        return {
            "strategy": "svp",
            "state": self._state.value,
            "session_id": self._session_id,
            "ready": self._developing.ready,
            "bars": self._developing.bars,
            "traded_this_session": self._traded,
            "poc": None if lv is None else lv.poc,
            "vah": None if lv is None else lv.vah,
            "val": None if lv is None else lv.val,
            "shape": None if lv is None else lv.shape.value,
            "prior": None if self._prior is None else {
                "session_id": self._prior.session_id,
                "poc": self._prior.poc, "vah": self._prior.vah,
                "val": self._prior.val, "shape": self._prior.shape.value,
            },
            "position": None if self._pos is None else {
                "direction": self._pos.direction.value,
                "entry_price": self._pos.entry_price,
                "stop": self._pos.stop,
                "bars_held": self._pos.bars_held,
            },
        }

    def reset(self) -> None:
        self._developing = self._new_profile()
        self._vsma = VolumeSMA(self.config.absorb_lookback)
        self._atr = WilderATR(self.config.atr_period)
        self._swing = SwingStructure(self.config.swing_lookback)
        self._session_open_px = None
        self._state = State.IDLE
        self._pos = None
        self._prior = None
        self._traded = 0
        self._prev_close = None
        self._last_ts = None
        self._session_id = None

    def replay(self, candles: Iterable[Candle]) -> list[Signal]:
        out: list[Signal] = []
        for c in candles:
            sig = self.on_candle(c)
            if sig is not None:
                out.append(sig)
        return out

    def force_flat(self, ts: datetime) -> Signal | None:
        """Sync after a server-side close: drop the ghost position, emit a
        broker_closed EXIT, and re-arm for another rotation."""
        pos = self._pos
        if pos is None:
            return None
        self._transition(ts, State.BREAKOUT, State.RANGE_DEFINED, "EXIT_BROKER",
                         f"bars={pos.bars_held}")
        sig = self._emit(ts, SignalKind.EXIT, pos.direction, pos.stop,
                         State.BREAKOUT, State.RANGE_DEFINED,
                         reason="broker_closed", stop=pos.stop,
                         bars_held=pos.bars_held)
        self._pos = None
        self._state = State.RANGE_DEFINED
        return sig

    def on_candle(self, c: Candle) -> Signal | None:
        cfg = self.config
        validate(c)

        # 1. timestamp ordering
        if self._last_ts is not None and c.ts <= self._last_ts:
            if cfg.strict_monotonic:
                raise OutOfOrderError(f"ts {c.ts} <= last {self._last_ts}")
            log.warning("svp_dropped_oob ts=%s last=%s", c.ts, self._last_ts)
            return None

        info = self._clock.classify(c.ts)

        # 2. session boundary (snapshot developing -> prior, reset)
        if info.session_id != self._session_id:
            exit_sig = None
            if self._state is State.BREAKOUT:
                exit_sig = self._force_exit(c, "session_end", State.IDLE)
            self._roll_session(info.session_id, c.ts)
            self._last_ts = c.ts
            if exit_sig is not None:
                return exit_sig
            # fall through: process this bar in the fresh session
        else:
            if info.zone is Zone.AFTER:
                self._last_ts = c.ts
                if self._state is State.BREAKOUT:
                    return self._force_exit(c, "session_end", State.IDLE)
                if self._state is not State.IDLE:
                    self._transition(c.ts, self._state, State.IDLE, "SESSION_DONE")
                    self._state = State.IDLE
                return None
            self._last_ts = c.ts

        # 3. feed the developing profile (in-session bars only). Detection uses
        #    the levels/volume ESTABLISHED before this bar — otherwise a bar that
        #    spikes through VAH also extends VAH to contain itself, and the
        #    "tag the edge + close back inside" rejection could never fire.
        prev_close = self._prev_close
        pre_levels: ProfileLevels | None = None
        pre_avg = self._vsma.value
        if info.zone in (Zone.IN_RANGE_WINDOW, Zone.IN_SESSION):
            pre_levels = self._developing.levels()
            self._developing.update(c)
            self._vsma.update(c.volume)
            # feed risk/filter indicators (inert unless enabled in config)
            self._atr.update(c.high, c.low, c.close)
            self._swing.update(c.high, c.low)
            if self._session_open_px is None:
                self._session_open_px = c.open  # Condition A reference price
            self._prev_close = c.close

        # 4. dispatch
        if self._state is State.IDLE:
            return self._on_idle(c, info)
        if self._state is State.RANGE_DEFINED:
            return self._on_armed(c, info, prev_close, pre_levels, pre_avg)
        if self._state is State.BREAKOUT:
            return self._on_in_position(c)
        return None

    # ------------------------------------------------------------------ #
    # State handlers
    # ------------------------------------------------------------------ #
    def _on_idle(self, c: Candle, info) -> Signal | None:
        if info.zone in (Zone.IN_RANGE_WINDOW, Zone.IN_SESSION):
            self._transition(c.ts, State.IDLE, State.RANGE_DEFINED, "SESSION_OPEN",
                             f"session={info.session_id}")
            self._state = State.RANGE_DEFINED
        return None

    def _on_armed(self, c: Candle, info, prev_close, levels, avg) -> Signal | None:
        if self._traded >= self.config.max_trades_per_session:
            return None
        if levels is None:
            return None
        return self._detect_setup(c, levels, prev_close, avg)

    def _on_in_position(self, c: Candle) -> Signal | None:
        # Hold. Exits are owned by the babysitter (server-side) and synced via
        # force_flat; the engine only counts bars for diagnostics.
        if self._pos is not None:
            self._pos.bars_held += 1
        return None

    # ------------------------------------------------------------------ #
    # Setup detection
    # ------------------------------------------------------------------ #
    def _detect_setup(self, c: Candle, lv: ProfileLevels, prev_close,
                      avg) -> Signal | None:
        cfg = self.config
        if cfg.enable_edge_rotation:
            sig = self._edge_rotation(c, lv)
            if sig is not None:
                return sig
        if cfg.enable_lvn:
            sig = self._lvn_break(c, lv, prev_close)
            if sig is not None:
                return sig
        if cfg.enable_absorption_proxy:
            sig = self._absorption_proxy(c, lv, avg)
            if sig is not None:
                return sig
        return None

    def _edge_rotation(self, c: Candle, lv: ProfileLevels) -> Signal | None:
        """Fade a Value-Area edge back toward the POC (balanced/D days only)."""
        if lv.shape is not Shape.D:
            return None
        cfg = self.config
        vah, val = lv.vah, lv.val
        pierced_high = c.high >= vah
        pierced_low = c.low <= val
        closed_inside = val < c.close < vah
        if pierced_high and not pierced_low and closed_inside and cfg.allow_short:
            stop = vah + cfg.buffer
            return self._enter(c, Direction.SHORT, stop, "edge_rot_vah_fade", lv)
        if pierced_low and not pierced_high and closed_inside and cfg.allow_long:
            stop = val - cfg.buffer
            return self._enter(c, Direction.LONG, stop, "edge_rot_val_fade", lv)
        return None

    def _lvn_break(self, c: Candle, lv: ProfileLevels,
                   prev_close) -> Signal | None:
        """Close-confirmed break THROUGH a low-volume node (unfair price runs)."""
        if prev_close is None:
            return None
        cfg = self.config
        for lvn in lv.lvns:
            if prev_close <= lvn < c.close and cfg.allow_long:
                return self._enter(c, Direction.LONG, lvn - cfg.buffer,
                                   "lvn_break_long", lv)
            if prev_close >= lvn > c.close and cfg.allow_short:
                return self._enter(c, Direction.SHORT, lvn + cfg.buffer,
                                   "lvn_break_short", lv)
        return None

    def _absorption_proxy(self, c: Candle, lv: ProfileLevels, avg) -> Signal | None:
        """Directionless absorption proxy at an HVN: heavy tick volume + a tiny
        body + a long rejection wick. NOT true delta (tick volume is undirected)."""
        cfg = self.config
        if not avg or avg <= 0 or c.volume < cfg.absorb_vol_mult * avg:
            return None
        body = abs(c.close - c.open)
        if body > cfg.absorb_body_ticks * cfg.tick_size:
            return None
        if not any(abs(c.close - h) <= cfg.row_size for h in lv.hvns):
            return None
        upper = c.high - max(c.open, c.close)
        lower = min(c.open, c.close) - c.low
        wick_min = cfg.absorb_wick_mult * max(body, cfg.tick_size)
        if lower >= wick_min and lower >= upper and cfg.allow_long:
            return self._enter(c, Direction.LONG, c.low - cfg.buffer,
                               "absorb_long", lv)
        if upper >= wick_min and upper > lower and cfg.allow_short:
            return self._enter(c, Direction.SHORT, c.high + cfg.buffer,
                               "absorb_short", lv)
        return None

    # ------------------------------------------------------------------ #
    # Entry / exit
    # ------------------------------------------------------------------ #
    def _enter(self, c: Candle, direction: Direction, stop: float,
               reason: str, lv: ProfileLevels) -> Signal | None:
        # --- FILTER GATE -----------------------------------------------------
        # The edge-rotation TRIGGER (pierce VAH/VAL + close back inside) lives
        # in _edge_rotation and is untouched. This single commit chokepoint
        # (shared by every setup) decides whether a detected setup is allowed to
        # become a position: trend-bias, killzone/blackout and delta filters.
        # Returning None leaves the engine ARMED (no transition, no trade
        # counted) so the next bar can still set up.
        if not self._entry_allowed(c, direction):
            return None
        # --- RISK: optional ATR stop replaces the structural shelf stop ------
        stop = self._risk_stop(c, direction, stop)
        atr_at_entry = self._atr.value or 0.0
        self._pos = PositionState(
            direction=direction, entry_ts=c.ts, entry_price=c.close,
            stop=stop, atr_at_entry=atr_at_entry, tp=None,
        )
        self._traded += 1
        event = "ENTRY_LONG" if direction is Direction.LONG else "ENTRY_SHORT"
        self._transition(c.ts, State.RANGE_DEFINED, State.BREAKOUT, event,
                         f"px={c.close} stop={stop} {reason}")
        self._state = State.BREAKOUT
        return self._emit(c.ts, SignalKind.ENTRY, direction, c.close,
                          State.RANGE_DEFINED, State.BREAKOUT, reason=reason,
                          stop=stop, range_high=lv.vah, range_low=lv.val,
                          bars_held=0)

    def _force_exit(self, c: Candle, reason: str, to_state: State) -> Signal:
        pos = self._pos
        assert pos is not None
        self._transition(c.ts, State.BREAKOUT, to_state, "EXIT_SESSION",
                         f"px={c.close} bars={pos.bars_held}")
        sig = self._emit(c.ts, SignalKind.EXIT, pos.direction, c.close,
                         State.BREAKOUT, to_state, reason=reason, stop=pos.stop,
                         bars_held=pos.bars_held)
        self._pos = None
        self._state = to_state
        return sig

    # ------------------------------------------------------------------ #
    # Filters & risk (all inert unless enabled in config)
    # ------------------------------------------------------------------ #
    def _entry_allowed(self, c: Candle, direction: Direction) -> bool:
        """Veto a detected setup (engine stays armed): killzone/blackout window,
        trend-bias gate, then the volume/delta confirmation stub."""
        if self._in_blackout(c):
            return False
        if not self._trend_ok(direction):
            return False
        if not self._delta_confirms(c):
            return False
        return True

    def _in_blackout(self, c: Candle) -> bool:
        """Time-of-day filter (UTC): open-volatility blackout, pre-close
        blackout, and an optional set of allowed killzone windows."""
        cfg = self.config
        if not (cfg.block_open_min or cfg.block_close_min or cfg.killzones):
            return False
        open_dt = datetime.combine(c.ts.date(), cfg.session_open_utc,
                                   tzinfo=c.ts.tzinfo)
        if c.ts < open_dt:                       # bar belongs to prior day's open
            open_dt -= timedelta(days=1)
        mins_since = (c.ts - open_dt).total_seconds() / 60.0
        mins_to_close = cfg.session_len_min - mins_since
        if cfg.block_open_min and mins_since < cfg.block_open_min:
            return True
        if cfg.block_close_min and mins_to_close < cfg.block_close_min:
            return True
        if cfg.killzones:
            mod = c.ts.hour * 60 + c.ts.minute
            if not any(s <= mod < e for s, e in cfg.killzones):
                return True
        return False

    def _bias_open(self) -> Direction | None:
        """Condition A: this session's open vs the PRIOR session POC — bullish
        when the open prints above it, bearish below, neutral if no prior."""
        if self._prior is None or self._session_open_px is None:
            return None
        if self._session_open_px > self._prior.poc:
            return Direction.LONG
        if self._session_open_px < self._prior.poc:
            return Direction.SHORT
        return None

    def _trend_ok(self, direction: Direction) -> bool:
        """Allow LONG only on a confirmed bullish bias, SHORT only on bearish.
        Combines Condition A (open vs prior POC) and Condition B (swing HH/HL vs
        LH/LL) per ``trend_filter_mode``. A neutral/unknown bias blocks the trade
        (act only on a CONFIRMED bias)."""
        mode = self.config.trend_filter_mode
        if mode == "off":
            return True
        a = self._bias_open()        # Condition A
        b = self._swing.bias         # Condition B (market structure)
        if mode == "open":
            return a is direction
        if mode == "structure":
            return b is direction
        if mode == "both":
            return a is direction and b is direction
        return a is direction or b is direction   # "either"

    def _delta_confirms(self, c: Candle) -> bool:
        """Volume/delta exhaustion confirmation — LIVE-only stub. Tick volume is
        UNDIRECTED, so genuine delta needs a live order-flow feed; on zero-volume
        backtest CSVs this BYPASSES (never blocks). When enabled with real
        volume, require the fade bar to print on BELOW-average volume (declining
        participation at the extreme = exhaustion)."""
        cfg = self.config
        if not cfg.use_delta_confirmation:
            return True
        if c.volume <= 0:            # no volume -> cannot evaluate -> bypass
            return True
        avg = self._vsma.value
        if avg is None or avg <= 0:
            return True
        return c.volume < avg

    def _risk_stop(self, c: Candle, direction: Direction,
                   structural: float) -> float:
        """Optionally replace the structural shelf stop with a dynamic ATR stop
        at ``atr_stop_mult`` x ATR from entry (canonical 1.5-2.0). Falls back to
        the structural stop until the ATR is ready, and (default) never sits
        TIGHTER than the structural shelf — a tight stop is swept by the tagging
        wick itself."""
        cfg = self.config
        if cfg.atr_stop_mult <= 0 or not self._atr.ready:
            return structural
        dist = cfg.atr_stop_mult * self._atr.value
        entry = c.close
        atr_stop = entry + dist if direction is Direction.SHORT else entry - dist
        if cfg.atr_stop_floor_structural:
            return (max(atr_stop, structural) if direction is Direction.SHORT
                    else min(atr_stop, structural))
        return atr_stop

    # ------------------------------------------------------------------ #
    # Session / helpers
    # ------------------------------------------------------------------ #
    def _roll_session(self, new_id: str, ts: datetime) -> None:
        # snapshot the just-finished developing profile into prior (carryover)
        if self._session_id is not None:
            lv = self._developing.levels()
            if lv is not None and self._gap_ok(self._session_id, new_id):
                self._prior = PriorProfile(
                    session_id=self._session_id, poc=lv.poc, vah=lv.vah,
                    val=lv.val, shape=lv.shape)
            else:
                self._prior = None  # stale / multi-day gap: don't carry it
            self._transition(ts, self._state, State.IDLE, "SESSION_RESET",
                             f"session={new_id}")
        self._developing = self._new_profile()
        self._vsma = VolumeSMA(self.config.absorb_lookback)
        self._atr = WilderATR(self.config.atr_period)
        self._swing = SwingStructure(self.config.swing_lookback)
        self._session_open_px = None
        self._state = State.IDLE
        self._pos = None
        self._traded = 0
        self._prev_close = None
        self._session_id = new_id

    @staticmethod
    def _gap_ok(prev_id: str, new_id: str) -> bool:
        """Carry prior value area only across consecutive (<=3 day) sessions."""
        try:
            a = datetime.fromisoformat(prev_id).date()
            b = datetime.fromisoformat(new_id).date()
        except ValueError:
            return True
        return 0 < (b - a).days <= 3

    def _new_profile(self) -> VolumeProfile:
        cfg = self.config
        return VolumeProfile(
            row_size=cfg.row_size, value_area_pct=cfg.value_area_pct,
            hvn_frac=cfg.hvn_frac, lvn_frac=cfg.lvn_frac,
            min_bars=cfg.min_session_bars, max_rows_per_bar=cfg.max_rows_per_bar,
            va_tiebreak=cfg.va_tiebreak, i_max_peak=cfg.i_max_peak,
            tpo_fallback=cfg.tpo_fallback,
        )

    # ------------------------------------------------------------------ #
    # Emit
    # ------------------------------------------------------------------ #
    def _transition(self, ts, state_from, state_to, event, detail=""):
        tr = StateTransition(ts, state_from, state_to, event, detail)
        log.info("SVP %s->%s %s %s", state_from.value, state_to.value, event, detail)
        if self._on_transition is not None:
            self._on_transition(tr)

    def _emit(self, ts, kind, direction, price, state_from, state_to, *, reason,
              stop=None, range_high=None, range_low=None, bars_held=None) -> Signal:
        sig = Signal(
            ts=ts, kind=kind, direction=direction, price=price,
            state_from=state_from, state_to=state_to, reason=reason,
            range_high=range_high, range_low=range_low, stop=stop,
            tp=None, qty=None, bars_held=bars_held,
        )
        log.info("SVP SIGNAL %s %s reason=%s", kind.value,
                 direction.value if direction else "-", reason)
        if self._on_signal is not None:
            self._on_signal(sig)
        return sig
