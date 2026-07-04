# US100 Productionize + Gold ORB Grid — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the US100 ORB edge survives real costs (and check if a non-1m timeframe beats 1m), then confirm/keep the live deployment; and grid-search gold ORB params under a hard out-of-sample gate to settle whether gold has any ORB edge.

**Architecture:** Add a reusable backtest harness (`scripts/sweep_orb.py`) that runs the existing ORB `run()` across timeframes (via the existing `aggregate_candles`) and parameter grids, scoring each with a split-sample / multi-window sign-stability report. Parameterize `run()` so grid params flow through without touching entry logic. Add a read-only MT5 spread profiler (`scripts/check_spread.py`). The last tasks *run* these tools and record verdicts; no live trading code changes except an optional `bots.ps1` confirmation.

**Tech Stack:** Python 3.11+ (runtime stdlib-only), pytest, MetaTrader5 package (read-only, only where the MT5 terminal runs), existing `scripts/sim_realistic.py` ORB simulator.

## Global Constraints

- **Runtime is stdlib-only.** `pyproject.toml` declares `dependencies = []`. No new runtime deps. `MetaTrader5` is allowed in scripts that talk to the terminal (already used by `fetch_mt5_history.py`), guarded by `try/except ImportError`.
- **Entry logic must stay behavior-identical.** `run()` parameterization MUST default to the current hardcoded values so existing results and live behavior are unchanged. Prove it with a regression test.
- **No live change until GATE A3 passes** (Task 7). Only `scripts/bots.ps1` may be touched, only at Task 8, only on owner approval. **US100 live size stays `qty 0.40`** (owner decision). XAUUSD live bot untouched.
- **Gold grid: no in-sample-only winners.** Every grid winner MUST pass the multi-window OOS gate (Task 9) or be discarded and logged as discarded.
- **Test path convention:** tests that import `scripts/` modules do `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))` (see `tests/test_emit_trades.py`). Workspace root is already on the path via `tests/conftest.py`.
- **Commit convention:** repo owner makes final commits (STATUS/PROGRESS note "changes staged… owner commits when ready"). Execute the `git add`/`git commit` steps as written; the owner reviews before pushing.
- **US100 spec (from `scripts/backtest_symbols.py`):** `value=1.0, stop_min=15.0, stop_max=30.0, spread=1.0, comm=0.0, qty=0.80, daily=60.0`. Backtest qty 0.80 = 2× live (0.40); win%/PF are size-independent.
- **Gold spec:** `value=100.0, comm=7.0, qty=0.06, daily=110.0`; real spread **$0.10** (stress at **$0.12**), NOT the old $0.20/$1.10. Stop band + roc + RR + partial are the grid axes.
- **Gold data windows (cross-window OOS set):**
  - `data/xauusd_1m_20260321_20260612.csv` — TwelveData (volume col = 0.0)
  - `data/xauusd_1m_20260303_20260612.csv` — MT5 real-vol (volume > 0)
  - `data/xauusd_1m_20260309_20260619.csv` — MT5 real-vol (volume > 0)
- **US100 data:** `data/us100_1m_20260303_20260612.csv` and `data/us100_1m_20260310_20260619.csv`.
- **LIVE filter definition** (the config that passed sign-stability): trades with `t["zone"] != "dead_zone"` AND `t["day_q"] in ("Q2","Q3")`.

---

### Task 1: Parameterize `run()` (behavior-preserving)

Thread the grid axes through the ORB backtest entry point so Task 9 can vary them, without changing default behavior. Extract config construction into a helper to make the mapping unit-testable.

**Files:**
- Modify: `scripts/sim_realistic.py` (`run()` at lines 244-286; add helper `_orb_cfg` above it)
- Test: `tests/test_sim_run_params.py` (create)

**Interfaces:**
- Produces:
  - `_orb_cfg(candles, qty, stop_min, stop_max, roc_min, tp_rrr, tp_close_frac) -> OrbConfig`
  - `run(candles, qty, spread, comm, max_daily_loss=110.0, stop_min=2.0, stop_max=4.0, value_per_move=USD_PER_LOT_PER_DOLLAR, roc_min=0.15, tp_rrr=2.0, tp_close_frac=0.7, partial_frac=0.7, partial_at_r=2.0, spike_ratio=2.5) -> list[dict]`
  - Trade dicts unchanged (keys include `zone`, `day_q`, `dir`, `pnl`, `reason`, `signal_ts`, `open_ts`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sim_run_params.py
"""run() parameterization: config mapping + default behavior regression."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import sim_realistic  # noqa: E402
from sim_realistic import _orb_cfg, load_csv, run  # noqa: E402

US100 = "data/us100_1m_20260303_20260612.csv"


def _candles():
    # small slice keeps the test fast but exercises real signal generation
    return load_csv([US100])[:8000]


def test_orb_cfg_maps_params():
    candles = _candles()
    cfg = _orb_cfg(candles, qty=0.8, stop_min=15.0, stop_max=30.0,
                   roc_min=0.22, tp_rrr=3.0, tp_close_frac=0.5)
    assert cfg.roc_min == 0.22
    assert cfg.stop_min_dist == 15.0 and cfg.stop_max_dist == 30.0
    assert cfg.tp_rrr == 3.0 and cfg.tp_close_frac == 0.5
    assert cfg.qty == 0.8


def test_run_defaults_unchanged_regression():
    # default-arg call and explicit-current-values call must be identical
    candles = _candles()
    a = run(candles, 0.8, 1.0, 0.0, max_daily_loss=60.0,
            stop_min=15.0, stop_max=30.0, value_per_move=1.0)
    b = run(candles, 0.8, 1.0, 0.0, max_daily_loss=60.0,
            stop_min=15.0, stop_max=30.0, value_per_move=1.0,
            roc_min=0.15, tp_rrr=2.0, tp_close_frac=0.7,
            partial_frac=0.7, partial_at_r=2.0, spike_ratio=2.5)
    assert len(a) == len(b)
    assert sum(t["pnl"] for t in a) == sum(t["pnl"] for t in b)


def test_run_roc_min_blocks_all_entries():
    candles = _candles()
    base = run(candles, 0.8, 1.0, 0.0, max_daily_loss=60.0,
               stop_min=15.0, stop_max=30.0, value_per_move=1.0)
    blocked = run(candles, 0.8, 1.0, 0.0, max_daily_loss=60.0,
                  stop_min=15.0, stop_max=30.0, value_per_move=1.0,
                  roc_min=10_000.0)  # impossible momentum threshold
    assert len(base) > 0
    assert len(blocked) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sim_run_params.py -v`
Expected: FAIL — `ImportError: cannot import name '_orb_cfg'`.

- [ ] **Step 3: Write minimal implementation**

In `scripts/sim_realistic.py`, add the helper directly above `def run(` (before line 244):

```python
def _orb_cfg(candles: list[Candle], qty: float, stop_min: float, stop_max: float,
             roc_min: float, tp_rrr: float, tp_close_frac: float) -> OrbConfig:
    return OrbConfig(
        session_open_utc=candles[0].ts.time().replace(second=0, microsecond=0),
        session_len_min=1440, roc_min=roc_min,
        stop_max_dist=stop_max, stop_min_dist=stop_min,
        tp_rrr=tp_rrr, tp_close_frac=tp_close_frac, qty=qty,
        one_trade_per_session=False, rearm_after_exit=True,
        rearm_range="rebuild",
    )
```

Replace the `run()` signature and its `cfg`/`sitter`/`spike` construction. Change the
signature (lines 244-247) to:

```python
def run(candles: list[Candle], qty: float, spread: float, comm: float,
        max_daily_loss: float = 110.0, stop_min: float = 2.0,
        stop_max: float = 4.0, value_per_move: float = USD_PER_LOT_PER_DOLLAR,
        roc_min: float = 0.15, tp_rrr: float = 2.0, tp_close_frac: float = 0.7,
        partial_frac: float = 0.7, partial_at_r: float = 2.0,
        spike_ratio: float = 2.5) -> list[dict]:
```

Replace the `cfg = OrbConfig(...)` block (lines 248-255) with:

```python
    cfg = _orb_cfg(candles, qty, stop_min, stop_max, roc_min, tp_rrr, tp_close_frac)
```

Replace the babysitter/spike lines (currently 258-259):

```python
    sitter = Babysitter(partial_frac=partial_frac, partial_at_r=partial_at_r)
    spike = SpikeCancel(ratio=spike_ratio)
```

Leave the rest of `run()` untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sim_run_params.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: all previously-green tests still pass (255+ tests), plus the 3 new ones.

- [ ] **Step 6: Commit**

```bash
git add scripts/sim_realistic.py tests/test_sim_run_params.py
git commit -m "feat(sim): parameterize ORB run() (roc/RR/partial/spike); behavior-preserving"
```

---

### Task 2: `sweep_orb.py` pure helpers

The composable, dependency-free building blocks for the sweep/grid/sign-test harness. Pure functions, fully unit-tested before any I/O wiring.

**Files:**
- Create: `scripts/sweep_orb.py`
- Test: `tests/test_sweep_orb.py` (create)

**Interfaces:**
- Produces:
  - `split_halves(candles: list) -> tuple[list, list]` — split by index midpoint.
  - `live_filter(trades: list[dict]) -> list[dict]` — deadzone + Q2Q3.
  - `grid_iter(axes: dict[str, list]) -> list[dict]` — cartesian product → list of param dicts.
  - `sign_stable(metric_dicts: list[dict], pf_min: float = 1.0) -> bool` — every window has `pf >= pf_min` and `pnl > 0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep_orb.py
"""Pure helpers for the ORB sweep/grid/sign-stability harness."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from sweep_orb import grid_iter, live_filter, sign_stable, split_halves  # noqa: E402


def test_split_halves_even_and_odd():
    assert split_halves([1, 2, 3, 4]) == ([1, 2], [3, 4])
    assert split_halves([1, 2, 3, 4, 5]) == ([1, 2], [3, 4, 5])


def test_live_filter_keeps_only_nondead_q2q3():
    trades = [
        {"zone": "premium", "day_q": "Q2", "pnl": 1},   # keep
        {"zone": "dead_zone", "day_q": "Q3", "pnl": 1},  # drop (dead)
        {"zone": "discount", "day_q": "Q1", "pnl": 1},   # drop (Q1)
        {"zone": "discount", "day_q": "Q3", "pnl": 1},   # keep
    ]
    kept = live_filter(trades)
    assert len(kept) == 2
    assert all(t["zone"] != "dead_zone" and t["day_q"] in ("Q2", "Q3") for t in kept)


def test_grid_iter_cartesian_product():
    axes = {"roc_min": [0.1, 0.2], "tp_rrr": [2, 3]}
    combos = grid_iter(axes)
    assert len(combos) == 4
    assert {"roc_min": 0.1, "tp_rrr": 2} in combos
    assert {"roc_min": 0.2, "tp_rrr": 3} in combos


def test_sign_stable_all_positive():
    good = [{"pf": 1.5, "pnl": 100}, {"pf": 1.1, "pnl": 10}, {"pf": 2.0, "pnl": 50}]
    bad = [{"pf": 1.5, "pnl": 100}, {"pf": 0.8, "pnl": -20}]
    assert sign_stable(good) is True
    assert sign_stable(bad) is False
    assert sign_stable(good, pf_min=1.3) is False   # 1.1 < 1.3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sweep_orb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sweep_orb'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/sweep_orb.py  (top of file — pure helpers only for now)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sweep_orb.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/sweep_orb.py tests/test_sweep_orb.py
git commit -m "feat(sweep): pure helpers for ORB sweep/grid/sign-stability"
```

---

### Task 3: `sweep_orb.py` scoring + sweep/grid/gate wiring

Wire the pure helpers to the simulator: score one candle set across full/first/second splits, sweep timeframes, run a param grid, and run the multi-window OOS gate. Add a CLI.

**Files:**
- Modify: `scripts/sweep_orb.py` (append below the helpers)
- Test: `tests/test_sweep_orb.py` (append a wiring smoke test)

**Interfaces:**
- Consumes: `sim_realistic.load_csv`, `sim_realistic.aggregate_candles`, `sim_realistic.run`, `sim_realistic.metrics`; helpers from Task 2.
- Produces:
  - `score(candles, spec, params, spread) -> dict` — keys `full`, `first`, `second`, each a `metrics()` dict computed on `live_filter(run(...))`.
  - `tf_sweep(candles, spec, params, tfs, spread) -> dict[str, dict]` — `score` per timeframe label.
  - `param_grid(candles, spec, axes, spread) -> list[tuple[dict, dict]]` — `(params, score)` sorted by `full["pf"]` desc.
  - `oos_gate(window_paths, spec, params, spread, pf_min=1.0) -> tuple[bool, dict]` — `(stable, per_window_full_metrics)`.
  - `SPECS: dict[str, dict]` — `"US100"` and `"XAUUSD"` spec dicts (per Global Constraints).
  - CLI: `python scripts/sweep_orb.py tf|grid|gate --symbol US100 [--spread X] [...]`.

- [ ] **Step 1: Write the failing wiring test**

```python
# append to tests/test_sweep_orb.py
from sweep_orb import SPECS, score, tf_sweep  # noqa: E402
from sim_realistic import load_csv            # noqa: E402

US100 = "data/us100_1m_20260303_20260612.csv"


def test_score_shape_on_real_slice():
    candles = load_csv([US100])[:12000]
    s = score(candles, SPECS["US100"], params={}, spread=1.0)
    assert set(s) == {"full", "first", "second"}
    for k in s:
        assert "pf" in s[k] and "pnl" in s[k] and "n" in s[k]


def test_tf_sweep_returns_each_tf():
    candles = load_csv([US100])[:12000]
    out = tf_sweep(candles, SPECS["US100"], params={}, tfs=["1m", "5m"], spread=1.0)
    assert set(out) == {"1m", "5m"}
    assert "full" in out["1m"] and "full" in out["5m"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sweep_orb.py::test_score_shape_on_real_slice -v`
Expected: FAIL — `ImportError: cannot import name 'SPECS'`.

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/sweep_orb.py`:

```python
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
                    help="price-unit spread (default 1.0 US100 / 0.10 gold)")
    ap.add_argument("--pf-min", type=float, default=1.0)
    args = ap.parse_args()

    spec = SPECS[args.symbol]
    spread = args.spread if args.spread is not None else (
        1.0 if args.symbol == "US100" else 0.10)
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
        axes = {
            "roc_min": [0.10, 0.15, 0.20, 0.25],
            "stop_min": [2.0, 2.6, 3.0], "stop_max": [4.0, 5.2, 6.0],
            "tp_rrr": [1.5, 2.0, 3.0],
            "partial_frac": [0.5, 0.7],
        }
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sweep_orb.py -v`
Expected: PASS (all helper tests + 2 wiring tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/sweep_orb.py tests/test_sweep_orb.py
git commit -m "feat(sweep): scoring + TF sweep + grid + OOS gate wiring & CLI"
```

---

### Task 4: `check_spread.py` — real spread profiler

Read-only MT5 per-bar spread distribution + live snapshot, with a pure, testable stats core.

**Files:**
- Create: `scripts/check_spread.py`
- Test: `tests/test_check_spread.py` (create)

**Interfaces:**
- Produces: `spread_stats(spread_points: list[int], point: float) -> dict` with keys `n, min, median, p90, p99, max, mean` (price units = `points * point`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_spread.py
"""Pure spread-stats core for check_spread.py."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from check_spread import spread_stats  # noqa: E402


def test_spread_stats_converts_points_to_price():
    # points 1..10, point=0.1 -> prices 0.1..1.0
    s = spread_stats(list(range(1, 11)), point=0.1)
    assert s["n"] == 10
    assert abs(s["min"] - 0.1) < 1e-9
    assert abs(s["max"] - 1.0) < 1e-9
    assert abs(s["mean"] - 0.55) < 1e-9
    assert abs(s["median"] - 0.55) < 1e-9   # midpoint of 0.5/0.6


def test_spread_stats_percentiles_monotonic():
    s = spread_stats(list(range(1, 101)), point=1.0)
    assert s["median"] <= s["p90"] <= s["p99"] <= s["max"]
    assert abs(s["p90"] - 90.0) < 1.5


def test_spread_stats_empty():
    s = spread_stats([], point=0.1)
    assert s["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_check_spread.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_spread'`.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/check_spread.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_check_spread.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_spread.py tests/test_check_spread.py
git commit -m "feat: read-only MT5 real-spread profiler (check_spread.py)"
```

---

### Task 5: RUN A1 — measure US100 real spread

**Files:** none modified. Produces a recorded measurement.

- [ ] **Step 1: Run the profiler (requires the MT5 terminal running)**

Run: `python scripts/check_spread.py US100.ecn --bars 100000`
Expected: a `per-bar spread (PRICE units)` line with median/p90, and a live ask-bid line.

- [ ] **Step 2: Record the result**

Append to `PROGRESS.md` (dated entry): the median / p90 / max price-unit spread and the live ask-bid, with the one-line comparison vs the assumed **1.0pt**. State plainly whether real ≈ 1.0, below, or above.

- [ ] **Step 3: If MT5 is not available**

If the script returns code 2/3 (no MT5), record that A1 is blocked and that GATE A3 cannot be evaluated until A1 runs. Do not proceed to deploy (Task 8); Track A pauses here. Track C (Task 9) uses CSV files and can still proceed.

---

### Task 6: RUN A2 — US100 ORB timeframe sweep

**Files:** none modified. Produces a recorded table.

- [ ] **Step 1: Run the sweep on the newest US100 window**

Run: `python scripts/sweep_orb.py tf --symbol US100 --csv data/us100_1m_20260310_20260619.csv`
Expected: per-TF (1m/2m/3m/5m/15m) full/first/second LIVE metrics, with the CONFOUND banner.

- [ ] **Step 2: Run the sweep on the older window too (cross-window check)**

Run: `python scripts/sweep_orb.py tf --symbol US100 --csv data/us100_1m_20260303_20260612.csv`
Expected: same shape, second window.

- [ ] **Step 3: Record + interpret**

Append a dated `PROGRESS.md` entry with both tables. For each TF, mark whether PF is sign-stable (positive across full/first/second AND across both windows). Apply the confound caveat: a higher TF "winning" under 1m-tuned params is a **candidate**, not a decision. Default conclusion stands at **1m** unless a higher TF is clearly and stably better on both windows.

---

### Task 7: RUN A3 — re-backtest chosen config at real spread (GATE)

**Files:** none modified. Produces a gate decision.

- [ ] **Step 1: Re-run the chosen config (default 1m) at the measured spread from Task 5**

Run (substitute `<median>` and `<p90>` from A1):
`python scripts/sweep_orb.py tf --symbol US100 --csv data/us100_1m_20260310_20260619.csv --spread <median>`
then again with `--spread <p90>` as a stress case.
Expected: LIVE full/first/second metrics at real cost.

- [ ] **Step 2: Evaluate GATE A3**

PASS if LIVE PF stays sign-stable and `full` PF ≥ ~1.3 at the median spread. Record PASS/FAIL in `PROGRESS.md` with the numbers.

- [ ] **Step 3: On FAIL**

If sign flips or PF < ~1.3 at real spread: record that the US100 edge does not survive real costs at the validated config; **do NOT change live**; recommend pausing/reducing the live US100 bot and reassessing. Stop Track A here.

---

### Task 8: A4 — deploy decision (expected: confirm, no change)

**Files:** possibly `scripts/bots.ps1` (only if config drift found).

- [ ] **Step 1: Confirm live config matches the validated one**

Run: `powershell -File scripts/bots.ps1 status`
Confirm the US100 line shows `alive=True feeding=True`, and that the launch args in `bots.ps1` are still `ORB 1m, --qty 0.40 --stop-min 15 --stop-max 30 --roc-min 0.15 --tp-rrr 2 --trueopen-filter deadzone --quarter-filter q2q3`.

- [ ] **Step 2: Decide**

- If A2 winner = 1m and GATE A3 = PASS: **no change.** US100 is already deployed at the validated config; size stays **0.40**. Document "A4 = confirm, no-op."
- If A2 produced a stable higher-TF winner: **do not auto-ship.** Record a follow-up: live ORB consumes a 1m feed; a higher-TF deploy needs feed-side aggregation (separate spec). Keep 1m live for now.
- If config drift was found in Step 1: fix only the drifted flag in `bots.ps1` `$ENABLED` US100 entry, keeping `qty 0.40`, then `powershell -File scripts/bots.ps1 restart` and re-confirm status. Commit the `bots.ps1` change.

- [ ] **Step 3: Record** the A4 decision in `STATUS.md` and `PROGRESS.md`.

---

### Task 9: RUN C — gold ORB param grid + OOS gate

**Files:** none modified except docs (`DECISIONS.md`, `STATUS.md`, `PROGRESS.md`).

- [ ] **Step 1: Run the grid on one in-sample gold window at real spread**

Run: `python scripts/sweep_orb.py grid --symbol XAUUSD --csv data/xauusd_1m_20260303_20260612.csv --spread 0.10`
Expected: top-15 param combos ranked by full LIVE PF, each with full/first/second metrics.

- [ ] **Step 2: For each promising winner, run the OOS gate across all 3 gold windows**

Edit the `params` dict in `sweep_orb.py main()`'s `gate` branch to the candidate config (e.g. `{"roc_min":0.20,"stop_min":2.6,"stop_max":5.2,"tp_rrr":2.0,"partial_frac":0.7}`), then:
Run: `python scripts/sweep_orb.py gate --symbol XAUUSD --spread 0.10`
Expected: per-window LIVE metrics + `stable=True/False`. Repeat at `--spread 0.12` (stress).
Revert the temporary `params` edit afterward (leave `params = {}`); do not commit the scratch edit.

- [ ] **Step 3: Apply the hard gate**

A config survives ONLY if `stable=True` (PF ≥ 1.0 and pnl > 0 across all 3 windows) at $0.10, and ideally still positive at $0.12. Discard in-sample-only winners explicitly.

- [ ] **Step 4: Record the verdict in `DECISIONS.md`**

- If ≥1 survivor: new decision entry "gold ORB candidate edge" with the surviving config and its three per-window PFs; note it is a candidate pending a deploy spec.
- If none survive: new decision entry reaffirming **D-020** — gold has no replicable ORB edge either; gold closed to further param/window search; next gold attempt requires a structurally new signal.

- [ ] **Step 5: Commit docs**

```bash
git add DECISIONS.md STATUS.md PROGRESS.md
git commit -m "docs: gold ORB grid verdict + US100 verify/deploy results"
```

---

## Self-Review

**Spec coverage:**
- A1 real-spread check → Task 4 (tool) + Task 5 (run). ✓
- A2 TF sweep + sign test + confound flag → Task 3 (`tf_sweep`, CONFOUND banner) + Task 6 (run). ✓
- A3 re-backtest @ real spread + GATE → Task 7. ✓
- A4 deploy decision, keep 0.40 → Task 8. ✓
- C1 grid → Task 3 (`param_grid`, axes) + Task 9 Step 1. ✓
- C2 OOS gate (split + 3 windows, discard in-sample-only) → Task 3 (`oos_gate`, `sign_stable`) + Task 9 Steps 2-3. ✓
- C3 verdict in DECISIONS → Task 9 Step 4. ✓
- New code: `check_spread.py` (Task 4), ORB TF aggregation (reused via `tf_sweep`, Task 3), `sweep_orb.py` (Tasks 2/3). ✓
- Behavior-preserving `run()` → Task 1 with regression test. ✓
- No live change until A3 → enforced in Task 7/8 ordering + Global Constraints. ✓

**Placeholder scan:** No TBD/TODO. The `gate` CLI branch uses an explicit-edit `params` dict (documented in Task 9 Step 2 with revert instruction) rather than fragile multi-value arg parsing — intentional and spelled out, not a placeholder.

**Type consistency:** `score()` returns `{full,first,second}` of `metrics()` dicts (keys `n,pnl,win,avg,pf,dd,dd_pct`) — consumed consistently by `_fmt`, `sign_stable` (`pf`,`pnl`), and `param_grid` sort (`full["pf"]`). `live_filter`/`split_halves`/`grid_iter`/`sign_stable` signatures match between Task 2 definition and Task 3 usage. `run()` keyword names match Task 1's new signature exactly.

---

## Execution Handoff
(see end of message)
