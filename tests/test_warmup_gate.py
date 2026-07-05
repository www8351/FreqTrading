"""--warmup-gate: broker actions suppressed while replaying feed history.

Reuses the end-to-end live harness from tests/test_cli_part2.py (scripted
2026-06-10 candles, real Mt5Broker over CliFakeMt5). "Now" is
``orb.cli._utcnow`` so tests pin the wall clock; the gate compares candle /
signal timestamps against process start:

* bar grace  = 3 min (M1 scale)
* sig grace  = trigger_tf + 2 min for smc (Signal.ts is the trigger-TF bar
  OPEN, so a live M30 signal can be ~31 min "old"), else 1 + 2 min

Default OFF: without the flag the live path is byte-identical (pinned by the
existing Part-2 spy test plus test_gate_off_no_suppression here).
"""

from datetime import datetime, timedelta, timezone

import orb.cli as cli_mod
from orb.cli import build_parser

from .test_cli_part2 import (
    ENTRY_CANDLES,
    CliFakeMt5,
    _entry_deals,
    _run_live,
    _spy_broker,
)


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_warmup_gate_flag_defaults_off():
    args = build_parser().parse_args(["live"])
    assert args.warmup_gate is False
    args = build_parser().parse_args(["live", "--warmup-gate"])
    assert args.warmup_gate is True


def test_stale_predicate_and_graces():
    start = _utc(2026, 6, 10, 12, 0)
    assert cli_mod._stale(_utc(2026, 6, 10, 11, 0), start, timedelta(minutes=3))
    assert not cli_mod._stale(_utc(2026, 6, 10, 11, 58), start,
                              timedelta(minutes=3))
    bar, sig = cli_mod._warmup_graces(is_smc=False, trigger_tf_min=0)
    assert bar == timedelta(minutes=3) and sig == timedelta(minutes=3)
    bar, sig = cli_mod._warmup_graces(is_smc=True, trigger_tf_min=30)
    assert bar == timedelta(minutes=3) and sig == timedelta(minutes=32)


def test_gate_suppresses_stale_signals(monkeypatch, capsys):
    # process "starts" hours after the scripted candles: everything is warmup.
    monkeypatch.setattr(cli_mod, "_utcnow", lambda: _utc(2026, 6, 10, 10, 0))
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--warmup-gate"])
    assert rc == 0
    assert _entry_deals(fake) == []          # nothing reached the broker
    assert "# WARMUP_SIG" in cap.err         # suppressed signal went to stderr
    assert "WARMUP_DONE" not in cap.err      # no fresh candle ever arrived
    assert cap.out.strip() == ""             # signals log stays tradable-only


def test_gate_flips_on_fresh_candle(monkeypatch, capsys):
    # start 00:05 -> bars 00:00/00:01 stale (< 00:02), 00:02+ fresh; the
    # 00:03 entry signal is inside the 3-min signal grace -> executes.
    monkeypatch.setattr(cli_mod, "_utcnow", lambda: _utc(2026, 6, 10, 0, 5))
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--warmup-gate"])
    assert rc == 0
    assert len(_entry_deals(fake)) == 1      # live entry fired after warmup
    assert cap.err.count("WARMUP_DONE") == 1
    assert "bars=2" in cap.err               # exactly the two stale bars


def test_gate_skips_broker_actions_during_warmup(monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "_utcnow", lambda: _utc(2026, 6, 10, 10, 0))
    fake = CliFakeMt5()
    calls: list = []
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--warmup-gate", "--max-daily-loss", "50"],
                           broker_cls=_spy_broker(fake, calls))
    assert rc == 0
    # per-bar / per-signal broker interaction never happens during warmup
    forbidden = {"execute", "close_all", "close_ticket", "modify_sl",
                 "update_stop", "has_position", "has_pending",
                 "cancel_pending", "cancel_expired"}
    assert forbidden.isdisjoint(calls), sorted(forbidden & set(calls))


def test_gate_off_no_suppression(monkeypatch, capsys):
    # No flag: same stale wall clock, yet the entry executes -> default off.
    monkeypatch.setattr(cli_mod, "_utcnow", lambda: _utc(2026, 6, 10, 10, 0))
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES)
    assert rc == 0
    assert len(_entry_deals(fake)) == 1
    assert "WARMUP" not in cap.err
