"""Ladder exit manager: Babysitter-compatible multi-stage exit layer.

Drop-in replacement for :class:`orb.babysitter.Babysitter` — same
``on_bar(positions, candle) -> list[Action]`` consumer contract, emitting the
exact same :class:`orb.babysitter.Action` objects (``partial_close`` with
ticket+volume, ``update_sl`` with ticket+sl). Per position, with
d = |entry - initial SL| and r = profit / d:

  - PARTIALS: each ``(r_level, fraction)`` rung closes ``fraction * vol0``
    (snapped DOWN to ``vol_step``), once, evaluated on the CURRENT bar's close
    (real-time — these are volume closes, not SL modifications). Unfillable
    rungs (below ``vol_min`` or residual would drop under ``vol_min``) are
    consumed silently — fail-safe: skip the order, keep managing.
  - FINAL: at ``final_tp_r`` close the whole remainder and forget the ticket.
    Deterministic gap behavior: rungs are evaluated in order, cumulative
    residual honored, final closes what is left — total closed == vol0.
  - TWO-STAGE DISCRETE SL (max 2 modifications per position lifetime, both
    tighten-only, evaluated on the N+1 CONFIRMATION rule): ``on_bar`` is fed
    one CLOSED trigger-TF candle at a time and keeps the previous one, so on
    receipt of candle X the CANDIDATE is N = X-1 — already fully closed by
    construction of the once-per-bar call — and X itself is N+1, the
    confirmation. All stage math reads candle N (never the developing/current
    candle); ``close`` (from X) is used only as the "current price" validity
    check and for partials.
      * Stage 1 (breakeven + costs): once, when candle N closes beyond
        ``stage1_at_r``; new SL = entry +/- (spread + commission/lot /
        value_per_move).
      * Stage 2 (final profit lock): once, when candle N closes beyond
        ``stage2_at_r``; new SL = candle N's low/high -/+ ``stage2_buffer``,
        floored/ceilinged to at least ``stage2_min_lock_r`` * d from entry.
        After stage 2 the SL is FROZEN — no further modification, ever.
      * If both thresholds are met on the same candle N (a gap), stage 2 wins
        outright (stage 1 is marked done without a separate modify) so at
        most one SL update is ever emitted per bar and at most two per
        position lifetime.
      * A computed SL that would be invalid vs the current price (e.g. a
        collapse that leaves the candidate on the wrong side of ``close``) or
        that is not strictly tighter than the position's current SL is
        skipped silently; the flag is NOT marked done, so the stage re-tries
        on a later bar.

Pure stdlib, sync, no I/O. O(1) per bar, bounded memory (state only for open
tickets; closed tickets swept every ``on_bar``).
"""

from __future__ import annotations

import math
from typing import Callable

from ..babysitter import LONG, Action
from ..models import Candle

_EPS = 1e-9


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
    """Ladder partials + two-stage discrete SL, Babysitter-shaped contract.

    ``SUPPORTS_CANDLE`` lets a generic driver (see ``scripts/sim_realistic.py``
    ``Sim`` and ``orb.cli``) tell this manager apart from :class:`Babysitter`
    without an isinstance import cycle: this manager's ``on_bar`` wants the
    full closed candle (for the N+1 confirmation), not just its close.
    """

    SUPPORTS_CANDLE = True

    __slots__ = ("partial_levels", "final_tp_r", "stage1_at_r", "stage2_at_r",
                 "stage2_min_lock_r", "stage2_buffer", "comm_per_lot",
                 "value_per_move", "spread", "spread_fn", "vol_min",
                 "vol_step", "default_d", "_trades", "_prev_candle")

    def __init__(self, *, partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30)),
                 final_tp_r: float = 10.0, stage1_at_r: float = 1.0,
                 stage2_at_r: float = 2.0, stage2_min_lock_r: float = 1.0,
                 stage2_buffer: float = 0.5, comm_per_lot: float = 7.0,
                 value_per_move: float = 100.0, spread: float = 0.10,
                 spread_fn: Callable[[], float] | None = None,
                 vol_min: float = 0.01, vol_step: float = 0.01,
                 default_d: float = 2.0) -> None:
        if vol_step <= 0 or vol_min <= 0:
            raise ValueError("vol_step and vol_min must be > 0")
        if default_d <= 0:
            raise ValueError("default_d must be > 0")
        if stage1_at_r <= 0:
            raise ValueError("stage1_at_r must be > 0")
        if stage2_at_r <= stage1_at_r:
            raise ValueError("stage2_at_r must be > stage1_at_r")
        if stage2_min_lock_r <= 0:
            raise ValueError("stage2_min_lock_r must be > 0")
        if value_per_move <= 0:
            raise ValueError("value_per_move must be > 0")
        self.partial_levels = tuple(partial_levels)
        self.final_tp_r = final_tp_r
        self.stage1_at_r = stage1_at_r
        self.stage2_at_r = stage2_at_r
        self.stage2_min_lock_r = stage2_min_lock_r
        self.stage2_buffer = stage2_buffer
        self.comm_per_lot = comm_per_lot
        self.value_per_move = value_per_move
        self.spread = spread
        self.spread_fn = spread_fn
        self.vol_min = vol_min
        self.vol_step = vol_step
        self.default_d = default_d
        self._trades: dict[int, _PosState] = {}
        self._prev_candle: Candle | None = None

    # ------------------------------------------------------------------ #
    def _current_spread(self) -> float:
        return self.spread_fn() if self.spread_fn is not None else self.spread

    def _stage1_level(self, entry: float, long_pos: bool) -> float:
        buf = self._current_spread() + self.comm_per_lot / self.value_per_move
        return entry + buf if long_pos else entry - buf

    def _stage2_level(self, entry: float, d: float, candidate: Candle,
                       long_pos: bool) -> float:
        if long_pos:
            lvl = candidate.low - self.stage2_buffer
            return max(lvl, entry + self.stage2_min_lock_r * d)
        lvl = candidate.high + self.stage2_buffer
        return min(lvl, entry - self.stage2_min_lock_r * d)

    # ------------------------------------------------------------------ #
    def on_bar(self, positions, candle: Candle) -> list[Action]:
        """positions: iterable with .ticket .type .volume .price_open .sl

        ``candle`` is the just-closed bar X; the confirmed candidate N = X-1
        is whatever was passed to the PRIOR call (stored, never re-derived).
        """
        actions: list[Action] = []
        seen = set()
        close = candle.close
        candidate = self._prev_candle
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

            # 3. two-stage discrete SL, gated on the confirmed candle N
            if candidate is not None and not st.stage2_done:
                profit_n = ((candidate.close - st.entry) if long_pos
                            else (st.entry - candidate.close))
                r_n = profit_n / st.d

                new_sl = None
                mark1 = mark2 = False
                if r_n >= self.stage2_at_r:
                    new_sl = self._stage2_level(st.entry, st.d, candidate,
                                                long_pos)
                    mark1 = mark2 = True
                elif not st.stage1_done and r_n >= self.stage1_at_r:
                    new_sl = self._stage1_level(st.entry, long_pos)
                    mark1 = True

                if new_sl is not None:
                    cur = p.sl or 0.0
                    tighter = (cur == 0.0 or
                               (long_pos and new_sl > cur) or
                               (not long_pos and new_sl < cur))
                    valid = (new_sl < close) if long_pos else (new_sl > close)
                    if tighter and valid:
                        actions.append(Action("update_sl", p.ticket, sl=new_sl))
                        if mark1:
                            st.stage1_done = True
                        if mark2:
                            st.stage2_done = True
                    # else: skip silently, re-evaluate on a later bar

        # forget closed tickets
        for t in list(self._trades):
            if t not in seen:
                del self._trades[t]
        self._prev_candle = candle
        return actions
