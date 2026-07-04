"""SmcConfig construction and validation rules."""

from dataclasses import FrozenInstanceError, replace

import pytest

from orb.smc import SMC_MAGIC
from orb.smc.config import SmcConfig


def test_defaults_construct():
    cfg = SmcConfig()
    assert cfg.trigger_tf_min == 15
    assert cfg.htf_min == 240
    assert cfg.d1_min == 1440
    assert cfg.partial_levels == ((5.0, 0.40), (7.0, 0.30))
    assert cfg.trail_mode == "swing"


def test_magic():
    assert SMC_MAGIC == 20260621


def test_row_size_default():
    assert SmcConfig().row_size == pytest.approx(1.0)  # 100 ticks * 0.01


def test_row_size_scales():
    assert SmcConfig(ticks_per_row=50, tick_size=0.1).row_size == pytest.approx(5.0)


def test_frozen():
    cfg = SmcConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.trigger_tf_min = 5


@pytest.mark.parametrize("kw", [
    # periods / counts >= 1
    {"trigger_tf_min": 0},
    {"htf_min": 0},
    {"swing_lookback": 0},
    {"max_swings": 0},
    {"ob_confirm_bars": 0},
    {"max_blocks": 0},
    {"ob_expiry_bars": 0},
    {"atr_period": 0},
    {"ticks_per_row": 0},
    {"min_profile_bars": 0},
    {"vol_sma_period": 0},
    {"max_trades_per_day": 0},
    # positive floats
    {"tick_size": 0.0},
    {"tick_size": -0.01},
    {"poc_tol": -1.0},
    {"disp_atr_mult": 0.0},
    {"vol_mult": 0.0},
    {"trail_atr_mult": 0.0},
    {"trail_buffer": -0.1},
    # disp_body_frac in (0, 1) exclusive
    {"disp_body_frac": 0.0},
    {"disp_body_frac": 1.0},
    {"disp_body_frac": -0.2},
    # risk_pct in (0, 10]
    {"risk_pct": 0.0},
    {"risk_pct": 10.5},
    {"risk_pct": -1.0},
    # trail_mode enum
    {"trail_mode": "fixed"},
    {"trail_mode": ""},
    # timeframe ordering / d1 fixed
    {"trigger_tf_min": 240},                     # trigger !< htf
    {"htf_min": 1440},                           # htf !< d1
    {"trigger_tf_min": 500, "htf_min": 400},     # trigger > htf
    {"d1_min": 720, "htf_min": 240},             # d1 != 1440
    # partial_levels
    {"partial_levels": ((0.0, 0.4),)},                       # r <= 0
    {"partial_levels": ((-1.0, 0.4),)},                      # r < 0
    {"partial_levels": ((5.0, 0.4), (5.0, 0.3))},            # not ascending (equal)
    {"partial_levels": ((7.0, 0.4), (5.0, 0.3))},            # descending
    {"partial_levels": ((5.0, 0.0),)},                       # frac <= 0
    {"partial_levels": ((5.0, 1.0),)},                       # frac >= 1
    {"partial_levels": ((5.0, 0.6), (7.0, 0.6))},            # sum > 1
    {"partial_levels": ((5.0,),)},                           # malformed pair
    # final_tp_r: 0 or > last partial r
    {"final_tp_r": 7.0},                          # == last partial r
    {"final_tp_r": 6.0},                          # < last partial r
    {"final_tp_r": -1.0},                         # negative
    # be_at_r / trail_start_r > 0
    {"be_at_r": 0.0},
    {"be_at_r": -1.0},
    {"trail_start_r": 0.0},
    {"trail_start_r": -0.5},
    # stop_max_dist > stop_buffer > 0
    {"stop_buffer": 0.0},
    {"stop_buffer": -0.5},
    {"stop_buffer": 15.0},                        # == stop_max_dist
    {"stop_buffer": 20.0},                        # > stop_max_dist
    {"stop_max_dist": 0.4},                       # < stop_buffer
    # min_confluences in 1..6
    {"min_confluences": 0},
    {"min_confluences": 7},
])
def test_invalid_raises(kw):
    with pytest.raises(ValueError):
        SmcConfig(**kw)


@pytest.mark.parametrize("kw", [
    {"final_tp_r": 0.0},                                     # 0 disables final TP
    {"final_tp_r": 7.5},                                     # just above last partial
    {"partial_levels": ()},                                  # no partials is legal
    {"partial_levels": (), "final_tp_r": 0.5},               # any r>0 with no partials
    {"partial_levels": ((2.0, 0.5), (4.0, 0.5))},            # fracs sum == 1.0
    {"trail_mode": "atr"},
    {"trail_start_r": 1.0, "be_at_r": 2.0},                  # independent, both > 0
    {"min_confluences": 1},
    {"min_confluences": 6},
    {"trail_buffer": 0.0},
    {"poc_tol": 0.0},
])
def test_valid_edges(kw):
    SmcConfig(**kw)  # must not raise


def test_replace_revalidates():
    cfg = SmcConfig()
    with pytest.raises(ValueError):
        replace(cfg, risk_pct=0.0)
