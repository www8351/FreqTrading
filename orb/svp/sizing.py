"""Structural-stop dynamic position sizing.

Given the actual distance to a structural stop (placed beyond an HVN/VAH/VAL
shelf), size the lot so the loss at that stop is ``risk_pct`` of balance — and
never more than an optional hard dollar cap (the remaining daily-loss budget).

Pure: takes broker specs as plain floats, no MetaTrader5 import. Reuses the
sizing math from ``Brain_X.md`` / ``scripts/symbol_specs.py``:
    value_per_move = tick_value / tick_size      ($ per 1.0 price move per lot)
    lot = risk_$ / (stop_distance * value_per_move)
then snapped down to the broker volume step.
"""

from __future__ import annotations

import math


def _floor_to_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    n = math.floor(x / step + 1e-9)
    return round(n * step, 10)


def compute_lot(
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
    value_per_move: float,
    vol_min: float,
    vol_step: float,
    vol_max: float,
    *,
    max_risk: float | None = None,
) -> float:
    """Lot size for a structural stop.

    Returns ``0.0`` (skip the trade) when inputs are degenerate OR when the
    smallest tradable lot (``vol_min``) would already exceed the risk budget —
    honouring "never breach risk-per-trade / the daily-loss limit". The result
    is clamped up to ``vol_max`` and snapped down to ``vol_step``.

    ``max_risk`` caps the dollar risk (e.g. ``max_daily_loss - day_pnl``) so a
    single SVP trade can never blow through the remaining daily budget.
    """
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or value_per_move <= 0 or balance <= 0 or risk_pct <= 0:
        return 0.0

    risk_dollars = balance * (risk_pct / 100.0)
    if max_risk is not None:
        if max_risk <= 0:
            return 0.0
        risk_dollars = min(risk_dollars, max_risk)

    lot_raw = risk_dollars / (stop_dist * value_per_move)
    lot = _floor_to_step(lot_raw, vol_step)
    if lot < vol_min:
        # vol_min would over-risk the budget -> skip rather than breach it
        return 0.0
    return min(lot, vol_max)
