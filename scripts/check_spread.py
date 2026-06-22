"""Read-only real-spread profiler from the local MT5 terminal.

Pulls per-bar `spread` (points) from copy_rates over recent history -> price-unit
distribution, plus a live ask-bid snapshot. No orders, metadata + rates only.

Usage:  python scripts/check_spread.py US100.ecn [--bars 100000]
"""
from __future__ import annotations

import argparse
import sys


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = round((p / 100.0) * (len(sorted_vals) - 1))
    return sorted_vals[int(idx)]


def _median(sorted_vals: list[float]) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return sorted_vals[mid]
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def spread_stats(spread_points: list[int], point: float) -> dict:
    """Convert per-bar spread (points) to price-unit distribution stats."""
    prices = sorted(p * point for p in spread_points)
    n = len(prices)
    if n == 0:
        return {"n": 0, "min": 0.0, "median": 0.0, "p90": 0.0, "p99": 0.0,
                "max": 0.0, "mean": 0.0}
    return {"n": n, "min": prices[0], "median": _median(prices),
            "p90": _pct(prices, 90), "p99": _pct(prices, 99),
            "max": prices[-1], "mean": sum(prices) / n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="US100.ecn")
    ap.add_argument("--bars", type=int, default=100000)
    args = ap.parse_args()

    try:
        import MetaTrader5 as mt5  # noqa: N816
    except ImportError:
        print("MetaTrader5 not importable in this interpreter", file=sys.stderr)
        return 2
    if not mt5.initialize():
        print(f"mt5.initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 3
    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"symbol_select failed: {mt5.last_error()}", file=sys.stderr)
            return 1
        info = mt5.symbol_info(args.symbol)
        point = info.point
        rates = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M1, 0, args.bars)
        if rates is None or len(rates) == 0:
            print(f"no rates: {mt5.last_error()}", file=sys.stderr)
            return 1
        pts = [int(r["spread"]) for r in rates]
        s = spread_stats(pts, point)
        tick = mt5.symbol_info_tick(args.symbol)
        live = (tick.ask - tick.bid) if tick else None
        print(f"symbol={args.symbol} point={point} bars={s['n']}")
        print(f"per-bar spread (PRICE units): min={s['min']:.4f} "
              f"median={s['median']:.4f} p90={s['p90']:.4f} p99={s['p99']:.4f} "
              f"max={s['max']:.4f} mean={s['mean']:.4f}")
        print(f"live ask-bid now: "
              f"{('%.4f' % live) if live is not None else 'n/a (closed)'}")
        print(f"backtest ASSUMED spread = 1.0 (US100) / 0.10 (gold) -> compare median/p90")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
