"""Ladder exit manager: Babysitter-compatible multi-stage exit layer.

Drop-in replacement for :class:`orb.babysitter.Babysitter` — same
``on_bar(positions, close) -> list[Action]`` consumer contract, emitting the
exact same :class:`orb.babysitter.Action` objects (``partial_close`` with
ticket+volume, ``update_sl`` with ticket+sl) — plus an ``observe(candle)``
feed that builds higher-TF trailing context. Per position, with
d = |entry - initial SL| and r = profit / d:

  - PARTIALS: each ``(r_level, fraction)`` rung closes ``fraction * vol0``
    (snapped DOWN to ``vol_step``), once. Unfillable rungs (below ``vol_min``
    or residual would drop under ``vol_min``) are consumed silently —
    fail-safe: skip the order, keep trailing.
  - FINAL: at ``final_tp_r`` close the whole remainder and forget the ticket.
    Deterministic gap behavior: rungs are evaluated in order, cumulative
    residual honored, final closes what is left — total closed == vol0.
  - BE: from ``be_at_r`` the SL floor is entry, permanently.
  - TRAIL: from ``trail_start_r``, ``"swing"`` mode trails the last confirmed
    ``trail_tf_min`` swing low/high -/+ ``trail_buffer``; ``"atr"`` mode
    trails ``trail_atr_mult *`` Wilder ATR from the close. Context not ready
    (no swing / ATR warming) -> no trail candidate.
  - New SL = tightest of {BE floor, trail candidate} in the position's favor;
    emitted only when strictly tighter than the current SL, never widened.
    Raw floats, like Babysitter — instrument rounding is the broker's job.

Pure stdlib, sync, no I/O. O(1) per bar, bounded memory (state only for open
tickets; closed tickets swept every ``on_bar``).
"""

from __future__ import annotations

import math

from ..babysitter import LONG, Action
from ..indicators import WilderATR
from ..models import Candle
from .mtf import TimeframeAggregator
from .structure import StructureTracker

_EPS = 1e-9


def _snap_down(v: float, step: float) -> float:
    """Floor ``v`` to a multiple of ``step``, guarding float artifacts."""
    return round(math.floor(v / step + _EPS) * step, 10)


class _PosState:
    __slots__ = ("entry", "d", "vol0", "filled", "be_done")

    def __init__(self, entry: float, d: float, vol0: float) -> None:
        self.entry = entry
        self.d = d
        self.vol0 = vol0
        self.filled: set[int] = set()
        self.be_done = False


class LadderExitManager:
    """Ladder partials + BE lock + structure/ATR trail, Babysitter contract."""

    __slots__ = ("partial_levels", "final_tp_r", "be_at_r", "trail_start_r",
                 "trail_mode", "trail_atr_mult", "trail_buffer", "vol_min",
                 "vol_step", "default_d", "_agg", "_struct", "_atr", "_trades")

    def __init__(self, *, partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30)),
                 final_tp_r: float = 10.0, be_at_r: float = 2.0,
                 trail_start_r: float = 2.0, trail_mode: str = "swing",
                 trail_atr_mult: float = 2.5, trail_buffer: float = 0.5,
                 swing_lookback: int = 2, atr_period: int = 14,
                 trail_tf_min: int = 15, vol_min: float = 0.01,
                 vol_step: float = 0.01, default_d: float = 2.0) -> None:
        if trail_mode not in ("swing", "atr"):
            raise ValueError(f"trail_mode must be 'swing' or 'atr': {trail_mode!r}")
        if vol_step <= 0 or vol_min <= 0:
            raise ValueError("vol_step and vol_min must be > 0")
        if default_d <= 0:
            raise ValueError("default_d must be > 0")
        self.partial_levels = tuple(partial_levels)
        self.final_tp_r = final_tp_r
        self.be_at_r = be_at_r
        self.trail_start_r = trail_start_r
        self.trail_mode = trail_mode
        self.trail_atr_mult = trail_atr_mult
        self.trail_buffer = trail_buffer
        self.vol_min = vol_min
        self.vol_step = vol_step
        self.default_d = default_d
        self._agg = TimeframeAggregator(trail_tf_min)
        self._struct = StructureTracker(lookback=swing_lookback)
        self._atr = WilderATR(atr_period)
        self._trades: dict[int, _PosState] = {}

    # ------------------------------------------------------------------ #
    def observe(self, c: Candle) -> None:
        """Feed every closed 1m candle; folds into TF trail context."""
        done = self._agg.update(c)
        if done is not None:
            self._struct.update(done)
            self._atr.update(done.high, done.low, done.close)

    # ------------------------------------------------------------------ #
    def _trail(self, long_pos: bool, close: float) -> float | None:
        """Trail SL candidate from current context; None when not ready."""
        if self.trail_mode == "atr":
            atr = self._atr.value
            if atr is None:
                return None
            return (close - self.trail_atr_mult * atr if long_pos
                    else close + self.trail_atr_mult * atr)
        sp = (self._struct.last_swing_low if long_pos
              else self._struct.last_swing_high)
        if sp is None:
            return None
        return (sp.price - self.trail_buffer if long_pos
                else sp.price + self.trail_buffer)

    # ------------------------------------------------------------------ #
    def on_bar(self, positions, close: float) -> list[Action]:
        """positions: iterable with .ticket .type .volume .price_open .sl"""
        actions: list[Action] = []
        seen = set()
        for p in positions:
            seen.add(p.ticket)
            st = self._trades.get(p.ticket)
            if st is None:
                d = abs(p.price_open - p.sl) if p.sl else self.default_d
                if d <= 0:                     # SL at entry: unusable distance
                    d = self.default_d
                st = self._trades[p.ticket] = _PosState(p.price_open, d,
                                                        p.volume)
            long_pos = p.type == LONG
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
                    continue                   # fail-safe: skip, keep trailing
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

            # 3. breakeven floor, permanent once reached
            if self.be_at_r > 0 and r >= self.be_at_r:
                st.be_done = True

            # 4. trail candidate, only past trail_start_r
            candidate = st.entry if st.be_done else None
            if r >= self.trail_start_r:
                t = self._trail(long_pos, close)
                if t is not None:
                    if candidate is None:
                        candidate = t
                    else:
                        candidate = (max(candidate, t) if long_pos
                                     else min(candidate, t))

            # 5. tighten-only SL update
            if candidate is None:
                continue
            cur = p.sl or 0.0
            tighter = (cur == 0.0 or
                       (long_pos and candidate > cur) or
                       (not long_pos and candidate < cur))
            if tighter:
                actions.append(Action("update_sl", p.ticket, sl=candidate))

        # forget closed tickets
        for t in list(self._trades):
            if t not in seen:
                del self._trades[t]
        return actions
