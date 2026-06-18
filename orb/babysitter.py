"""Position babysitter: manages every open position by ticket, independent of
the engine's state machine (limit-mode fills can arrive after the engine moved
on).

Per position, with d = the trade's initial SL distance:
  - at +2R (profit >= 2*d): close ``partial_frac`` of the volume, once;
  - chase with the stop: SL trails at distance d behind the close, only ever
    tightening. The position dies only by (trailed) SL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("orb.babysitter")

LONG = 0   # mt5.POSITION_TYPE_BUY
SHORT = 1  # mt5.POSITION_TYPE_SELL


@dataclass
class _TradeState:
    d: float
    partial_done: bool = False


@dataclass
class Action:
    kind: str          # "partial_close" | "update_sl"
    ticket: int
    volume: float = 0.0
    sl: float = 0.0


@dataclass
class Babysitter:
    partial_frac: float = 0.7
    partial_at_r: float = 2.0
    default_d: float = 4.0           # fallback when a position has no SL
    _trades: dict = field(default_factory=dict)

    def on_bar(self, positions, close: float) -> list[Action]:
        """positions: iterable with .ticket .type .volume .price_open .sl"""
        actions: list[Action] = []
        seen = set()
        for p in positions:
            seen.add(p.ticket)
            st = self._trades.get(p.ticket)
            if st is None:
                d = abs(p.price_open - p.sl) if p.sl else self.default_d
                st = self._trades[p.ticket] = _TradeState(d=d)
            long_pos = p.type == LONG
            profit = (close - p.price_open) if long_pos else (p.price_open - close)

            if not st.partial_done and profit >= self.partial_at_r * st.d:
                st.partial_done = True
                actions.append(Action("partial_close", p.ticket,
                                      volume=p.volume * self.partial_frac))

            target = close - st.d if long_pos else close + st.d
            cur = p.sl or 0.0
            tighter = (cur == 0.0 or
                       (long_pos and target > cur) or
                       (not long_pos and target < cur))
            if tighter:
                actions.append(Action("update_sl", p.ticket, sl=target))
        # forget closed tickets
        for t in list(self._trades):
            if t not in seen:
                del self._trades[t]
        return actions
