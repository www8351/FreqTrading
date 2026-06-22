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
