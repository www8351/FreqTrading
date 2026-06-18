"""Async live wrapper around the synchronous OrbEngine.

The engine call is sync and CPU-trivial (O(1)/bar) so it runs inline on the event
loop with no executor offload. Backpressure is handled by the upstream source
(an ``asyncio.Queue`` or any async iterator of Candles).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .engine import OrbEngine
from .models import Candle, CandleError, OutOfOrderError, Signal

log = logging.getLogger("orb.stream")

# Sentinel pushed onto a Queue source to signal end-of-stream.
STOP = object()


class CandleStream:
    """Drives an :class:`OrbEngine` from an async candle source."""

    def __init__(self, engine: OrbEngine, on_signal=None, on_bar=None,
                 strict: bool = False) -> None:
        self._engine = engine
        self._on_signal = on_signal
        self._on_bar = on_bar  # called after every processed bar (sync hooks)
        self._strict = strict  # re-raise per-bar data errors instead of skipping

    async def run(self, source) -> list[Signal]:
        """Consume candles until the source is exhausted; return all signals."""
        signals: list[Signal] = []
        async for candle in _aiter(source):
            try:
                sig = self._engine.on_candle(candle)
            except (CandleError, OutOfOrderError) as e:
                log.error("bad candle ts=%s: %s", getattr(candle, "ts", "?"), e)
                if self._strict:
                    raise
                continue
            except asyncio.CancelledError:
                if self._engine.position is not None:
                    log.warning("cancelled with open position: %s",
                                self._engine.snapshot()["position"])
                raise
            if sig is not None:
                signals.append(sig)
                if self._on_signal is not None:
                    await _maybe_await(self._on_signal(sig))
            if self._on_bar is not None:
                await _maybe_await(self._on_bar(candle))
        return signals


async def _aiter(source) -> AsyncIterator[Candle]:
    """Normalize an asyncio.Queue or async iterator into an async iterator."""
    if isinstance(source, asyncio.Queue):
        while True:
            item = await source.get()
            if item is STOP:
                return
            yield item
    else:
        async for item in source:
            yield item


async def _maybe_await(result):
    if asyncio.iscoroutine(result):
        await result
