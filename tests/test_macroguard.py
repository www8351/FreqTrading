"""MacroGuard unit tests — pure, network-free: inject a state file + a clock."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from macro.state_writer import neutral_state, write_state
from orb.macroguard import MacroGuard, MacroState
from orb.models import Direction, Signal, SignalKind, State

UTC = timezone.utc
GEN = "2026-06-16T10:40:00Z"
NOW = datetime(2026, 6, 16, 10, 40, 30, tzinfo=UTC)   # 30s after GEN -> fresh
STALE_GEN = "2026-06-16T10:00:00Z"                    # 40m before NOW -> stale @ttl300


def _now():
    return NOW


def mk_entry(direction=Direction.LONG, qty=0.04) -> Signal:
    return Signal(ts=NOW, kind=SignalKind.ENTRY, direction=direction, price=2000.0,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason="breakout", qty=qty)


def make_guard(tmp_path, state: dict | None, *, symbol="XAUUSD.ecn",
               default_when_stale="allow", conf_min=0.6) -> MacroGuard:
    p = tmp_path / "macro_state.json"
    if state is not None:
        write_state(state, str(p))
    return MacroGuard(symbol=symbol, state_path=str(p),
                      default_when_stale=default_when_stale,
                      conf_min=conf_min, now_fn=_now)


def with_asset(key, score, conf, gen=GEN) -> dict:
    # `conf` is the GLOBAL confidence (schema has no per-asset confidence); the
    # bias-conflict gate uses global confidence against the per-asset score sign.
    s = neutral_state(generated_at=gen)
    s["global"]["confidence"] = conf
    s["assets"][key] = {"bias": "x", "score": score, "horizon": "intraday",
                        "drivers": ["test"]}
    return s


def with_blackout(reason="FOMC", gen=GEN, regime="neutral", conf=0.0) -> dict:
    s = neutral_state(generated_at=gen)
    s["global"]["risk_regime"] = regime
    s["global"]["confidence"] = conf
    s["global"]["blackout"] = {"active": True,
                               "until": "2026-06-16T11:10:00Z", "reason": reason}
    return s


# --- happy path -------------------------------------------------------------
def test_allow_when_fresh_neutral(tmp_path):
    g = make_guard(tmp_path, neutral_state(generated_at=GEN))
    d = g.evaluate_entry(mk_entry())
    assert d.action == "ALLOW"
    assert d.qty == 0.04                       # qty passes through unchanged in M0


def test_neutral_state_parses_all_symbols():
    st = MacroState.from_dict(neutral_state(generated_at=GEN))
    assert set(st.assets) == {"XAUUSD", "US100", "US500", "XAGUSD"}
    assert st.blackout.active is False


# --- blackout ---------------------------------------------------------------
def test_veto_on_blackout(tmp_path):
    g = make_guard(tmp_path, with_blackout(reason="FOMC"))
    d = g.evaluate_entry(mk_entry())
    assert d.action == "VETO"
    assert d.reason.startswith("blackout:FOMC")


# --- bias conflict ----------------------------------------------------------
def test_veto_long_against_bearish(tmp_path):
    g = make_guard(tmp_path, with_asset("XAUUSD", -0.5, 0.8))
    d = g.evaluate_entry(mk_entry(Direction.LONG))
    assert d.action == "VETO"
    assert "macro_bias_conflict" in d.reason


def test_veto_short_against_bullish(tmp_path):
    g = make_guard(tmp_path, with_asset("XAUUSD", 0.5, 0.8))
    d = g.evaluate_entry(mk_entry(Direction.SHORT))
    assert d.action == "VETO"


def test_allow_aligned_bias(tmp_path):
    g = make_guard(tmp_path, with_asset("XAUUSD", 0.5, 0.8))
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "ALLOW"


def test_allow_low_confidence_conflict(tmp_path):
    g = make_guard(tmp_path, with_asset("XAUUSD", -0.5, 0.4))   # conf < 0.6
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "ALLOW"


def test_allow_zero_score_deadband(tmp_path):
    g = make_guard(tmp_path, with_asset("XAUUSD", 0.0, 0.95))
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "ALLOW"


def test_symbol_suffix_normalized(tmp_path):
    # bot symbol "US100" (no suffix) maps to the bare "US100" asset key
    g = make_guard(tmp_path, with_asset("US100", -0.7, 0.9), symbol="US100")
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "VETO"


# --- stale / missing fail-safe ---------------------------------------------
def test_stale_allow(tmp_path):
    g = make_guard(tmp_path, neutral_state(generated_at=STALE_GEN),
                   default_when_stale="allow")
    d = g.evaluate_entry(mk_entry())
    assert d.action == "ALLOW"
    assert d.reason == "macro_stale_allow"


def test_stale_block(tmp_path):
    g = make_guard(tmp_path, neutral_state(generated_at=STALE_GEN),
                   default_when_stale="block")
    d = g.evaluate_entry(mk_entry())
    assert d.action == "VETO"
    assert d.reason == "macro_stale_block"


def test_missing_file_allow(tmp_path):
    g = make_guard(tmp_path, None, default_when_stale="allow")
    d = g.evaluate_entry(mk_entry())
    assert d.action == "ALLOW"
    assert d.reason == "macro_absent_allow"


def test_missing_file_block(tmp_path):
    g = make_guard(tmp_path, None, default_when_stale="block")
    assert g.evaluate_entry(mk_entry()).action == "VETO"


def test_corrupt_json_keeps_last_good(tmp_path):
    g = make_guard(tmp_path, neutral_state(generated_at=GEN))
    assert g.evaluate_entry(mk_entry()).action == "ALLOW"   # primes last-good
    (tmp_path / "macro_state.json").write_text("{ this is not json", encoding="utf-8")
    # corrupt parse -> falls back to last good (still fresh) -> ALLOW
    assert g.evaluate_entry(mk_entry()).action == "ALLOW"


# --- risk-off (guard mode) --------------------------------------------------
def test_risk_off_on_blackout(tmp_path):
    g = make_guard(tmp_path, with_blackout(reason="war_spike"))
    ro, reason = g.risk_off_now()
    assert ro is True
    assert reason.startswith("blackout:war_spike")


def test_risk_off_requires_blackout_not_regime(tmp_path):
    # a soft risk_off regime (no blackout) must NOT trigger a proactive close —
    # only a hard blackout (scheduled window or confirmed war_spike) closes.
    s = neutral_state(generated_at=GEN)
    s["global"]["risk_regime"] = "risk_off"
    s["global"]["confidence"] = 0.8
    g = make_guard(tmp_path, s)
    assert g.risk_off_now()[0] is False


def test_risk_off_false_when_neutral(tmp_path):
    g = make_guard(tmp_path, neutral_state(generated_at=GEN))
    assert g.risk_off_now() == (False, "")


def test_risk_off_false_when_stale(tmp_path):
    # blackout active but state stale -> must NOT act on a blind brain
    g = make_guard(tmp_path, with_blackout(reason="FOMC", gen=STALE_GEN))
    assert g.risk_off_now()[0] is False


# --- validation -------------------------------------------------------------
def test_invalid_default_when_stale():
    with pytest.raises(ValueError):
        MacroGuard(symbol="XAUUSD.ecn", state_path="x.json",
                   default_when_stale="maybe")
