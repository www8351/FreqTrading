"""MT5 trade-server retcode policy (Part 2 §6 — exhaustive retcode handling).

Pure stdlib. Maps every documented ``order_send`` retcode to one
:class:`PolicyAction` so ``Mt5Broker._send_with_policy`` can decide: succeed,
retry (fresh price / backoff), recover an ambiguous double-fill, rotate the
filling mode, defer to the 10019 volume ladder, or abort (optionally with an
operator alert).

Default-OFF: the broker only consults this module when constructed with a
:class:`RetryPolicy`; ``retry=None`` keeps ``_send`` byte-identical.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "POLICY",
    "RETCODE_NAMES",
    "PolicyAction",
    "RetryPolicy",
    "classify",
    "delays",
    "retcode_name",
]


class PolicyAction(Enum):
    SUCCESS = "success"                      # order done, return result
    SUCCESS_PARTIAL = "success_partial"      # done but partial volume — warn
    RETRY_FRESH_PRICE = "retry_fresh_price"  # re-read tick, rebuild, resend
    RETRY_BACKOFF = "retry_backoff"          # transient — exponential backoff
    AMBIGUOUS = "ambiguous"                  # unknown outcome — re-query first
    DEFER_LADDER = "defer_ladder"            # 10019 — _open volume ladder owns
    ROTATE_FILLING = "rotate_filling"        # switch type_filling once
    ABORT = "abort"                          # permanent — raise, no retry
    ABORT_ALERT = "abort_alert"              # permanent + operator alert log


#: Human-readable names (MT5 TRADE_RETCODE_* constants, sans prefix).
RETCODE_NAMES: dict[int, str] = {
    10004: "REQUOTE",
    10006: "REJECT",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10011: "ERROR",
    10012: "TIMEOUT",
    10013: "INVALID",
    10014: "INVALID_VOLUME",
    10015: "INVALID_PRICE",
    10016: "INVALID_STOPS",
    10017: "TRADE_DISABLED",
    10018: "MARKET_CLOSED",
    10019: "NO_MONEY",
    10020: "PRICE_CHANGED",
    10021: "PRICE_OFF",
    10022: "INVALID_EXPIRATION",
    10024: "TOO_MANY_REQUESTS",
    10026: "SERVER_DISABLES_AT",
    10027: "CLIENT_DISABLES_AT",
    10028: "LOCKED",
    10029: "FROZEN",
    10030: "INVALID_FILL",
    10031: "CONNECTION",
    10033: "LIMIT_ORDERS",
    10034: "LIMIT_VOLUME",
}

#: Retcode -> action. Codes absent from this table classify as ABORT.
POLICY: dict[int, PolicyAction] = {
    10009: PolicyAction.SUCCESS,             # DONE
    10008: PolicyAction.SUCCESS,             # PLACED (pending accepted)
    10010: PolicyAction.SUCCESS_PARTIAL,     # partial fill — success + warn
    10004: PolicyAction.RETRY_FRESH_PRICE,   # requote
    10015: PolicyAction.RETRY_FRESH_PRICE,   # invalid price (stale tick)
    10020: PolicyAction.RETRY_FRESH_PRICE,   # price changed
    10006: PolicyAction.RETRY_BACKOFF,       # reject (transient dealer)
    10011: PolicyAction.RETRY_BACKOFF,       # common error
    10021: PolicyAction.RETRY_BACKOFF,       # no quotes to process
    10024: PolicyAction.RETRY_BACKOFF,       # too many requests
    10028: PolicyAction.RETRY_BACKOFF,       # locked for processing
    10012: PolicyAction.AMBIGUOUS,           # timeout — may have filled
    10031: PolicyAction.AMBIGUOUS,           # no connection — may have filled
    10019: PolicyAction.DEFER_LADDER,        # no money — volume ladder owns
    10030: PolicyAction.ROTATE_FILLING,      # unsupported filling mode
    10013: PolicyAction.ABORT,               # invalid request
    10014: PolicyAction.ABORT,               # invalid volume
    10017: PolicyAction.ABORT,               # trade disabled
    10018: PolicyAction.ABORT,               # market closed
    10022: PolicyAction.ABORT,               # invalid expiration
    10029: PolicyAction.ABORT,               # order/position frozen
    10033: PolicyAction.ABORT,               # pending-order limit reached
    10034: PolicyAction.ABORT,               # volume limit reached
    10016: PolicyAction.ABORT_ALERT,         # invalid stops — logic bug
    10026: PolicyAction.ABORT_ALERT,         # autotrading disabled by server
    10027: PolicyAction.ABORT_ALERT,         # autotrading disabled by client
}


def classify(retcode: int | None) -> PolicyAction:
    """Map an ``order_send`` outcome to a policy action.

    ``None`` (order_send itself returned None) is AMBIGUOUS — the request may
    have reached the server. Unknown codes are ABORT: never blind-retry a
    retcode we cannot reason about.
    """
    if retcode is None:
        return PolicyAction.AMBIGUOUS
    return POLICY.get(retcode, PolicyAction.ABORT)


def retcode_name(retcode: int | None) -> str:
    if retcode is None:
        return "NONE_RESULT"
    return RETCODE_NAMES.get(retcode, f"UNKNOWN_{retcode}")


@dataclass(frozen=True)
class RetryPolicy:
    """Retry budget + backoff shape. ``sleep_fn`` injectable for tests."""

    max_retries: int = 3        # retries after the initial attempt
    base_delay: float = 0.5     # seconds before the first backoff retry
    mult: float = 2.0           # exponential growth factor
    max_delay: float = 8.0      # backoff cap, seconds
    sleep_fn: Callable[[float], None] = time.sleep


def delays(policy: RetryPolicy) -> Iterator[float]:
    """Yield ``max_retries`` backoff delays: exponential, capped."""
    d = policy.base_delay
    for _ in range(policy.max_retries):
        yield min(d, policy.max_delay)
        d *= policy.mult
