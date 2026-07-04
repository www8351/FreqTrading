"""TradeEvent schema, JSONL log sink, and EventHub fan-out (orb/tradeevents.py).

Contract under test = docs/copytrade_schema.md payload (plan Part 2, schema v1):
one JSON object per event, ``schema_version`` added at serialization time,
``source`` nested. Event emission must never be able to fail a trade, so the
log sink swallows OSError and the hub isolates sink exceptions.
"""

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from orb.tradeevents import (
    SCHEMA_VERSION,
    EventHub,
    TradeEvent,
    TradeEventLog,
    build_event,
    to_payload,
)

TS = datetime(2026, 7, 3, 13, 14, 15, 123456, tzinfo=timezone.utc)

# MT5-shaped request/result dicts as Mt5Broker builds/receives them.
REQUEST = {
    "action": 1,                 # TRADE_ACTION_DEAL
    "symbol": "XAUUSD.ecn",
    "volume": 0.04,
    "type": 1,                   # ORDER_TYPE_SELL
    "price": 4182.00,
    "sl": 4187.90,
    "tp": 0.0,
    "magic": 20260610,
}
RESULT = {"retcode": 10009, "order": 111, "deal": 222, "price": 4182.05, "volume": 0.04}

# Documented schema v1 key sets (docs/copytrade_schema.md) — exact match required.
TOP_KEYS = {
    "schema_version", "event_id", "seq", "ts", "source",
    "symbol", "base_symbol", "action",
    "ticket", "order", "deal", "direction", "volume",
    "price_requested", "price_filled", "slippage",
    "sl", "tp", "reason",
    "rr_planned", "rr_achieved", "risk_inflation_r",
    "pnl", "retcode",
}
SOURCE_KEYS = {"node", "account", "strategy", "magic"}


def ev_open(**kw):
    kw.setdefault("symbol", "XAUUSD.ecn")
    kw.setdefault("magic", 20260610)
    kw.setdefault("request", dict(REQUEST))
    kw.setdefault("result", dict(RESULT))
    kw.setdefault("reason", "breakout_short")
    kw.setdefault("account", 2001894982)
    kw.setdefault("now_fn", lambda: TS)
    return build_event("open", **kw)


# --------------------------------------------------------------------------- #
# build_event mapping
# --------------------------------------------------------------------------- #
def test_build_maps_request_and_result_for_open():
    ev = ev_open()
    assert ev.action == "open"
    assert ev.symbol == "XAUUSD.ecn"
    assert ev.base_symbol == "XAUUSD"
    assert ev.magic == 20260610
    assert ev.account == 2001894982
    assert ev.strategy == "orb"
    assert ev.direction == "short"          # ORDER_TYPE_SELL
    assert ev.volume == 0.04
    assert ev.price_requested == 4182.00
    assert ev.price_filled == 4182.05
    assert ev.slippage == pytest.approx(0.05)
    assert ev.sl == 4187.90
    assert ev.tp == 0.0
    assert ev.reason == "breakout_short"
    assert ev.retcode == 10009
    assert ev.order == 111
    assert ev.deal == 222
    assert ev.ticket == 111                 # market open: ticket = result order
    assert ev.ts == "2026-07-03T13:14:15.123456+00:00"
    assert isinstance(ev.event_id, str) and len(ev.event_id) == 32
    assert ev.pnl is None
    assert ev.rr_planned is None


def test_partial_close_reason_passthrough():
    ev = build_event(
        "partial_close",
        symbol="XAUUSD.ecn",
        magic=20260610,
        request={"volume": 0.02, "type": 0, "price": 4180.0, "position": 333},
        result={"retcode": 10009, "order": 444, "deal": 555, "price": 4180.1,
                "volume": 0.02},
        reason="tp1_partial",
        now_fn=lambda: TS,
    )
    assert ev.action == "partial_close"
    assert ev.reason == "tp1_partial"
    assert ev.ticket == 333                 # request position wins over result order
    assert ev.order == 444
    assert ev.volume == 0.02


def test_build_without_request_or_result_yields_nulls():
    ev = build_event("cancel_pending", symbol="US100.pro", magic=1,
                     now_fn=lambda: TS)
    assert ev.base_symbol == "US100"
    assert ev.direction is None
    assert ev.volume is None
    assert ev.price_requested is None
    assert ev.price_filled is None
    assert ev.slippage is None
    assert ev.retcode is None
    assert ev.ticket is None


def test_direction_derived_long_and_via_extra():
    ev = ev_open(request={**REQUEST, "type": 0})       # ORDER_TYPE_BUY
    assert ev.direction == "long"
    ev2 = ev_open(request={"sl": 1.0}, extra={"direction": "short"})
    assert ev2.direction == "short"


def test_extra_overrides_and_fills_fields():
    ev = ev_open(extra={"pnl": -3.21, "rr_planned": 5.0, "rr_achieved": 4.93,
                        "risk_inflation_r": 0.009, "node": "test-node",
                        "ticket": 999})
    assert ev.pnl == -3.21
    assert ev.rr_planned == 5.0
    assert ev.rr_achieved == 4.93
    assert ev.risk_inflation_r == 0.009
    assert ev.node == "test-node"
    assert ev.ticket == 999


def test_base_symbol_strips_known_suffixes():
    assert ev_open(symbol="XAUUSD.ecn").base_symbol == "XAUUSD"
    assert ev_open(symbol="XAUUSDm").base_symbol == "XAUUSD"
    assert ev_open(symbol="XAUUSD.pro").base_symbol == "XAUUSD"
    assert ev_open(symbol="XAUUSD").base_symbol == "XAUUSD"
    assert ev_open(symbol="US100").base_symbol == "US100"


def test_event_is_frozen():
    ev = ev_open()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.symbol = "EURUSD"


# --------------------------------------------------------------------------- #
# seq
# --------------------------------------------------------------------------- #
def test_seq_monotonic_across_build_event_calls():
    a, b, c = ev_open(), ev_open(), ev_open()
    assert a.seq < b.seq < c.seq


def test_seq_fn_injectable():
    ev = ev_open(seq_fn=lambda: 17)
    assert ev.seq == 17


# --------------------------------------------------------------------------- #
# to_payload — exact documented key set
# --------------------------------------------------------------------------- #
def test_payload_key_set_matches_documented_schema_exactly():
    payload = to_payload(ev_open())
    assert set(payload.keys()) == TOP_KEYS
    assert set(payload["source"].keys()) == SOURCE_KEYS


def test_payload_values_and_source_nesting():
    payload = to_payload(ev_open())
    assert payload["schema_version"] == SCHEMA_VERSION == 1
    assert payload["source"] == {"node": payload["source"]["node"],
                                 "account": 2001894982,
                                 "strategy": "orb",
                                 "magic": 20260610}
    assert payload["symbol"] == "XAUUSD.ecn"
    assert payload["base_symbol"] == "XAUUSD"
    assert payload["ts"] == "2026-07-03T13:14:15.123456+00:00"
    json.dumps(payload)  # json-safe


# --------------------------------------------------------------------------- #
# TradeEventLog
# --------------------------------------------------------------------------- #
def test_log_appends_one_parseable_json_line_per_event(tmp_path):
    path = tmp_path / "trade_events.jsonl"
    sink = TradeEventLog(str(path))
    sink.write(ev_open())
    sink.write(ev_open(reason="tp1_partial"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert set(rows[0].keys()) == TOP_KEYS
    assert rows[0]["reason"] == "breakout_short"
    assert rows[1]["reason"] == "tp1_partial"


def test_log_write_failure_does_not_raise(tmp_path, caplog):
    sink = TradeEventLog(str(tmp_path))  # a directory: open(...,"a") -> OSError
    with caplog.at_level("WARNING", logger="orb.tradeevents"):
        sink.write(ev_open())            # must not raise
    assert any("tradeevent_log_write_failed" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# EventHub
# --------------------------------------------------------------------------- #
def test_hub_sink_exception_isolated_second_sink_still_called(caplog):
    seen = []

    def bad_sink(ev):
        raise RuntimeError("boom")

    hub = EventHub()
    hub.add(bad_sink)
    hub.add(seen.append)
    ev = ev_open()
    with caplog.at_level("WARNING", logger="orb.tradeevents"):
        hub.emit(ev)                     # must not raise
    assert seen == [ev]
    assert any("tradeevent_sink_failed" in r.message for r in caplog.records)


def test_hub_with_no_sinks_is_noop():
    EventHub().emit(ev_open())           # must not raise
