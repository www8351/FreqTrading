"""Background broker-state cache: keep blocking reads off the candle hot path.

The live loop's per-bar sync (:func:`orb.cli.cmd_live`'s ``on_bar``) needs the
account balance and the open positions every bar. Both are blocking MetaTrader5
IPC round-trips. Calling them inline stalls the same asyncio event loop that
drives the candle feed, so a volatile bar can be delayed by the IPC latency of
an otherwise-unrelated read.

:class:`BrokerStateCache` runs ONE background task that refreshes a snapshot on a
fixed interval, executing the blocking reads in a worker thread
(``loop.run_in_executor``) so they never block the loop. ``on_bar`` then reads
the cached snapshot — a cheap, lock-free attribute load. A read falls back to a
direct broker call only while the cache is still cold (before the first refresh),
so behaviour is identical on the first bar and merely fresher thereafter.

Writes (``order_send`` / ``modify_sl`` / ``close``) are deliberately NOT cached
or backgrounded — they stay synchronous and serialized on the caller, so order
mutation is never racy.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any, Callable

log = logging.getLogger("orb.brokerstate")


class BrokerStateCache:
    """Caches ``broker.balance()`` / ``broker.my_positions()`` snapshots.

    Parameters
    ----------
    broker:
        Any object exposing ``balance() -> float`` and ``my_positions() -> list``
        (e.g. :class:`orb.broker.mt5.Mt5Broker`).
    interval:
        Seconds between background refreshes (default 0.3s — fast enough that a
        1-minute-bar loop always sees a near-current snapshot).
    now_fn:
        Injectable monotonic clock (seconds) for tests; defaults to
        ``time.monotonic``.
    """

    def __init__(
        self,
        broker,
        interval: float = 0.3,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._broker = broker
        self._interval = interval
        self._now = now_fn or _time.monotonic
        self._balance: float | None = None
        self._positions: Any = None
        self._updated_at: float | None = None
        self._task: asyncio.Task | None = None
        self._stop = False

    # ------------------------------------------------------------------ #
    @property
    def age(self) -> float | None:
        """Seconds since the last successful refresh, or None if never."""
        if self._updated_at is None:
            return None
        return self._now() - self._updated_at

    def _is_fresh(self, max_age: float | None) -> bool:
        if self._updated_at is None:
            return False
        if max_age is None:
            return True
        return (self._now() - self._updated_at) <= max_age

    # ------------------------------------------------------------------ #
    def balance(self, max_age: float | None = None) -> float:
        """Last cached balance; direct read when the cache is cold/stale."""
        if self._balance is not None and self._is_fresh(max_age):
            return self._balance
        bal = self._broker.balance()
        self._balance = bal
        return bal

    def positions(self, max_age: float | None = None):
        """Last cached positions; direct read when the cache is cold/stale."""
        if self._positions is not None and self._is_fresh(max_age):
            return self._positions
        pos = self._broker.my_positions()
        self._positions = pos
        return pos

    # ------------------------------------------------------------------ #
    async def _refresh_once(self) -> None:
        """Pull a fresh snapshot, running the blocking reads off the loop."""
        loop = asyncio.get_event_loop()
        bal = await loop.run_in_executor(None, self._broker.balance)
        pos = await loop.run_in_executor(None, self._broker.my_positions)
        self._balance = bal
        self._positions = pos
        self._updated_at = self._now()

    async def _run(self) -> None:
        while not self._stop:
            try:
                await self._refresh_once()
            except Exception as e:  # noqa: BLE001 — keep looping; reads fall back
                log.warning("broker_state_refresh_failed: %s", e)
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        """Launch the background refresher (idempotent). Call inside the loop."""
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def aclose(self) -> None:
        """Stop and await the background refresher."""
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
