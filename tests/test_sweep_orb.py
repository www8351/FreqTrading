"""Pure helpers for the ORB sweep/grid/sign-stability harness."""
from __future__ import annotations

import pathlib
import sys

import pytest

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


# ---------------------------------------------------------------------------
# Wiring smoke tests (Task 3): exercise real signal generation on a data slice
# ---------------------------------------------------------------------------
from sweep_orb import SPECS, score, tf_sweep  # noqa: E402
from sim_realistic import load_csv            # noqa: E402

US100 = "data/us100_1m_20260303_20260612.csv"

# data/ CSVs are gitignored (45 MB, local-only) — skip the data-backed wiring
# smoke tests when absent (e.g. CI). The pure-helper tests above still run.
_needs_data = pytest.mark.skipif(
    not (pathlib.Path(__file__).resolve().parents[1] / US100).exists(),
    reason="data/ backtest CSVs not present (gitignored; local-only)",
)


@_needs_data
def test_score_shape_on_real_slice():
    candles = load_csv([US100])[:12000]
    s = score(candles, SPECS["US100"], params={}, spread=1.0)
    assert set(s) == {"full", "first", "second"}
    for k in s:
        assert "pf" in s[k] and "pnl" in s[k] and "n" in s[k]


@_needs_data
def test_tf_sweep_returns_each_tf():
    candles = load_csv([US100])[:12000]
    out = tf_sweep(candles, SPECS["US100"], params={}, tfs=["1m", "5m"], spread=1.0)
    assert set(out) == {"1m", "5m"}
    assert "full" in out["1m"] and "full" in out["5m"]
