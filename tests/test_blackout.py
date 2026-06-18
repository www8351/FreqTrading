"""M1 macro tests: calendar parse, blackout windows, state build, daemon.

All offline: parse a saved ForexFactory sample, inject a fake opener / fixed clock.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

from macro import daemon
from macro.blackout import active_blackout, upcoming_events
from macro.build import build_state
from macro.collectors import forexfactory
from macro.normalizer import classify_kind, normalize_impact, parse_ts

UTC = timezone.utc
FIX = pathlib.Path(__file__).parent / "fixtures" / "ff_calendar_sample.json"


def dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def load_events():
    data = json.loads(FIX.read_text(encoding="utf-8"))
    return forexfactory.parse_calendar(data)


# --- normalizer / parser ----------------------------------------------------
def test_classify_kind():
    assert classify_kind("CPI m/m") == "CPI"
    assert classify_kind("FOMC Statement") == "FOMC"
    assert classify_kind("Federal Funds Rate") == "FOMC"
    assert classify_kind("Non-Farm Employment Change") == "NFP"
    assert classify_kind("Flash Manufacturing PMI") == "OTHER"


def test_normalize_impact():
    assert normalize_impact("High") == "high"
    assert normalize_impact("MEDIUM") == "medium"
    assert normalize_impact("") == "unknown"


def test_parse_ts_to_utc():
    assert parse_ts("2026-06-17T08:30:00-04:00") == dt(2026, 6, 17, 12, 30)
    assert parse_ts("2026-06-17T12:30:00Z") == dt(2026, 6, 17, 12, 30)


def test_parse_calendar_tolerant_and_sorted():
    ev = load_events()
    assert len(ev) == 6                       # 7 rows - 1 junk (no date)
    assert [e.ts for e in ev] == sorted(e.ts for e in ev)
    cpi = next(e for e in ev if e.kind == "CPI")
    assert cpi.ts == dt(2026, 6, 17, 12, 30)
    assert cpi.impact == "high"
    fomc_stmt = next(e for e in ev if e.title == "FOMC Statement")
    assert fomc_stmt.forecast is None         # "" -> None


def test_parse_calendar_rejects_non_list():
    assert forexfactory.parse_calendar({"not": "a list"}) == []


# --- blackout windows -------------------------------------------------------
def test_blackout_active_at_event():
    ev = load_events()
    bo = active_blackout(ev, dt(2026, 6, 17, 12, 30))
    assert bo and bo["active"] is True
    assert "CPI" in bo["reason"]
    assert bo["until"] == "2026-06-17T13:00:00Z"


def test_blackout_window_edges():
    ev = load_events()
    assert active_blackout(ev, dt(2026, 6, 17, 12, 0)) is not None   # ts-30 start
    assert active_blackout(ev, dt(2026, 6, 17, 11, 59)) is None      # just before
    assert active_blackout(ev, dt(2026, 6, 17, 13, 0)) is not None   # ts+30 end
    assert active_blackout(ev, dt(2026, 6, 17, 13, 1)) is None       # just after


def test_blackout_clustered_fomc():
    # FOMC Statement + Federal Funds Rate both 18:00Z -> one window, reason FOMC
    ev = load_events()
    bo = active_blackout(ev, dt(2026, 6, 17, 18, 10))
    assert bo and bo["reason"] == "FOMC"      # de-duped
    assert bo["until"] == "2026-06-17T18:30:00Z"


def test_blackout_ignores_medium_by_default():
    ev = load_events()
    # Flash PMI is medium at 13:45Z
    assert active_blackout(ev, dt(2026, 6, 18, 13, 45)) is None
    inc = active_blackout(ev, dt(2026, 6, 18, 13, 45), impacts=("high", "medium"))
    assert inc is not None


# --- upcoming events --------------------------------------------------------
def test_upcoming_events_window():
    ev = load_events()
    up = upcoming_events(ev, dt(2026, 6, 17, 0, 0), horizon_h=48)
    kinds = sorted(e["kind"] for e in up)
    assert kinds == ["CPI", "FOMC", "FOMC", "OTHER"]   # NFP past, Holiday dropped
    assert all(e["blackout_pre_min"] == 30 for e in up)


# --- build_state ------------------------------------------------------------
def test_build_state_blackout_on():
    ev = load_events()
    s = build_state(ev, dt(2026, 6, 17, 12, 30), ttl_sec=300)
    assert s["global"]["blackout"]["active"] is True
    assert s["generated_at"] == "2026-06-17T12:30:00Z"
    assert s["ttl_sec"] == 300
    assert set(s["assets"]) == {"XAUUSD", "US100", "US500", "XAGUSD"}
    assert len(s["events"]) >= 1


def test_build_state_blackout_off():
    ev = load_events()
    s = build_state(ev, dt(2026, 6, 17, 15, 0))
    assert s["global"]["blackout"]["active"] is False


# --- daemon integration (injected opener + clock, no network/sleep) ---------
def _fake_opener(_url):
    return FIX.read_bytes()


def test_fetch_with_fake_opener():
    ev = forexfactory.fetch("ignored", opener=_fake_opener)
    assert len(ev) == 6


def test_daemon_run_once_writes_blackout(tmp_path):
    out = tmp_path / "macro_state.json"
    state = daemon.run_once(load_events(), str(out), dt(2026, 6, 17, 12, 30))
    assert state["global"]["blackout"]["active"] is True
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["global"]["blackout"]["reason"].startswith("CPI")


def test_daemon_run_loop_one_iter(tmp_path):
    out = tmp_path / "macro_state.json"
    daemon.run(out=str(out), opener=_fake_opener,
               now_fn=lambda: dt(2026, 6, 17, 18, 0),
               write_interval=0.0, max_iters=1)
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["global"]["blackout"]["active"] is True
    assert on_disk["global"]["blackout"]["reason"] == "FOMC"
