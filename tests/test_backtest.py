"""M6 tests: macro backtest overlay — PF before/after, veto reasons, per-symbol."""

from __future__ import annotations

from datetime import datetime, timezone

from macro.backtest import Trade, apply_macro, compare, stats
from macro.collectors import forexfactory

UTC = timezone.utc


def t(h, mi, direction, pnl, symbol="XAUUSD"):
    return Trade(ts=datetime(2026, 6, 17, h, mi, tzinfo=UTC),
                 symbol=symbol, direction=direction, pnl=pnl)


# CPI hot at 12:30Z (forecast 0.2% -> actual 0.6%): blackout 12:00-13:00, then a
# strong bearish-gold surprise bias for hours after.
EVENTS = forexfactory.parse_calendar([
    {"title": "CPI m/m", "country": "USD", "date": "2026-06-17T12:30:00Z",
     "impact": "High", "forecast": "0.2%", "actual": "0.6%"},
])

TRADES = [
    t(12, 30, "LONG", 10.0),     # inside CPI blackout      -> VETO
    t(16, 0, "LONG", -5.0),      # post-CPI bearish bias    -> VETO (bias conflict)
    t(16, 0, "SHORT", 8.0),      # aligned with bearish gold-> ALLOW
    t(6, 0, "LONG", 3.0),        # before CPI (not released)-> ALLOW
]


# --- stats ------------------------------------------------------------------
def test_stats():
    s = stats(TRADES)
    assert s["n"] == 4 and s["net"] == 16.0
    assert s["pf"] == 4.2                       # 21 / 5
    assert s["winrate"] == 0.75


def test_stats_no_losses_pf_none():
    s = stats([t(16, 0, "SHORT", 8.0), t(6, 0, "LONG", 3.0)])
    assert s["pf"] is None and s["net"] == 11.0


# --- apply_macro / decisions ------------------------------------------------
def test_apply_macro_drops_vetoed():
    kept, decisions = apply_macro(TRADES, EVENTS)
    assert len(kept) == 2
    reasons = [d.reason for _, d in decisions]
    assert reasons[0].startswith("blackout:CPI")
    assert "macro_bias_conflict" in reasons[1]
    assert decisions[2][1].action == "ALLOW" and decisions[3][1].action == "ALLOW"


# --- compare ----------------------------------------------------------------
def test_compare_before_after():
    res = compare(TRADES, EVENTS)
    assert res["baseline"]["n"] == 4 and res["filtered"]["n"] == 2
    assert res["dropped"] == 2
    assert res["baseline"]["pf"] == 4.2
    assert res["filtered"]["pf"] is None        # kept trades both winners
    assert res["by_symbol"]["XAUUSD"]["dropped"] == 2


def test_compare_no_events_keeps_all():
    res = compare(TRADES, [])                    # neutral state -> nothing vetoed
    assert res["dropped"] == 0
    assert res["filtered"]["n"] == res["baseline"]["n"]


def test_compare_per_symbol_split():
    trades = TRADES + [t(6, 0, "LONG", 2.0, symbol="US100")]   # untouched symbol
    res = compare(trades, EVENTS)
    assert set(res["by_symbol"]) == {"XAUUSD", "US100"}
    assert res["by_symbol"]["US100"]["dropped"] == 0
