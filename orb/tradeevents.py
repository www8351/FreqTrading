"""Trade-event schema + sinks for the Part 2 copy-trade broadcast layer.

One :class:`TradeEvent` is built per broker action (open / open_pending /
modify_sl / partial_close / close / cancel_pending) and fanned out through an
:class:`EventHub` to sinks (JSONL trade log, HTTP broadcaster, risk guards).
``to_payload`` serializes an event into the shared schema-v1 JSON object
documented in ``docs/copytrade_schema.md`` — the same object is a trade-log
line, an HTTP body, a leader-store line, and a ZMQ frame, and Part 1's MQL5 EA
emits the identical shape via WebRequest.

Stdlib-only (D-002) and side-effect-free except for the sinks. Hard rule:
**event handling can never fail a trade** — the log sink swallows OSError and
the hub isolates every sink behind try/except; both only log a WARNING.

Testability: ``build_event`` takes injectable ``now_fn`` / ``seq_fn`` so tests
pin timestamps and sequence numbers.
"""

from __future__ import annotations

import itertools
import json
import logging
import socket
import uuid
from dataclasses import dataclass, fields
from datetime import datetime, timezone

log = logging.getLogger("orb.tradeevents")

SCHEMA_VERSION = 1

#: Documented action vocabulary (docs/copytrade_schema.md). Unknown actions are
#: still emitted (additive-only rule under schema v1) but logged as a WARNING.
ACTIONS = ("open", "open_pending", "modify_sl", "partial_close", "close",
           "cancel_pending")

# MT5 ORDER_TYPE_* constants: even = buy side, odd = sell side
# (BUY=0, SELL=1, BUY_LIMIT=2, SELL_LIMIT=3, BUY_STOP=4, SELL_STOP=5,
#  BUY_STOP_LIMIT=6, SELL_STOP_LIMIT=7).
_ORDER_TYPE_MAX = 7

# Non-dot broker suffixes (JustMarkets "XAUUSDm" cent/mini forms); dotted
# suffixes (.ecn/.pro/...) are handled by the partition itself — same simple
# convention as orb.macroguard.bare_key.
_BARE_SUFFIXES = ("m", "c")

PRICE_DP = 8  # slippage rounding, matches orb.broker.mt5.PRICE_DP


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Schema-v1 trade event. Field set mirrors docs/copytrade_schema.md
    (``schema_version`` and the ``source`` nesting are added by
    :func:`to_payload` at serialization time)."""

    event_id: str
    seq: int
    ts: str                       # ISO-8601 UTC
    node: str
    account: int | None
    strategy: str
    magic: int | None
    symbol: str
    base_symbol: str
    action: str
    ticket: int | None = None
    order: int | None = None
    deal: int | None = None
    direction: str | None = None  # "long" | "short" | None
    volume: float | None = None
    price_requested: float | None = None
    price_filled: float | None = None
    slippage: float | None = None
    sl: float | None = None
    tp: float | None = None
    reason: str | None = None
    rr_planned: float | None = None
    rr_achieved: float | None = None
    risk_inflation_r: float | None = None
    pnl: float | None = None
    retcode: int | None = None


_FIELD_NAMES = frozenset(f.name for f in fields(TradeEvent))

_seq_counter = itertools.count(1)  # module-level monotonic event sequence


def base_symbol_of(symbol: str) -> str:
    """Broker symbol -> bare base symbol: ``XAUUSD.ecn`` / ``XAUUSDm`` ->
    ``XAUUSD``. Partition on ``.`` (macroguard convention), then strip a known
    lowercase non-dot suffix."""
    head = str(symbol).partition(".")[0].strip()
    for suf in _BARE_SUFFIXES:
        if len(head) > len(suf) and head.endswith(suf):
            head = head[: -len(suf)]
            break
    return head.upper()


def _direction_of(order_type) -> str | None:
    if isinstance(order_type, int) and 0 <= order_type <= _ORDER_TYPE_MAX:
        return "long" if order_type % 2 == 0 else "short"
    return None


def _as_float(v) -> float | None:
    return None if v is None else float(v)


def _as_int(v) -> int | None:
    return None if v is None else int(v)


def build_event(
    action: str,
    *,
    symbol: str,
    magic: int | None,
    request: dict | None = None,
    result: dict | None = None,
    reason: str | None = None,
    strategy: str = "orb",
    account: int | None = None,
    extra: dict | None = None,
    now_fn=None,
    seq_fn=None,
) -> TradeEvent:
    """Build a TradeEvent from an MT5-shaped ``request`` dict (volume/sl/tp/
    price/type/position) and ``result`` dict (retcode/order/deal/price/volume).

    ``extra`` overrides or fills any event field after the base mapping
    (unknown keys are ignored with a WARNING). ``now_fn``/``seq_fn`` are
    injectable for tests; defaults are UTC wall clock and a module-level
    monotonic counter.
    """
    if action not in ACTIONS:
        log.warning("tradeevent_unknown_action action=%s symbol=%s", action, symbol)
    req = request or {}
    res = result or {}

    now = now_fn() if now_fn is not None else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    price_requested = _as_float(req.get("price"))
    price_filled = _as_float(res.get("price"))
    slippage = None
    if price_requested is not None and price_filled is not None:
        slippage = round(price_filled - price_requested, PRICE_DP)

    order = _as_int(res.get("order"))
    position = req.get("position")  # SLTP/close requests target a position
    volume = res.get("volume")      # prefer actual filled volume
    if volume is None:
        volume = req.get("volume")

    values = {
        "event_id": uuid.uuid4().hex,
        "seq": seq_fn() if seq_fn is not None else next(_seq_counter),
        "ts": now.astimezone(timezone.utc).isoformat(),
        "node": socket.gethostname(),
        "account": account,
        "strategy": strategy,
        "magic": magic,
        "symbol": symbol,
        "base_symbol": base_symbol_of(symbol),
        "action": action,
        "ticket": _as_int(position) if position is not None else order,
        "order": order,
        "deal": _as_int(res.get("deal")),
        "direction": _direction_of(req.get("type")),
        "volume": _as_float(volume),
        "price_requested": price_requested,
        "price_filled": price_filled,
        "slippage": slippage,
        "sl": _as_float(req.get("sl")),
        "tp": _as_float(req.get("tp")),
        "reason": reason,
        "rr_planned": None,
        "rr_achieved": None,
        "risk_inflation_r": None,
        "pnl": None,
        "retcode": _as_int(res.get("retcode")),
    }
    if extra:
        for key, val in extra.items():
            if key in _FIELD_NAMES:
                values[key] = val
            else:
                log.warning("tradeevent_extra_ignored key=%s action=%s", key, action)
    return TradeEvent(**values)


def to_payload(ev: TradeEvent) -> dict:
    """Serialize to the schema-v1 JSON object: adds ``schema_version`` and
    nests ``source`` = {node, account, strategy, magic}. Key set must match
    docs/copytrade_schema.md exactly."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": ev.event_id,
        "seq": ev.seq,
        "ts": ev.ts,
        "source": {"node": ev.node, "account": ev.account,
                   "strategy": ev.strategy, "magic": ev.magic},
        "symbol": ev.symbol,
        "base_symbol": ev.base_symbol,
        "action": ev.action,
        "ticket": ev.ticket,
        "order": ev.order,
        "deal": ev.deal,
        "direction": ev.direction,
        "volume": ev.volume,
        "price_requested": ev.price_requested,
        "price_filled": ev.price_filled,
        "slippage": ev.slippage,
        "sl": ev.sl,
        "tp": ev.tp,
        "reason": ev.reason,
        "rr_planned": ev.rr_planned,
        "rr_achieved": ev.rr_achieved,
        "risk_inflation_r": ev.risk_inflation_r,
        "pnl": ev.pnl,
        "retcode": ev.retcode,
    }


class TradeEventLog:
    """Append-only JSONL sink: one schema-v1 object per line.

    Write failures (disk full, locked file, bad path) are swallowed after a
    WARNING — a broken trade log must never fail a trade.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def write(self, ev: TradeEvent) -> None:
        try:
            line = json.dumps(to_payload(ev), default=str)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("tradeevent_log_write_failed path=%s err=%s", self.path, e)


class EventHub:
    """Fan-out of one event to N sinks (callables taking a TradeEvent).

    Every sink runs behind its own try/except: one failing sink can neither
    fail the trade nor starve the remaining sinks.
    """

    def __init__(self) -> None:
        self._sinks: list = []

    def add(self, sink) -> None:
        self._sinks.append(sink)

    def emit(self, ev: TradeEvent) -> None:
        for sink in self._sinks:
            try:
                sink(ev)
            except Exception as e:  # noqa: BLE001 — isolation is the contract
                name = getattr(sink, "__name__", type(sink).__name__)
                log.warning("tradeevent_sink_failed sink=%s action=%s err=%s",
                            name, ev.action, e)
