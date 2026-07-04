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
