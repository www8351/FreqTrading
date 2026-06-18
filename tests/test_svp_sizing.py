"""Structural-stop dynamic sizing."""

from pytest import approx

from orb.svp.sizing import compute_lot


def test_basic_size_to_5pct_risk():
    # risk_$ = 500*5% = 25; stop_dist=2.0; value_per_move=100 -> 0.125 -> snap 0.12
    lot = compute_lot(balance=500, risk_pct=5, entry=2000.0, stop=1998.0,
                      value_per_move=100.0, vol_min=0.01, vol_step=0.01, vol_max=10.0)
    assert lot == approx(0.12)


def test_zero_stop_distance_skips():
    assert compute_lot(500, 5, 2000.0, 2000.0, 100.0, 0.01, 0.01, 10.0) == 0.0


def test_below_vol_min_skips_rather_than_overrisk():
    # a very wide structural stop -> raw lot < vol_min -> skip (don't breach risk)
    assert compute_lot(500, 5, 2000.0, 1000.0, 100.0, 0.01, 0.01, 10.0) == 0.0


def test_clamps_up_to_vol_max():
    # tiny stop -> huge raw lot -> clamped to vol_max
    lot = compute_lot(100_000, 5, 2000.0, 1999.99, 100.0, 0.01, 0.01, 0.5)
    assert lot == 0.5


def test_max_risk_cap_shrinks_lot():
    # daily budget caps risk at $10 -> 10/(2*100) = 0.05
    lot = compute_lot(500, 5, 2000.0, 1998.0, 100.0, 0.01, 0.01, 10.0, max_risk=10.0)
    assert lot == approx(0.05)


def test_exhausted_daily_budget_skips():
    assert compute_lot(500, 5, 2000.0, 1998.0, 100.0, 0.01, 0.01, 10.0,
                       max_risk=0.0) == 0.0


def test_degenerate_inputs_skip():
    assert compute_lot(0, 5, 2000.0, 1998.0, 100.0, 0.01, 0.01, 10.0) == 0.0
    assert compute_lot(500, 0, 2000.0, 1998.0, 100.0, 0.01, 0.01, 10.0) == 0.0
    assert compute_lot(500, 5, 2000.0, 1998.0, 0.0, 0.01, 0.01, 10.0) == 0.0
