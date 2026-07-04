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
