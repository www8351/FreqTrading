"""M5 tests: Stooq parse, semis momentum, thematic bias for US100/US500."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from macro import daemon, thematic
from macro.build import build_state
from macro.collectors import proxies
from macro.thematic import assess_semis, merge_thematic
from macro.state_writer import neutral_state, write_state
from orb.macroguard import MacroGuard
from orb.models import Direction, Signal, SignalKind, State

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)
SYMS = ("XAUUSD", "US100", "US500", "XAGUSD")


def stooq_csv(closes) -> bytes:
    rows = ["Date,Open,High,Low,Close,Volume"]
    for i, c in enumerate(closes, 1):
        rows.append(f"2026-06-{i:02d},{c},{c},{c},{c},1000")
    return "\n".join(rows).encode()


RISING = stooq_csv([100 + i for i in range(11)])      # 100..110 -> +10% over 10 bars


def mk_entry(direction):
    return Signal(ts=NOW, kind=SignalKind.ENTRY, direction=direction, price=2000.0,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason="b", qty=0.04)


# --- Stooq parse + momentum -------------------------------------------------
def test_parse_stooq():
    out = proxies.parse_stooq(RISING)
    assert len(out) == 11
    assert out[0] == (date(2026, 6, 1), 100.0)
    assert out[-1][1] == 110.0


def test_parse_stooq_error_body():
    assert proxies.parse_stooq(b"Exceeded the daily hits limit") == []


def test_momentum():
    assert proxies.momentum([100 + i for i in range(11)]) == 1.0    # +10% / scale 0.1
    assert proxies.momentum([100.0] * 11) == 0.0                    # flat
    assert proxies.momentum([100, 101]) is None                    # too short


def test_semis_momentum_with_fake_opener():
    mom = proxies.semis_momentum(opener=lambda url: RISING)
    assert set(mom) == set(proxies.SEMIS)
    assert all(v == 1.0 for v in mom.values())


# --- assess -----------------------------------------------------------------
def test_assess_semis():
    t = assess_semis({"nvda.us": 1.0, "avgo.us": 1.0, "tsm.us": 1.0})
    assert t.score == 1.0 and t.confidence == 0.6                  # conf capped
    assert assess_semis({}).score == 0.0


def test_assess_semis_weak_low_confidence():
    t = assess_semis({"nvda.us": 0.1})
    assert t.confidence == 0.1                                     # magnitude-scaled


# --- merge ------------------------------------------------------------------
def test_merge_thematic_tilts_indices_only():
    s = neutral_state(generated_at="2026-06-17T14:00:00Z")
    merge_thematic(s, thematic.ThemeResult(1.0, ("semis_mom=+1.00",), 0.6))
    assert s["assets"]["US100"]["bias"] == "bullish"
    assert s["assets"]["US100"]["score"] == 0.4                    # weight 0.4
    assert s["assets"]["US500"]["score"] == 0.2                    # weight 0.2 (lighter)
    assert s["assets"]["XAUUSD"]["score"] == 0.0                   # metals untouched
    assert s["global"]["confidence"] == 0.6


def test_merge_thematic_none_unchanged():
    s = neutral_state(generated_at="2026-06-17T14:00:00Z")
    assert merge_thematic(s, None)["assets"]["US100"]["score"] == 0.0


# --- build_state ------------------------------------------------------------
def test_build_state_thematic():
    t = assess_semis({"nvda.us": 1.0, "avgo.us": 1.0})
    s = build_state([], NOW, theme=t)
    assert s["assets"]["US100"]["bias"] == "bullish"


# --- daemon -----------------------------------------------------------------
def test_daemon_run_with_thematic_provider(tmp_path):
    out = tmp_path / "macro_state.json"
    provider = daemon._default_thematic_provider(symbols=SYMS,
                                                 opener=lambda url: RISING)
    daemon.run(out=str(out), opener=lambda url: b"[]", thematic_provider=provider,
               now_fn=lambda: NOW, write_interval=0.0, max_iters=1)
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["assets"]["US100"]["score"] > 0


# --- guard E2E --------------------------------------------------------------
def test_strong_semis_vetoes_short_us100(tmp_path):
    p = str(tmp_path / "macro_state.json")
    t = assess_semis({"nvda.us": 1.0, "avgo.us": 1.0, "tsm.us": 1.0, "amd.us": 1.0})
    write_state(build_state([], NOW, theme=t), p)
    g = MacroGuard("US100", p, now_fn=lambda: NOW, conf_min=0.6)
    assert g.evaluate_entry(mk_entry(Direction.SHORT)).action == "VETO"   # bullish tilt
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "ALLOW"   # aligned
