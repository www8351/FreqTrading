"""Displacement-anchored order-block detection over one timeframe's candles.

An order block is the last opposite-colour candle before a displacement bar
(body/range >= ``disp_body_frac`` AND range >= ``disp_atr_mult`` * ATR, ATR
fed internally from the same candles). The candidate zone — the opposite
candle's FULL range — goes ACTIVE only when a same-direction structure break
(:class:`orb.smc.structure.StructureEvent`, BOS or CHOCH) arrives within
``confirm_bars`` bars of the candidate; unconfirmed candidates are dropped.

Active blocks are mitigated permanently on the first bar that trades into the
zone (bars strictly AFTER activation — the block's own candle and the
displacement leg never self-mitigate), removed on a close through the far
edge, and expire once ``expiry_bars`` bars have elapsed since ``born_bar``.
``poi_at`` consumes: the returned block is marked mitigated.

One instance per timeframe, fed COMPLETED TF candles. Pure, sync, stdlib
only. O(1) per bar, bounded memory, no I/O. Ambiguous state -> ``None``,
never raise for market conditions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from ..indicators import WilderATR
from ..models import Candle, Direction
from .structure import StructureEvent

_RECENT_BARS = 10      # scan depth for the opposite-colour candle
_MAX_PENDING = 5       # unconfirmed candidates kept per direction


@dataclass(slots=True)
class OrderBlock:
    """One order-block zone; ``direction`` is the move it supports."""

    ts: datetime
    direction: Direction       # bullish OB supports LONG
    top: float                 # full candle range
    bottom: float
    mitigated: bool = False
    born_bar: int = 0          # tracker bar counter at creation


class OrderBlockTracker:
    """Displacement + structure-confirmed order blocks, fed one closed TF bar at a time."""

    __slots__ = ("disp_body_frac", "disp_atr_mult", "confirm_bars",
                 "max_blocks", "expiry_bars", "atr_period",
                 "_atr", "_recent", "_bar", "_pending", "_active")

    def __init__(self, *, disp_body_frac: float = 0.5, disp_atr_mult: float = 1.2,
                 confirm_bars: int = 10, max_blocks: int = 20,
                 expiry_bars: int = 180, atr_period: int = 14) -> None:
        if not (0.0 < disp_body_frac <= 1.0):
            raise ValueError("disp_body_frac must be in (0, 1]")
        if disp_atr_mult <= 0:
            raise ValueError("disp_atr_mult must be > 0")
        if confirm_bars < 0:
            raise ValueError("confirm_bars must be >= 0")
        if max_blocks < 1:
            raise ValueError("max_blocks must be >= 1")
        if expiry_bars < 1:
            raise ValueError("expiry_bars must be >= 1")
        self.disp_body_frac = disp_body_frac
        self.disp_atr_mult = disp_atr_mult
        self.confirm_bars = confirm_bars
        self.max_blocks = max_blocks
        self.expiry_bars = expiry_bars
        self.atr_period = atr_period
        self._atr = WilderATR(atr_period)
        self._recent: deque[Candle] = deque(maxlen=_RECENT_BARS)
        self._bar = 0
        self._pending: dict[Direction, deque[OrderBlock]] = {
            Direction.LONG: deque(maxlen=_MAX_PENDING),
            Direction.SHORT: deque(maxlen=_MAX_PENDING)}
        self._active: dict[Direction, deque[OrderBlock]] = {
            Direction.LONG: deque(maxlen=max_blocks),
            Direction.SHORT: deque(maxlen=max_blocks)}

    # ------------------------------------------------------------------ #
    def update(self, c: Candle, event: StructureEvent | None = None) -> None:
        """Feed one closed TF bar plus that bar's structure event, if any."""
        self._bar += 1
        bar = self._bar

        # 1. expire / invalidate / mitigate blocks active BEFORE this bar.
        #    Runs ahead of promotion so a block never sees its own displacement
        #    leg or confirmation bar.
        for dirn, reg in self._active.items():
            if not reg:
                continue
            keep = []
            for ob in reg:
                if bar - ob.born_bar >= self.expiry_bars:
                    continue                                   # expired
                if (c.close < ob.bottom if dirn is Direction.LONG
                        else c.close > ob.top):
                    continue                                   # invalidated
                if not ob.mitigated and c.low <= ob.top and c.high >= ob.bottom:
                    ob.mitigated = True                        # first touch
                keep.append(ob)
            reg.clear()
            reg.extend(keep)

        # 2. displacement bar -> candidate from the last opposite-colour bar.
        #    ATR/recents still exclude this bar: the bar is measured against
        #    prior volatility, not itself.
        rng = c.high - c.low
        atr = self._atr.value
        if (atr is not None and rng > 0 and c.close != c.open
                and abs(c.close - c.open) / rng >= self.disp_body_frac
                and rng >= self.disp_atr_mult * atr):
            dirn = Direction.LONG if c.close > c.open else Direction.SHORT
            for prev in reversed(self._recent):
                if (prev.close < prev.open if dirn is Direction.LONG
                        else prev.close > prev.open):
                    self._pending[dirn].append(OrderBlock(
                        ts=prev.ts, direction=dirn,
                        top=prev.high, bottom=prev.low, born_bar=bar))
                    break

        # 3. same-direction structure break promotes in-window candidates
        #    (the event may land on the displacement bar itself).
        if event is not None:
            pend = self._pending.get(event.direction)
            if pend:
                reg = self._active[event.direction]
                for ob in pend:
                    if bar - ob.born_bar <= self.confirm_bars:
                        reg.append(ob)
                pend.clear()

        # 4. drop candidates past the confirmation window.
        for pend in self._pending.values():
            while pend and bar - pend[0].born_bar > self.confirm_bars:
                pend.popleft()

        # 5. fold this bar into ATR and the opposite-candle scan window.
        self._atr.update(c.high, c.low, c.close)
        self._recent.append(c)

    # ------------------------------------------------------------------ #
    def poi_at(self, lo: float, hi: float, direction: Direction) -> OrderBlock | None:
        """Most recent active unmitigated block of ``direction`` overlapping [lo, hi].

        A POI is consumed by the touch: the block is marked mitigated and
        returned; ``None`` when nothing qualifies.
        """
        reg = self._active.get(direction)
        if not reg:
            return None
        for ob in reversed(reg):
            if not ob.mitigated and ob.bottom <= hi and ob.top >= lo:
                ob.mitigated = True
                return ob
        return None

    def reset(self) -> None:
        self._atr = WilderATR(self.atr_period)
        self._recent.clear()
        self._bar = 0
        for pend in self._pending.values():
            pend.clear()
        for reg in self._active.values():
            reg.clear()
