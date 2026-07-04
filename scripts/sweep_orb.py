"""ORB timeframe sweep + parameter grid + split-sample / multi-window
sign-stability harness. Reusable for US100 (TF sweep) and gold (param grid).

Pure helpers here; data wiring + CLI added in the next task.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def split_halves(candles: list) -> tuple[list, list]:
    """Split a candle (or any) list at the index midpoint: first <= second."""
    mid = len(candles) // 2
    return candles[:mid], candles[mid:]


def live_filter(trades: list[dict]) -> list[dict]:
    """The validated LIVE config: drop dead-zone, keep day quarters Q2/Q3."""
    return [t for t in trades
            if t["zone"] != "dead_zone" and t["day_q"] in ("Q2", "Q3")]


def grid_iter(axes: dict[str, list]) -> list[dict]:
    """Cartesian product of named axes -> list of param dicts."""
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(axes[k] for k in keys))]


def sign_stable(metric_dicts: list[dict], pf_min: float = 1.0) -> bool:
    """True iff every window is profitable: pf >= pf_min and pnl > 0."""
    return bool(metric_dicts) and all(
        m["pf"] >= pf_min and m["pnl"] > 0 for m in metric_dicts)


# ---------------------------------------------------------------------------
# Data wiring + CLI (Task 3)
# ---------------------------------------------------------------------------
import contextlib  # noqa: E402
import io          # noqa: E402

from sim_realistic import (aggregate_candles, load_csv,  # noqa: E402
                           metrics, run)

SPECS: dict[str, dict] = {
    "US100": dict(value=1.0, stop_min=15.0, stop_max=30.0, comm=0.0,
                  qty=0.80, daily=60.0),
    "XAUUSD": dict(value=100.0, stop_min=2.0, stop_max=4.0, comm=7.0,
                   qty=0.06, daily=110.0),
}

# Real measured broker spread (price units), 2026-06-22 (D-025):
#   US100.ecn median 0.60pt (was assumed 1.0); XAUUSD ~0.10. Used as the per-symbol
#   default; override with --spread.
DEFAULT_SPREAD: dict[str, float] = {"US100": 0.6, "XAUUSD": 0.10}

# Per-symbol param-grid axes. Stop bands MUST match the instrument's scale: gold
# moves in ~2-6 price units, US100 in ~15-40 points. (Sharing gold bands on US100
# instant-stops every trade -> PF 0.48 garbage, the D-025 bug.)
GRID_AXES: dict[str, dict] = {
    "US100": {
        "roc_min": [0.10, 0.15, 0.20, 0.25],
        "stop_min": [10.0, 15.0, 20.0], "stop_max": [20.0, 30.0, 40.0],
        "tp_rrr": [1.5, 2.0, 3.0],
        "partial_frac": [0.5, 0.7],
    },
    "XAUUSD": {
        "roc_min": [0.10, 0.15, 0.20, 0.25],
        "stop_min": [2.0, 2.6, 3.0], "stop_max": [4.0, 5.2, 6.0],
        "tp_rrr": [1.5, 2.0, 3.0],
        "partial_frac": [0.5, 0.7],
    },
}

_TFS_DEFAULT = ["1m", "2m", "3m", "5m", "15m"]


def _run_live(candles: list, spec: dict, params: dict, spread: float) -> dict:
    """run() with spec+params, return metrics on the LIVE-filtered trades.
    Engine spike-debug prints are muted."""
    p = dict(params)
    with contextlib.redirect_stdout(io.StringIO()):
        trades = run(candles, spec["qty"], spread, spec["comm"],
                     max_daily_loss=spec["daily"],
                     stop_min=p.get("stop_min", spec["stop_min"]),
                     stop_max=p.get("stop_max", spec["stop_max"]),
                     value_per_move=spec["value"],
                     roc_min=p.get("roc_min", 0.15),
                     tp_rrr=p.get("tp_rrr", 2.0),
                     tp_close_frac=p.get("tp_close_frac", 0.7),
                     partial_frac=p.get("partial_frac", 0.7),
                     partial_at_r=p.get("partial_at_r", 2.0),
                     spike_ratio=p.get("spike_ratio", 2.5))
    return metrics(live_filter(trades))


def score(candles: list, spec: dict, params: dict, spread: float) -> dict:
    first, second = split_halves(candles)
    return {"full": _run_live(candles, spec, params, spread),
            "first": _run_live(first, spec, params, spread),
            "second": _run_live(second, spec, params, spread)}


def tf_sweep(candles: list, spec: dict, params: dict, tfs: list[str],
             spread: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for tf in tfs:
        mins = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15}[tf]
        agg = aggregate_candles(candles, mins)
        out[tf] = score(agg, spec, params, spread)
    return out


def param_grid(candles: list, spec: dict, axes: dict[str, list],
               spread: float) -> list[tuple[dict, dict]]:
    results = [(p, score(candles, spec, p, spread)) for p in grid_iter(axes)]
    results.sort(key=lambda ps: ps[1]["full"]["pf"], reverse=True)
    return results


def oos_gate(window_paths: list[str], spec: dict, params: dict, spread: float,
             pf_min: float = 1.0) -> tuple[bool, dict]:
    per: dict[str, dict] = {}
    for path in window_paths:
        candles = load_csv([path])
        per[path] = _run_live(candles, spec, params, spread)
    stable = sign_stable(list(per.values()), pf_min=pf_min)
    return stable, per


def _fmt(tag: str, m: dict) -> str:
    return (f"{tag:<22} n={m['n']:<5} win%={m['win']:5.1f} PF={m['pf']:5.2f} "
            f"pnl=${m['pnl']:+9.2f} maxDD=${m['dd']:8.2f}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("tf", "grid", "gate"))
    ap.add_argument("--symbol", default="US100", choices=tuple(SPECS))
    ap.add_argument("--csv", default="", help="data CSV (default: per-symbol)")
    ap.add_argument("--spread", type=float, default=None,
                    help="price-unit spread (default: real measured 0.6 US100 / 0.10 gold)")
    ap.add_argument("--pf-min", type=float, default=1.0)
    args = ap.parse_args()

    spec = SPECS[args.symbol]
    spread = args.spread if args.spread is not None else DEFAULT_SPREAD[args.symbol]
    default_csv = ("data/us100_1m_20260310_20260619.csv" if args.symbol == "US100"
                   else "data/xauusd_1m_20260303_20260612.csv")
    csv_path = args.csv or default_csv

    if args.mode == "tf":
        candles = load_csv([csv_path])
        print(f"# TF sweep {args.symbol} {csv_path} spread={spread}")
        print("# CONFOUND: roc_min/spike/stop tuned for 1m; higher-TF winners "
              "are candidates, not conclusions (per-TF retune out of scope).")
        out = tf_sweep(candles, spec, params={}, tfs=_TFS_DEFAULT, spread=spread)
        for tf in _TFS_DEFAULT:
            s = out[tf]
            print(f"\n[{tf}]")
            for k in ("full", "first", "second"):
                print(_fmt(k, s[k]))
    elif args.mode == "grid":
        candles = load_csv([csv_path])
        axes = GRID_AXES[args.symbol]
        print(f"# grid {args.symbol} {csv_path} spread={spread} "
              f"({len(grid_iter(axes))} combos)")
        ranked = param_grid(candles, spec, axes, spread)
        for params, s in ranked[:15]:
            print(f"\n{params}")
            for k in ("full", "first", "second"):
                print(_fmt(k, s[k]))
    else:  # gate
        # gold OOS gate across the 3 windows; params via repeated --? not parsed
        # here: edit AXES_WINNER below to the candidate before running.
        windows = ["data/xauusd_1m_20260321_20260612.csv",
                   "data/xauusd_1m_20260303_20260612.csv",
                   "data/xauusd_1m_20260309_20260619.csv"]
        params: dict = {}   # set to the candidate config when gating
        stable, per = oos_gate(windows, spec, params, spread, pf_min=args.pf_min)
        print(f"# OOS gate {args.symbol} params={params} stable={stable}")
        for path, m in per.items():
            print(_fmt(path.split('/')[-1], m))


if __name__ == "__main__":
    main()
