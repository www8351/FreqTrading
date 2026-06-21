"""M6 — macro backtest harness (the gate before any live filter/guard).

Answers the only question that matters before risking money: *would the macro
filter have helped?* It overlays the EXACT live veto logic on a baseline trade
list — for each trade it reconstructs the MacroState that the macro layer would have
emitted at that trade's timestamp (``build_state(events, trade.ts)``) and runs the
shared ``decide_entry`` — then reports profit factor / net / win-rate BEFORE vs
AFTER the filter, per symbol.

Reconstruction is calendar-driven (blackout windows + released-event surprise bias),
which is fully determinable from a historical economic-calendar dump (ts + forecast
+ actual). Geo/sentiment/semis backtesting needs historical series for those feeds
(not cheaply available) — pass them via ``build_kw`` when you have them; omitted,
those layers stay neutral and the gate measures the calendar filter.

Pure + stdlib (reuses ``macro.build`` + the shared ``orb.macroguard`` decision fn).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from orb.macroguard import MacroState, bare_key, decide_entry
from orb.models import Direction

from .build import build_state


@dataclass(frozen=True, slots=True)
class Trade:
    ts: datetime              # entry time, tz-aware UTC
    symbol: str               # bare ("XAUUSD") or broker-suffixed ("XAUUSD.ecn")
    direction: str            # "LONG" | "SHORT"
    pnl: float                # realized P&L (account currency)


def stats(trades) -> dict:
    """Profit factor / net / win-rate / count over a trade list. pf=None when there
    are no losing trades (undefined / +inf)."""
    n = len(trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    gross_w, gross_l = sum(wins), -sum(losses)
    pf = round(gross_w / gross_l, 3) if gross_l > 0 else None
    return {"n": n, "net": round(sum(t.pnl for t in trades), 2), "pf": pf,
            "winrate": round(len(wins) / n, 3) if n else 0.0}


def apply_macro(trades, events, conf_min: float = 0.6,
                default_when_stale: str = "allow", build_kw: dict | None = None):
    """Return (kept_trades, decisions) — kept = trades the macro filter would NOT
    have vetoed. ``decisions`` is a list of (trade, Decision) for inspection."""
    build_kw = build_kw or {}
    kept, decisions = [], []
    for t in trades:
        state = MacroState.from_dict(build_state(events, t.ts, **build_kw))
        dec = decide_entry(state, bare_key(t.symbol),
                           Direction[t.direction.upper()], None, t.ts,
                           conf_min, default_when_stale)
        decisions.append((t, dec))
        if dec.action != "VETO":
            kept.append(t)
    return kept, decisions


def compare(trades, events, symbols=None, **kw) -> dict:
    """Baseline vs macro-filtered stats, overall + per symbol."""
    kept, _ = apply_macro(trades, events, **kw)
    out = {"baseline": stats(trades), "filtered": stats(kept),
           "dropped": len(trades) - len(kept), "by_symbol": {}}
    syms = symbols or sorted({bare_key(t.symbol) for t in trades})
    for s in syms:
        bt = [t for t in trades if bare_key(t.symbol) == s]
        kt = [t for t in kept if bare_key(t.symbol) == s]
        out["by_symbol"][s] = {"baseline": stats(bt), "filtered": stats(kt),
                               "dropped": len(bt) - len(kt)}
    return out
