"""MT5-native candle feed: closed M1 bars straight from the local terminal.

Zero feed lag vs external REST providers — the same terminal that executes
orders supplies the candles. Polls copy_rates_from_pos every poll_sec and
yields each newly CLOSED bar exactly once (rates[-1] is the forming bar and
is never emitted).

Usage:
    python -m orb live --source orb.feeds.mt5feed:xauusd_live --broker mt5 ...
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..models import Candle, OrbError

log = logging.getLogger("orb.feeds.mt5")

TIMEFRAME_M1 = 1  # mt5.TIMEFRAME_M1
RECONNECT_AFTER = 3  # consecutive no-rate polls before re-initializing the IPC link


class Mt5FeedError(OrbError):
    pass


def _reconnect(mt5, symbol: str) -> bool:
    """Tear down + re-establish the terminal IPC link (terminal was restarted).

    A terminal restart silently invalidates the python<->terminal pipe, after
    which copy_rates_from_pos fails forever with (-10001, 'IPC send failed').
    shutdown()+initialize() re-attaches to the now-running terminal."""
    try:
        mt5.shutdown()
    except Exception:  # noqa: BLE001 — link may already be dead
        pass
    try:
        return bool(mt5.initialize() and mt5.symbol_select(symbol, True))
    except Exception:  # noqa: BLE001
        return False


async def stream_candles(
    symbol: str = "XAUUSD.ecn",
    poll_sec: float = 2.0,
    tz_offset_sec: int | str = "auto",
    mt5=None,
    now_fn=None,
):
    """Async generator of closed M1 Candles from the local MT5 terminal.

    MT5 bar times are BROKER SERVER time, not UTC. ``tz_offset_sec="auto"``
    measures the offset on the first FRESH batch (forming bar vs real UTC now,
    rounded to the hour) and subtracts it so emitted Candles are true UTC.
    When the market is closed (weekend/holiday) the latest bar is stale, so
    offset-locking is deferred until a genuinely-forming bar appears — otherwise
    every candle would carry a weekend-sized timestamp error. ``now_fn`` is an
    injectable UTC-epoch clock for tests.
    """
    if mt5 is None:
        import MetaTrader5 as mt5  # noqa: N816
    if now_fn is None:
        def now_fn():
            return datetime.now(timezone.utc).timestamp()
    if not mt5.initialize():
        raise Mt5FeedError(f"mt5.initialize failed: {mt5.last_error()}")
    if not mt5.symbol_select(symbol, True):
        raise Mt5FeedError(f"symbol_select({symbol}) failed: {mt5.last_error()}")

    offset: int | None = None if tz_offset_sec == "auto" else int(tz_offset_sec)
    last_emitted: int | None = None  # epoch of last yielded bar open
    fail_streak = 0
    while True:
        rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME_M1, 0, 3)
        if rates is None or len(rates) < 2:
            fail_streak += 1
            log.warning("no_rates %s %s (streak=%d)", symbol, mt5.last_error(),
                        fail_streak)
            if fail_streak >= RECONNECT_AFTER:
                if _reconnect(mt5, symbol):
                    log.warning("mt5_reconnect ok %s", symbol)
                    fail_streak = 0
                else:
                    log.warning("mt5_reconnect failed %s %s", symbol,
                                mt5.last_error())
                    await asyncio.sleep(poll_sec * 5)  # back off while terminal down
            await asyncio.sleep(poll_sec)
            continue
        fail_streak = 0
        if offset is None:
            # A LIVE forming bar opened <=~1 min ago in UTC, so (forming - now)
            # equals the broker TZ offset: a small whole number of hours
            # (|offset| <= ~14h for any real broker). A STALE bar (market closed
            # / weekend) sits many hours further off -> its gap to now far
            # exceeds any real offset. Defer locking until a fresh bar appears,
            # else offset would lock to a weekend-sized error for the whole
            # session (corrupting every candle's UTC timestamp).
            forming = int(rates[-1]["time"])
            now = now_fn()
            if abs(forming - now) > 15 * 3600:
                log.info("market_closed_or_stale forming_age=%.0fs await_fresh_bar",
                         now - forming)
                await asyncio.sleep(poll_sec)
                continue
            offset = round((forming - now) / 3600) * 3600
            log.info("broker_tz_offset_sec=%d", offset)
        # rates[-1] is the still-forming bar; everything before it is closed.
        for r in rates[:-1]:
            t = int(r["time"])
            if last_emitted is not None and t <= last_emitted:
                continue
            last_emitted = t
            yield Candle(
                ts=datetime.fromtimestamp(t - offset, tz=timezone.utc),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
        await asyncio.sleep(poll_sec)


def xauusd_live():
    """Factory for the CLI --source flag."""
    return stream_candles()


def us100_live():
    """CLI --source factory: Nasdaq 100 native M1 feed."""
    return stream_candles(symbol="US100.ecn")


def us500_live():
    """CLI --source factory: S&P 500 native M1 feed."""
    return stream_candles(symbol="US500.ecn")


def xagusd_live():
    """CLI --source factory: silver native M1 feed."""
    return stream_candles(symbol="XAGUSD.ecn")
