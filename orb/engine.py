"""OrbEngine — the synchronous, pure ORB state machine.

State path: IDLE -> RANGE_DEFINED -> BREAKOUT -> EXIT (transient) -> IDLE/RANGE_DEFINED.

The engine consumes one closed 1m candle at a time via ``on_candle`` and returns
an optional :class:`Signal` (ENTRY / EXIT / REJECT). Every state edge also emits
a :class:`StateTransition` to the ``on_transition`` callback and the logger.

Pure: no I/O, no asyncio. ``replay`` is a thin loop for backtests; ``stream`` (a
separate module) adapts an async source onto ``on_candle``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

from .indicators import ROC, VolumeSMA, WilderATR
from .models import (
    Candle,
    Direction,
    ExitReason,
    OrbConfig,
    OutOfOrderError,
    PositionState,
    Signal,
    SignalKind,
    State,
    StateTransition,
    validate,
)
from .session import SessionClock, Zone

log = logging.getLogger("orb.engine")


class OrbEngine:
    def __init__(
        self,
        config: OrbConfig,
        on_transition=None,
        on_signal=None,
    ) -> None:
        self.config = config
        self._on_transition = on_transition
        self._on_signal = on_signal
        self._clock = SessionClock(config)
        self._build_indicators()
        self._state = State.IDLE
        self._range_hi: float | None = None
        self._range_lo: float | None = None
        self._range_bars = 0
        self._range_locked = False
        self._pos: PositionState | None = None
        self._rebuild = False  # rearm_range="rebuild": collecting a fresh range
        self._traded_this_session = False
        self._last_ts: datetime | None = None
        self._session_id: str | None = None
        self._gate_disabled_logged = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> State:
        return self._state

    @property
    def position(self) -> PositionState | None:
        return self._pos

    def snapshot(self) -> dict:
        return {
            "state": self._state.value,
            "session_id": self._session_id,
            "range_high": self._range_hi,
            "range_low": self._range_lo,
            "range_bars": self._range_bars,
            "range_locked": self._range_locked,
            "traded_this_session": self._traded_this_session,
            "atr": self._atr.value,
            "roc": self._roc.value if self.config.use_roc else None,
            "vol_sma": self._vsma.value if self.config.use_rvol else None,
            "position": None if self._pos is None else {
                "direction": self._pos.direction.value,
                "entry_price": self._pos.entry_price,
                "stop": self._pos.stop,
                "bars_held": self._pos.bars_held,
            },
        }

    def reset(self) -> None:
        """Hard reset to a pristine IDLE engine (e.g. before a fresh replay)."""
        self._build_indicators()
        self._state = State.IDLE
        self._clear_range()
        self._pos = None
        self._rebuild = False
        self._traded_this_session = False
        self._last_ts = None
        self._session_id = None
        self._gate_disabled_logged = False

    def replay(self, candles: Iterable[Candle]) -> list[Signal]:
        """Run a sequence of candles and collect emitted signals (backtest)."""
        out: list[Signal] = []
        for c in candles:
            sig = self.on_candle(c)
            if sig is not None:
                out.append(sig)
        return out

    def force_flat(self, ts: datetime) -> Signal | None:
        """Sync after a server-side close (broker SL/TP filled): drop the ghost
        position, emit a broker_closed EXIT, apply normal post-exit/rearm."""
        pos = self._pos
        if pos is None:
            return None
        to_state = self._post_exit_state()
        self._transition(ts, State.BREAKOUT, to_state, "EXIT_BROKER",
                         f"bars={pos.bars_held}")
        sig = Signal(
            ts=ts, kind=SignalKind.EXIT, direction=pos.direction,
            price=pos.stop, state_from=State.BREAKOUT, state_to=to_state,
            reason="broker_closed", range_high=self._range_hi,
            range_low=self._range_lo, atr=self._atr.value, stop=pos.stop,
            tp=pos.tp, qty=self.config.qty if self.config.qty > 0 else None,
            bars_held=pos.bars_held,
        )
        log.info("SIGNAL EXIT %s reason=broker_closed", pos.direction.value)
        if self._on_signal is not None:
            self._on_signal(sig)
        self._pos = None
        self._state = to_state
        if to_state is State.RANGE_DEFINED:
            detail = ""
            if self.config.rearm_range == "rebuild":
                self._clear_range()
                self._rebuild = True
                detail = "rebuild_range"
            self._transition(ts, State.RANGE_DEFINED, State.RANGE_DEFINED,
                             "REARM", detail)
        return sig

    def on_candle(self, c: Candle) -> Signal | None:
        cfg = self.config
        validate(c)

        # 1. timestamp ordering -------------------------------------------------
        if self._last_ts is not None and c.ts <= self._last_ts:
            if cfg.strict_monotonic:
                raise OutOfOrderError(f"ts {c.ts} <= last {self._last_ts}")
            log.warning("dropped_dup_or_oob ts=%s last=%s", c.ts, self._last_ts)
            return None

        info = self._clock.classify(c.ts)

        # 2. session boundary (evaluated BEFORE gaps: an inter-session time jump
        #    is a new session, not an intra-session data gap) --------------------
        if info.session_id != self._session_id:
            exit_sig = None
            if self._state is State.BREAKOUT:
                # position carried across a session boundary -> close as SESSION_END.
                exit_sig = self._exit(c, ExitReason.SESSION_END, State.IDLE)
            self._reset_for_new_session(info.session_id, c.ts)
            self._last_ts = c.ts
            if exit_sig is not None:
                return exit_sig
            # otherwise fall through and process this bar in the fresh session
        else:
            # same session: forced session-end exit / teardown
            if info.zone is Zone.AFTER:
                self._last_ts = c.ts
                if self._state is State.BREAKOUT:
                    return self._exit(c, ExitReason.SESSION_END, State.IDLE)
                if self._state is not State.IDLE:
                    self._transition(c.ts, self._state, State.IDLE, "SESSION_DONE")
                    self._state = State.IDLE
                    self._clear_range()
                return None

            # 3. gap detection (within the active session) ----------------------
            if self._last_ts is not None:
                delta = (c.ts - self._last_ts).total_seconds()
                gap = round(delta / cfg.expected_bar_sec) - 1
                if gap > 0:
                    if gap > cfg.max_gap_bars or cfg.on_gap == "reset":
                        sig = self._handle_gap_reset(c, gap)
                        self._last_ts = c.ts
                        if sig is not None:
                            return sig
                        # no open position: fall through, process this bar fresh
                    else:
                        log.info("gap_hold gap=%d ts=%s", gap, c.ts)
            self._last_ts = c.ts

        # 4. feed indicators (in-session bars only) -----------------------------
        if info.zone in (Zone.IN_RANGE_WINDOW, Zone.IN_SESSION):
            self._atr.update(c.high, c.low, c.close)
            if cfg.use_roc:
                self._roc.update(c.close)
            if cfg.use_rvol:
                self._vsma.update(c.volume)

        # 5. dispatch -----------------------------------------------------------
        if self._state is State.IDLE:
            return self._on_idle(c, info)
        if self._state is State.RANGE_DEFINED:
            return self._on_range_defined(c, info)
        if self._state is State.BREAKOUT:
            return self._on_breakout(c, info)
        return None

    # ------------------------------------------------------------------ #
    # State handlers
    # ------------------------------------------------------------------ #
    def _on_idle(self, c: Candle, info) -> Signal | None:
        if info.zone is Zone.IN_RANGE_WINDOW:
            self._range_hi, self._range_lo, self._range_bars = c.high, c.low, 1
            self._range_locked = False
            self._transition(c.ts, State.IDLE, State.RANGE_DEFINED, "RANGE_OPEN",
                             f"H={c.high} L={c.low}")
            self._state = State.RANGE_DEFINED
            self._maybe_lock(c)
        return None

    def _on_range_defined(self, c: Candle, info) -> Signal | None:
        # still building a range: the opening window, or an in-session rebuild
        building = (info.zone is Zone.IN_RANGE_WINDOW) or self._rebuild
        if building and not self._range_locked:
            if self._range_hi is None:  # first bar of a rebuilt range
                self._range_hi, self._range_lo, self._range_bars = c.high, c.low, 1
            else:
                self._range_hi = max(self._range_hi, c.high)
                self._range_lo = min(self._range_lo, c.low)
                self._range_bars += 1
            self._transition(c.ts, State.RANGE_DEFINED, State.RANGE_DEFINED,
                             "RANGE_BUILD", f"bars={self._range_bars}")
            self._maybe_lock(c)
            return None

        # entered the session; if range never reached N bars, lock it partial
        if info.zone is Zone.IN_SESSION and not self._range_locked:
            self._lock_range(c, partial=True)

        # armed candidate: require full indicator warmup
        if not self._warm():
            log.info("warmup_incomplete ts=%s", c.ts)
            return None

        return self._check_breakout(c)

    def _on_breakout(self, c: Candle, info) -> Signal | None:
        assert self._pos is not None
        cfg = self.config
        pos = self._pos
        pos.bars_held += 1

        # excursion tracking (diagnostic)
        if pos.direction is Direction.LONG:
            pos.mfe = max(pos.mfe, c.high - pos.entry_price)
            pos.mae = max(pos.mae, pos.entry_price - c.low)
        else:
            pos.mfe = max(pos.mfe, pos.entry_price - c.low)
            pos.mae = max(pos.mae, c.high - pos.entry_price)

        # exit precedence: range re-entry > take profit > trail stop > ratchet
        if self._range_reentered(c):
            price = c.close if cfg.reentry_on == "close" else (
                self._range_hi if pos.direction is Direction.LONG else self._range_lo)
            return self._exit(c, ExitReason.RANGE_REENTRY, self._post_exit_state(),
                              price=price)

        if self._tp_hit(c):
            price = c.close if cfg.reentry_on == "close" else pos.tp
            if cfg.tp_close_frac < 1.0:
                return self._partial_tp(c, price)
            return self._exit(c, ExitReason.TAKE_PROFIT, self._post_exit_state(),
                              price=price)

        if self._trail_hit(c):
            price = c.close if cfg.reentry_on == "close" else pos.stop
            return self._exit(c, ExitReason.TRAIL_STOP, self._post_exit_state(),
                              price=price)

        # ratchet the stop (always off close; never loosens)
        dist = self._stop_dist(self._atr.value)
        if pos.direction is Direction.LONG:
            pos.stop = max(pos.stop, c.close - dist)
        else:
            pos.stop = min(pos.stop, c.close + dist)
        log.debug("trail_update ts=%s stop=%s", c.ts, pos.stop)
        return None

    # ------------------------------------------------------------------ #
    # Range helpers
    # ------------------------------------------------------------------ #
    def _maybe_lock(self, c: Candle) -> None:
        if self._range_bars >= self.config.range_minutes:
            self._lock_range(c, partial=False)

    def _lock_range(self, c: Candle, partial: bool) -> None:
        self._range_locked = True
        self._rebuild = False
        detail = f"H={self._range_hi} L={self._range_lo} bars={self._range_bars}"
        if partial:
            detail += " range_partial"
        if self._range_hi == self._range_lo:
            detail += " zero_width_range"
            log.warning("zero_width_range ts=%s level=%s", c.ts, self._range_hi)
        self._transition(c.ts, State.RANGE_DEFINED, State.RANGE_DEFINED,
                         "RANGE_LOCK", detail)

    def _clear_range(self) -> None:
        self._range_hi = self._range_lo = None
        self._range_bars = 0
        self._range_locked = False

    # ------------------------------------------------------------------ #
    # Breakout + momentum
    # ------------------------------------------------------------------ #
    def _check_breakout(self, c: Candle) -> Signal | None:
        cfg = self.config
        direction: Direction | None = None
        if c.close > self._range_hi:
            direction = Direction.LONG
        elif c.close < self._range_lo:
            direction = Direction.SHORT
        if direction is None:
            return None

        if direction is Direction.LONG and not cfg.allow_long:
            log.info("breakout_dir_disabled LONG ts=%s", c.ts)
            return None
        if direction is Direction.SHORT and not cfg.allow_short:
            log.info("breakout_dir_disabled SHORT ts=%s", c.ts)
            return None

        roc_val, rvol_val, ok, fail = self._momentum(c, direction)
        if not ok:
            return self._signal(
                c, SignalKind.REJECT, direction, c.close,
                State.RANGE_DEFINED, State.RANGE_DEFINED,
                reason=f"momentum_fail:{fail}", roc=roc_val, rvol=rvol_val,
                atr=self._atr.value,
            )

        # open simulated position
        atr = self._atr.value
        dist = self._stop_dist(atr)
        stop = (c.close - dist) if direction is Direction.LONG else (c.close + dist)
        tp = None
        if cfg.tp_rrr is not None:
            risk = abs(stop - c.close)
            tp = (c.close + cfg.tp_rrr * risk) if direction is Direction.LONG \
                else (c.close - cfg.tp_rrr * risk)
        self._pos = PositionState(
            direction=direction, entry_ts=c.ts, entry_price=c.close,
            stop=stop, atr_at_entry=atr, tp=tp,
        )
        self._traded_this_session = True
        event = "BREAKOUT_LONG" if direction is Direction.LONG else "BREAKOUT_SHORT"
        self._transition(c.ts, State.RANGE_DEFINED, State.BREAKOUT, event,
                         f"px={c.close} stop={stop}" + (f" tp={tp}" if tp else ""))
        self._state = State.BREAKOUT
        return self._signal(
            c, SignalKind.ENTRY, direction, c.close,
            State.RANGE_DEFINED, State.BREAKOUT,
            reason=f"breakout_{direction.value.lower()}",
            roc=roc_val, rvol=rvol_val, atr=atr, stop=stop, tp=tp, bars_held=0,
        )

    def _stop_dist(self, atr: float) -> float:
        """ATR stop distance, clamped to [stop_min_dist, stop_max_dist]."""
        dist = self.config.atr_mult * atr
        if self.config.stop_max_dist is not None:
            dist = min(dist, self.config.stop_max_dist)
        if self.config.stop_min_dist is not None:
            dist = max(dist, self.config.stop_min_dist)
        return dist

    def _momentum(self, c: Candle, direction: Direction):
        """Return (roc_value, rvol_value, passed, fail_reason)."""
        cfg = self.config
        roc_val = self._roc.value if cfg.use_roc else None
        rvol_val = None
        if cfg.use_rvol:
            sma = self._vsma.value
            rvol_val = (c.volume / sma) if (sma and sma > 0) else 0.0

        if not cfg.use_roc and not cfg.use_rvol:
            if not self._gate_disabled_logged:
                log.info("momentum_gate_disabled (pure breakout)")
                self._gate_disabled_logged = True
            return roc_val, rvol_val, True, ""

        if cfg.use_roc:
            if direction is Direction.LONG and not (roc_val >= cfg.roc_min):
                return roc_val, rvol_val, False, "roc"
            if direction is Direction.SHORT and not (roc_val <= -cfg.roc_min):
                return roc_val, rvol_val, False, "roc"
        if cfg.use_rvol and not (rvol_val >= cfg.rvol_min):
            return roc_val, rvol_val, False, "rvol"
        return roc_val, rvol_val, True, ""

    # ------------------------------------------------------------------ #
    # Exit logic
    # ------------------------------------------------------------------ #
    def _range_reentered(self, c: Candle) -> bool:
        pos = self._pos
        if self.config.reentry_on == "close":
            if pos.direction is Direction.LONG:
                return c.close <= self._range_hi
            return c.close >= self._range_lo
        # intrabar
        if pos.direction is Direction.LONG:
            return c.low <= self._range_hi
        return c.high >= self._range_lo

    def _tp_hit(self, c: Candle) -> bool:
        pos = self._pos
        if pos.tp is None:
            return False
        if self.config.reentry_on == "close":
            if pos.direction is Direction.LONG:
                return c.close >= pos.tp
            return c.close <= pos.tp
        if pos.direction is Direction.LONG:
            return c.high >= pos.tp
        return c.low <= pos.tp

    def _partial_tp(self, c: Candle, price: float) -> Signal:
        """Close tp_close_frac of the position at TP; remainder rides the trail."""
        cfg = self.config
        pos = self._pos
        closed = cfg.qty * cfg.tp_close_frac if cfg.qty > 0 else None
        self._transition(c.ts, State.BREAKOUT, State.BREAKOUT, "EXIT_TP_PARTIAL",
                         f"px={price} closed={closed} bars={pos.bars_held}")
        sig = self._signal(
            c, SignalKind.EXIT, pos.direction, price,
            State.BREAKOUT, State.BREAKOUT, reason="take_profit_partial",
            atr=self._atr.value, stop=pos.stop, tp=pos.tp,
            bars_held=pos.bars_held, qty_override=closed,
        )
        pos.tp = None  # TP consumed; trail manages the remainder
        return sig

    def _trail_hit(self, c: Candle) -> bool:
        pos = self._pos
        if self.config.reentry_on == "close":
            if pos.direction is Direction.LONG:
                return c.close <= pos.stop
            return c.close >= pos.stop
        if pos.direction is Direction.LONG:
            return c.low <= pos.stop
        return c.high >= pos.stop

    def _post_exit_state(self) -> State:
        cfg = self.config
        if (not cfg.one_trade_per_session) and cfg.rearm_after_exit and (
                cfg.rearm_range == "rebuild" or self._range_locked):
            return State.RANGE_DEFINED
        return State.IDLE

    def _exit(self, c: Candle, reason: ExitReason, to_state: State,
              price: float | None = None) -> Signal:
        pos = self._pos
        assert pos is not None
        px = c.close if price is None else price
        event = {
            ExitReason.TAKE_PROFIT: "EXIT_TP",
            ExitReason.TRAIL_STOP: "EXIT_TRAIL",
            ExitReason.RANGE_REENTRY: "EXIT_REENTRY",
            ExitReason.SESSION_END: "EXIT_SESSION",
            ExitReason.GAP_INVALIDATE: "EXIT_GAP",
        }[reason]
        self._transition(c.ts, State.BREAKOUT, to_state, event,
                         f"px={px} bars={pos.bars_held}")
        sig = self._signal(
            c, SignalKind.EXIT, pos.direction, px,
            State.BREAKOUT, to_state, reason=reason.value.lower(),
            atr=self._atr.value, stop=pos.stop, tp=pos.tp, bars_held=pos.bars_held,
        )
        self._pos = None
        self._state = to_state
        if to_state is State.IDLE:
            self._transition(c.ts, State.IDLE, State.IDLE,
                             "TRADE_DONE" if reason is not ExitReason.SESSION_END
                             else "SESSION_DONE")
        else:
            detail = ""
            if self.config.rearm_range == "rebuild":
                self._clear_range()
                self._rebuild = True
                detail = "rebuild_range"
            self._transition(c.ts, State.RANGE_DEFINED, State.RANGE_DEFINED,
                             "REARM", detail)
        return sig

    # ------------------------------------------------------------------ #
    # Resets
    # ------------------------------------------------------------------ #
    def _handle_gap_reset(self, c: Candle, gap: int) -> Signal | None:
        sig = None
        if self._state is State.BREAKOUT:
            sig = self._exit(c, ExitReason.GAP_INVALIDATE, State.IDLE)
        self._build_indicators()
        # Mid-session gap (e.g. broker daily maintenance): rebuild a fresh
        # range from the next bars instead of stranding the engine in IDLE
        # until tomorrow's session open.
        info = self._clock.classify(c.ts)
        if info.zone in (Zone.IN_RANGE_WINDOW, Zone.IN_SESSION):
            self._transition(c.ts, self._state, State.RANGE_DEFINED, "GAP_RESET",
                             f"gap={gap} rebuild_range")
            self._state = State.RANGE_DEFINED
            self._clear_range()
            self._rebuild = True
        else:
            if self._state is not State.IDLE:
                self._transition(c.ts, self._state, State.IDLE, "GAP_RESET",
                                 f"gap={gap}")
            self._state = State.IDLE
            self._clear_range()
            self._rebuild = False
        log.warning("gap_reset gap=%d ts=%s", gap, c.ts)
        return sig

    def _reset_for_new_session(self, new_id: str, ts: datetime) -> None:
        self._build_indicators()
        # Emit on a genuine session change (a prior session existed); the very
        # first session (None -> first id) has no boundary to mark.
        if self._session_id is not None:
            self._transition(ts, self._state, State.IDLE, "SESSION_RESET",
                             f"session={new_id}")
        self._state = State.IDLE
        self._clear_range()
        self._pos = None
        self._rebuild = False
        self._traded_this_session = False
        self._session_id = new_id

    def _build_indicators(self) -> None:
        cfg = self.config
        self._atr = WilderATR(cfg.atr_period)
        self._roc = ROC(cfg.roc_period)
        self._vsma = VolumeSMA(cfg.rvol_period)

    def _warm(self) -> bool:
        cfg = self.config
        return (
            self._atr.ready
            and (not cfg.use_roc or self._roc.ready)
            and (not cfg.use_rvol or self._vsma.ready)
        )

    # ------------------------------------------------------------------ #
    # Emit
    # ------------------------------------------------------------------ #
    def _transition(self, ts, state_from, state_to, event, detail=""):
        tr = StateTransition(ts, state_from, state_to, event, detail)
        log.info("TRANS %s->%s %s %s", state_from.value, state_to.value, event, detail)
        if self._on_transition is not None:
            self._on_transition(tr)

    def _signal(self, c, kind, direction, price, state_from, state_to, *, reason,
                roc=None, rvol=None, atr=None, stop=None, tp=None,
                bars_held=None, qty_override=None) -> Signal:
        qty = qty_override if qty_override is not None else (
            self.config.qty if self.config.qty > 0 else None)
        sig = Signal(
            ts=c.ts, kind=kind, direction=direction, price=price,
            state_from=state_from, state_to=state_to, reason=reason,
            range_high=self._range_hi, range_low=self._range_lo,
            roc=roc, rvol=rvol, atr=atr, stop=stop, tp=tp, qty=qty,
            bars_held=bars_held,
        )
        log.info("SIGNAL %s %s reason=%s", kind.value,
                 direction.value if direction else "-", reason)
        if self._on_signal is not None:
            self._on_signal(sig)
        return sig
