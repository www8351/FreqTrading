"""Multi-symbol realistic backtest for the new Brain_X.md parameters.

Runs the live limit-mode pipeline (sim_realistic.run) per symbol with that
symbol's value-per-move, iron stop band, and measured spread, then reports
win% for: baseline, deadzone filter, and the FULL live config
(deadzone + quarter q2q3). Win% is independent of lot size.

Usage:  python scripts/backtest_symbols.py
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim_realistic import (  # noqa: E402
    load_csv, metrics, run, trades_to_records, write_trades_json)

# symbol -> params. spread in PRICE units (measured live); comm $/lot RT.
# value = $ per 1.0 price move per lot (from MT5 symbol_info).
SYMBOLS = {
    "XAUUSD": dict(glob="data/xauusd_1m_20260303_*.csv", value=100.0,
                   stop_min=2.0, stop_max=4.0, spread=0.20, comm=7.0, qty=0.06,
                   daily=110.0),
    "US100":  dict(glob="data/us100_1m_20260303_*.csv", value=1.0,
                   stop_min=15.0, stop_max=30.0, spread=1.0, comm=0.0, qty=0.80,
                   daily=60.0),
    "US500":  dict(glob="data/us500_1m_20260303_*.csv", value=1.0,
                   stop_min=2.5, stop_max=5.0, spread=0.25, comm=0.0, qty=4.80,
                   daily=60.0),
    "XAGUSD": dict(glob="data/xagusd_1m_20260303_*.csv", value=5000.0,
                   stop_min=0.055, stop_max=0.110, spread=0.03, comm=7.0,
                   qty=0.04, daily=60.0),
}


def line(sym: str, cfg: str, trades: list[dict]) -> None:
    m = metrics(trades)
    print(f"{sym:<7} {cfg:<16} n={m['n']:<5} win%={m['win']:5.1f} "
          f"PF={m['pf']:5.2f} pnl=${m['pnl']:+9.2f} avg=${m['avg']:+6.2f} "
          f"maxDD=${m['dd']:8.2f}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-trades", dest="emit_trades",
                    help="write merged multi-symbol entry trades JSON for "
                         "scripts/backtest_macro.py (the M6 macro gate)")
    args = ap.parse_args()

    live_win = {}
    base_win = {}
    all_records: list[dict] = []
    print("period 2026-03-03 .. 2026-06-12 | RR 1:2, 70%@2R chase, "
          "roc 0.15, spike 2.5\n")
    for sym, p in SYMBOLS.items():
        paths = glob.glob(p["glob"])
        if not paths:
            print(f"{sym}: NO DATA ({p['glob']})", file=sys.stderr)
            continue
        candles = load_csv(paths)
        trades = run(candles, p["qty"], p["spread"], p["comm"],
                     max_daily_loss=p["daily"], stop_min=p["stop_min"],
                     stop_max=p["stop_max"], value_per_move=p["value"])
        if args.emit_trades:
            all_records.extend(trades_to_records(trades, sym))
        dz = [t for t in trades if t["zone"] != "dead_zone"]
        live = [t for t in dz if t["day_q"] in ("Q2", "Q3")]
        line(sym, "baseline", trades)
        line(sym, "deadzone", dz)
        line(sym, "deadzone+q2q3", live)
        print()
        base_win[sym] = metrics(trades)["win"]
        live_win[sym] = metrics(live)["win"]

    def rng(d):
        vals = list(d.values())
        return f"{min(vals):.1f}% .. {max(vals):.1f}%" if vals else "n/a"

    print("=" * 64)
    print("WIN% RANGE across symbols:")
    print(f"  baseline       : {rng(base_win)}   " +
          ", ".join(f"{k} {v:.1f}%" for k, v in base_win.items()))
    print(f"  live (dz+q2q3) : {rng(live_win)}   " +
          ", ".join(f"{k} {v:.1f}%" for k, v in live_win.items()))

    if args.emit_trades:
        write_trades_json(all_records, args.emit_trades)


if __name__ == "__main__":
    main()
