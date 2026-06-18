"""M2 tests: surprise scorer, sensitivity, FRED collector, filter-mode E2E."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from macro import scorer
from macro.build import build_state
from macro.collectors import forexfactory, fred
from macro.normalizer import RawEvent, parse_value
from macro.state_writer import write_state
from orb.macroguard import MacroGuard
from orb.models import Direction, Signal, SignalKind, State

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)
SYMS = ("XAUUSD", "US100", "US500", "XAGUSD")


def ev(kind, age_h, *, impact="high", forecast=None, actual=None) -> RawEvent:
    return RawEvent(source="t", ts=NOW - timedelta(hours=age_h), title=kind,
                    currency="USD", impact=impact, kind=kind,
                    forecast=forecast, actual=actual)


def mk_entry(direction, qty=0.04) -> Signal:
    return Signal(ts=NOW, kind=SignalKind.ENTRY, direction=direction, price=2000.0,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason="b", qty=qty)


# --- value parsing ----------------------------------------------------------
def test_parse_value():
    assert parse_value("190K") == 190000.0
    assert parse_value("0.3%") == 0.3
    assert parse_value("<5.50%") == 5.5
    assert parse_value("-0.1%") == -0.1
    assert parse_value("1,250") == 1250.0
    assert parse_value("") is None
    assert parse_value(None) is None
    assert parse_value("n/a") is None


# --- surprise ---------------------------------------------------------------
def test_surprise_sign_and_clamp():
    assert scorer.surprise(ev("CPI", 1, forecast="0.2%", actual="0.5%")) == 1.0  # clamped
    assert scorer.surprise(ev("NFP", 1, forecast="200K", actual="100K")) < 0
    assert scorer.surprise(ev("CPI", 1, forecast="0.2%")) is None               # no actual


# --- scorer per-asset bias --------------------------------------------------
def test_cpi_hot_bearish_metals_and_equities():
    out = scorer.score([ev("CPI", 2, forecast="0.2%", actual="0.5%")], NOW, SYMS)
    a = out["assets"]
    assert a["XAUUSD"]["bias"] == "bearish" and a["XAUUSD"]["score"] < 0
    assert a["US100"]["bias"] == "bearish"
    assert out["global"]["confidence"] >= 0.6
    assert "CPI:hot" in a["XAUUSD"]["drivers"]


def test_nfp_strong_risk_on():
    out = scorer.score([ev("NFP", 2, forecast="180K", actual="320K")], NOW, SYMS)
    a = out["assets"]
    assert a["US100"]["bias"] == "bullish" and a["US100"]["score"] > 0
    assert a["XAUUSD"]["score"] < 0                      # strong jobs -> gold down
    assert out["global"]["risk_regime"] == "risk_on"


def test_no_recent_events_neutral():
    out = scorer.score([], NOW, SYMS)
    assert out["global"]["confidence"] == 0.0
    assert all(v["bias"] == "neutral" for v in out["assets"].values())


def test_stale_event_ignored():
    out = scorer.score([ev("CPI", 50, forecast="0.2%", actual="0.9%")], NOW, SYMS)
    assert out["global"]["confidence"] == 0.0           # 50h > 36h lookback


def test_confidence_decays_with_age():
    c1 = scorer.score([ev("CPI", 1, forecast="0.2%", actual="0.5%")],
                      NOW, SYMS)["global"]["confidence"]
    c30 = scorer.score([ev("CPI", 30, forecast="0.2%", actual="0.5%")],
                       NOW, SYMS)["global"]["confidence"]
    assert c1 > c30 > 0


def test_medium_impact_weighted_lower_than_high():
    hi = scorer.score([ev("CPI", 2, impact="high", forecast="0.2%", actual="0.5%")],
                      NOW, SYMS)["assets"]["XAUUSD"]["score"]
    med = scorer.score([ev("CPI", 2, impact="medium", forecast="0.2%", actual="0.5%")],
                       NOW, SYMS)["assets"]["XAUUSD"]["score"]
    assert abs(med) < abs(hi)


# --- build_state integration ------------------------------------------------
def test_build_state_fills_bias_no_blackout():
    s = build_state([ev("CPI", 2, forecast="0.2%", actual="0.5%")], NOW, SYMS)
    assert s["global"]["blackout"]["active"] is False    # 2h ago -> outside window
    assert s["assets"]["XAUUSD"]["bias"] == "bearish"
    assert s["global"]["confidence"] >= 0.6


# --- ForexFactory now captures `actual` -------------------------------------
def test_forexfactory_captures_actual():
    rows = [{"title": "CPI m/m", "country": "USD",
             "date": "2026-06-17T12:00:00Z", "impact": "High",
             "forecast": "0.2%", "actual": "0.5%", "previous": "0.2%"}]
    e = forexfactory.parse_calendar(rows)[0]
    assert e.actual == "0.5%" and e.forecast == "0.2%"


# --- FRED collector ---------------------------------------------------------
def test_fred_parse_observations():
    data = {"observations": [
        {"date": "2026-06-01", "value": "315.0"},
        {"date": "2026-05-01", "value": "314.5"},
        {"date": "2026-04-01", "value": "."},        # missing -> skipped
    ]}
    obs = fred.parse_observations(data)
    assert obs == [(__import__("datetime").date(2026, 5, 1), 314.5),
                   (__import__("datetime").date(2026, 6, 1), 315.0)]


def test_fred_latest_with_fake_opener():
    payload = b'{"observations":[{"date":"2026-06-01","value":"315.0"},' \
              b'{"date":"2026-05-01","value":"314.5"}]}'
    got = fred.latest("CPIAUCSL", api_key="x", opener=lambda url: payload)
    assert got[1] == 315.0


# --- filter-mode E2E: bias conflict vetoes, alignment allows ----------------
def test_filter_mode_bias_conflict(tmp_path):
    p = str(tmp_path / "macro_state.json")
    write_state(build_state([ev("CPI", 2, forecast="0.2%", actual="0.6%")], NOW, SYMS), p)
    g = MacroGuard("XAUUSD.ecn", p, now_fn=lambda: NOW, conf_min=0.6)
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "VETO"   # gold bearish
    assert "macro_bias_conflict" in g.evaluate_entry(mk_entry(Direction.LONG)).reason
    assert g.evaluate_entry(mk_entry(Direction.SHORT)).action == "ALLOW"  # aligned
