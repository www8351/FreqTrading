"""SMC backtest grid: symbol x timeframe, condensed risk/ops metrics.

Isolates the effect of base-candle granularity on the SMC engine (see
STATUS/PROGRESS 2026-07-05 M30 re-test) by running the SAME strategy/window
per symbol across several base timeframes, and reports ops-style numbers
(daily/weekly worst-case, trade cadence, $/trade) instead of the usual
PF/expectancy analytics block.

Real per-symbol spread measured live via scripts/check_spread.py (median,
recent bars) -- NOT the retired $1.10 gold assumption used in earlier SMC
runs (D-016/D-018, rejected by D-019 for being ~11x the real $0.10-0.12).
value_per_move/vol_max from scripts/symbol_specs.py (MT5 contract specs).

Usage: python scripts/backtest_tf_grid.py
"""

from __future__ import annotations

import glob
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sim_realistic import load_csv, run_smc  # noqa: E402

logging.getLogger("orb.riskguard").setLevel(logging.ERROR)
logging.getLogger("orb.svp.profile").setLevel(logging.ERROR)

START_BALANCE = 1000.0

# SmcConfig defaults (stop_max_dist=15, poc_tol=2, stop_buffer=0.5,
# ticks_per_row=100@tick_size=0.01 -> $1/row) are GOLD-SCALED (gold ATR_m5
# ~2.87). Applying them unmodified to a differently-scaled instrument
# reproduces the exact D-025 "gold stops on US100" bug: every structural
# stop gets rejected as "too wide" -> silently zero trades, not "no edge".
# Per-symbol overrides below:
#   - btcusd: REUSES the owner's own live-deploy calibration (STATUS
#     2026-07-05 BTCUSD.ecn smoke-test command), not a new guess.
#   - us100: first-pass estimate, scaled from measured ATR_m5 ratio to gold
#     (14.19/2.87 ~= 4.94x) via scripts/symbol_specs.py -- UNVALIDATED,
#     unlike btcusd's owner-tested numbers. Flag this in any report.
SMC_OVERRIDES = {
    "xauusd": {},
    "us100": {"stop_max_dist": 75.0, "poc_tol": 10.0, "stop_buffer": 2.5,
              "ticks_per_row": 500},
    "btcusd": {"stop_max_dist": 1500.0, "poc_tol": 60.0, "stop_buffer": 40.0,
               "ticks_per_row": 3000},
}

# tag, real measured spread (price units, median recent), value_per_1.0_move/lot, vol_max
SYMBOLS = [
    ("xauusd", 0.09, 100.0, 100.0),
    ("us100", 0.6, 1.0, 100.0),
    ("btcusd", 6.0, 1.0, 100.0),
]
TIMEFRAMES = ["m30", "m45", "m90", "h1", "h2", "h4"]


def worst_period(trades: list[dict], key) -> tuple[float, float]:
    """Worst (most negative) net for any period bucket; returns (net$, pct)."""
    buckets: dict = defaultdict(float)
    for t in trades:
        buckets[key(t["open_ts"])] += t["pnl"]
    if not buckets:
        return 0.0, 0.0
    worst = min(buckets.values())
    worst = min(worst, 0.0)
    return worst, (worst / START_BALANCE * 100.0)


def run_one(symbol_tag: str, tf: str, spread: float, value_per_move: float,
            vol_max: float) -> dict | None:
    matches = glob.glob(f"data/{symbol_tag}_{tf}_*.csv")
    if not matches:
        return None
    candles = load_csv(matches)
    trades = run_smc(candles, risk_pct=2.0, spread=spread, comm=7.0,
                      max_daily_loss_pct=10.0, start_balance=START_BALANCE,
                      value_per_move=value_per_move, vol_min=0.01,
                      vol_step=0.01, vol_max=vol_max,
                      **SMC_OVERRIDES.get(symbol_tag, {}))
    n = len(trades)
    net = sum(t["pnl"] for t in trades)
    day_dd, day_dd_pct = worst_period(trades, lambda ts: ts.date())
    week_dd, week_dd_pct = worst_period(
        trades, lambda ts: (ts.isocalendar()[0], ts.isocalendar()[1]))
    trading_days = len({t["open_ts"].date() for t in trades})
    trades_per_day = (n / trading_days) if trading_days else 0.0
    avg_per_trade = (net / n) if n else 0.0
    max_win = max((t["pnl"] for t in trades), default=0.0)
    max_loss = min((t["pnl"] for t in trades), default=0.0)
    return {
        "symbol": symbol_tag, "tf": tf, "bars": len(candles),
        "span": f"{candles[0].ts.date()}..{candles[-1].ts.date()}" if candles else "-",
        "start_balance": START_BALANCE, "n": n, "net": net,
        "day_dd": day_dd, "day_dd_pct": day_dd_pct,
        "week_dd": week_dd, "week_dd_pct": week_dd_pct,
        "trades_per_day": trades_per_day, "avg_per_trade": avg_per_trade,
        "max_win": max_win, "max_loss": max_loss,
    }


def main() -> int:
    rows = []
    for symbol_tag, spread, value_per_move, vol_max in SYMBOLS:
        for tf in TIMEFRAMES:
            r = run_one(symbol_tag, tf, spread, value_per_move, vol_max)
            if r is None:
                print(f"# {symbol_tag} {tf}: no CSV found, skipped", file=sys.stderr)
                continue
            rows.append(r)
            print(f"{r['symbol']:<8} {r['tf']:<4} bars={r['bars']:<7} "
                  f"span={r['span']:<23} n={r['n']:<5} net=${r['net']:+9.2f} "
                  f"dayDD=${r['day_dd']:+8.2f}({r['day_dd_pct']:+5.1f}%) "
                  f"wkDD=${r['week_dd']:+8.2f}({r['week_dd_pct']:+5.1f}%) "
                  f"trades/day={r['trades_per_day']:.3f} "
                  f"avg$/trade=${r['avg_per_trade']:+7.2f} "
                  f"maxWin=${r['max_win']:+7.2f} maxLoss=${r['max_loss']:+7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
