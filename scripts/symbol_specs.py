"""Read-only MT5 contract-spec dump for lot-size calculation.

Pulls symbol_info + account balance for the trade universe so per-symbol lot
sizes can be computed exactly (no guessing broker contract values). Does NOT
place, modify, or close any order — metadata + Market Watch select only.

Usage:  python scripts/symbol_specs.py
"""

from __future__ import annotations

import json
import sys

SYMBOLS = ["XAUUSD.ecn", "US100.ecn", "US500.ecn", "XAGUSD.ecn"]

ATR_PERIOD = 14
BARS = 300


def _atr(rates, period: int = ATR_PERIOD):
    """Wilder ATR on a sequence of MT5 rate rows (read-only compute)."""
    if rates is None or len(rates) < period + 1:
        return None
    trs = []
    for i in range(1, len(rates)):
        h, low, pc = rates[i]["high"], rates[i]["low"], rates[i - 1]["close"]
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def main() -> int:
    try:
        import MetaTrader5 as mt5  # noqa: N816
    except ImportError:
        print("MetaTrader5 package not importable in this interpreter", file=sys.stderr)
        return 2

    if not mt5.initialize():
        print(f"mt5.initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 3

    out: dict = {}
    try:
        acct = mt5.account_info()
        out["account"] = (
            None if acct is None
            else {
                "login": acct.login,
                "server": acct.server,
                "balance": acct.balance,
                "equity": acct.equity,
                "currency": acct.currency,
                "leverage": acct.leverage,
                "trade_mode": acct.trade_mode,  # 0 == demo
            }
        )

        specs = {}
        for sym in SYMBOLS:
            if not mt5.symbol_select(sym, True):  # make visible in Market Watch
                specs[sym] = {"error": f"symbol_select failed: {mt5.last_error()}"}
                continue
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
            if info is None:
                specs[sym] = {"error": f"symbol_info None: {mt5.last_error()}"}
                continue
            tick_value = getattr(info, "trade_tick_value", 0.0)
            tick_size = getattr(info, "trade_tick_size", 0.0)
            value_per_1_move = (tick_value / tick_size) if tick_size else None
            specs[sym] = {
                "digits": info.digits,
                "point": info.point,
                "trade_tick_size": tick_size,
                "trade_tick_value": tick_value,
                "value_per_1.0_move_per_lot": value_per_1_move,
                "trade_contract_size": getattr(info, "trade_contract_size", None),
                "volume_min": info.volume_min,
                "volume_step": info.volume_step,
                "volume_max": info.volume_max,
                "currency_profit": getattr(info, "currency_profit", None),
                "spread_points": getattr(info, "spread", None),
                "bid": getattr(tick, "bid", None) if tick else None,
                "ask": getattr(tick, "ask", None) if tick else None,
            }
            # recent history works even when market is closed (last bars)
            m1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, BARS)
            m5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, BARS)
            last_close = float(m1[-1]["close"]) if m1 is not None and len(m1) else None
            specs[sym]["last_close"] = last_close
            specs[sym]["atr_m1_14"] = _atr(m1)
            specs[sym]["atr_m5_14"] = _atr(m5)
        out["symbols"] = specs
    finally:
        mt5.shutdown()

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
