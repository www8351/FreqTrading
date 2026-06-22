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
