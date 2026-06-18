"""M3 tests: GDELT parse, geopolitics assess/merge, war-spike guard close E2E."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from macro import daemon
from macro.build import build_state
from macro.collectors import gdelt
from macro.geopolitics import GeoResult, assess, merge_geo
from macro.state_writer import neutral_state, write_state
from orb.macroguard import MacroGuard
from orb.models import Direction, Signal, SignalKind, State

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)
SYMS = ("XAUUSD", "US100", "US500", "XAGUSD")

WAR = GeoResult(risk_off=True, war_spike=True, score=0.5,
                drivers=("gdelt_tone_drop=6", "vix=30"), confidence=0.9)
SOFT = GeoResult(risk_off=True, war_spike=False, score=0.4,
                 drivers=("vix=27",), confidence=0.6)


def mk_entry(direction):
    return Signal(ts=NOW, kind=SignalKind.ENTRY, direction=direction, price=2000.0,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason="b", qty=0.04)


# --- assess -----------------------------------------------------------------
def test_assess_war_spike_needs_tone_and_proxy():
    g = assess(tone_now=-6.0, tone_base=0.0, vol_z=3.0, vix=30.0)
    assert g.war_spike and g.risk_off and g.confidence == 0.9


def test_assess_tone_only_is_soft_riskoff():
    g = assess(tone_now=-6.0, tone_base=0.0, vol_z=3.0, vix=15.0)
    assert g.risk_off and not g.war_spike and g.confidence == 0.6


def test_assess_proxy_only_is_soft_riskoff():
    g = assess(tone_now=0.0, tone_base=0.0, vol_z=0.0, vix=30.0)
    assert g.risk_off and not g.war_spike


def test_assess_low_volume_no_tone_spike():
    g = assess(tone_now=-6.0, tone_base=0.0, vol_z=0.5, vix=15.0)
    assert not g.risk_off and not g.war_spike and g.confidence == 0.0


def test_assess_calm():
    g = assess(tone_now=0.0, tone_base=0.0, vol_z=0.0, vix=15.0)
    assert not g.risk_off and not g.war_spike


# --- merge_geo --------------------------------------------------------------
def test_merge_war_spike_tilts_and_blacks_out():
    s = merge_geo(neutral_state(generated_at="2026-06-17T14:00:00Z"), WAR, NOW)
    assert s["assets"]["XAUUSD"]["bias"] == "bullish"      # metals bid
    assert s["assets"]["US100"]["bias"] == "bearish"       # equities offered
    assert s["global"]["risk_regime"] == "risk_off"
    assert s["global"]["blackout"] == {"active": True,
                                        "until": "2026-06-17T16:00:00Z",
                                        "reason": "war_spike"}
    assert "risk_off" in s["assets"]["XAUUSD"]["drivers"]


def test_merge_soft_riskoff_no_blackout():
    s = merge_geo(neutral_state(generated_at="2026-06-17T14:00:00Z"), SOFT, NOW)
    assert s["global"]["risk_regime"] == "risk_off"
    assert s["global"]["blackout"]["active"] is False
    assert s["global"]["confidence"] == 0.6


def test_merge_none_or_calm_unchanged():
    base = neutral_state(generated_at="2026-06-17T14:00:00Z")
    assert merge_geo(base, None, NOW)["global"]["risk_regime"] == "neutral"
    calm = GeoResult(False, False, 0.0, (), 0.0)
    assert merge_geo(base, calm, NOW)["global"]["risk_regime"] == "neutral"


# --- GDELT parse ------------------------------------------------------------
def test_parse_timeline():
    data = {"timeline": [{"series": "Average Tone", "data": [
        {"date": "20260617T120000Z", "value": -2.5},
        {"date": "20260617T130000Z", "value": -5.0},
    ]}]}
    out = gdelt.parse_timeline(data)
    assert out[0][0] == datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    assert out[1][1] == -5.0


def test_tone_features_spike():
    tn, tb, vz = gdelt.tone_features([0, 0, 0, -6], [100, 100, 100, 300])
    assert tn == -6 and tb == 0.0 and vz == 10.0


# --- build_state with geo ---------------------------------------------------
def test_build_state_war_spike():
    s = build_state([], NOW, geo=WAR)
    assert s["global"]["blackout"]["reason"] == "war_spike"
    assert s["assets"]["XAUUSD"]["bias"] == "bullish"


# --- daemon with an injected geo provider -----------------------------------
def test_daemon_run_with_geo_provider(tmp_path):
    out = tmp_path / "macro_state.json"
    daemon.run(out=str(out), opener=lambda url: b"[]",      # empty calendar
               geo_provider=lambda now: WAR,
               now_fn=lambda: NOW, write_interval=0.0, max_iters=1)
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["global"]["blackout"]["reason"] == "war_spike"


# --- guard E2E --------------------------------------------------------------
def test_guard_closes_on_war_spike(tmp_path):
    p = str(tmp_path / "macro_state.json")
    write_state(build_state([], NOW, geo=WAR), p)
    g = MacroGuard("US100", p, now_fn=lambda: NOW)
    ro, reason = g.risk_off_now()
    assert ro and reason == "blackout:war_spike"            # guard CLOSES
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "VETO"


def test_guard_soft_riskoff_vetoes_but_no_close(tmp_path):
    p = str(tmp_path / "macro_state.json")
    write_state(build_state([], NOW, geo=SOFT), p)
    g = MacroGuard("US100", p, now_fn=lambda: NOW, conf_min=0.6)
    assert g.risk_off_now()[0] is False                     # soft -> no close
    # US100 tilted bearish -> a LONG conflicts -> vetoed in filter/guard
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "VETO"
    assert "macro_bias_conflict" in g.evaluate_entry(mk_entry(Direction.LONG)).reason