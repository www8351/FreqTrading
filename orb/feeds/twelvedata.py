"""Twelve Data adapter for XAU/USD 1m candles.

Historical: REST ``time_series`` -> ascending list[Candle] for ``engine.replay``.
Live: a minute poller. Twelve Data's WebSocket streams price *quotes*, not closed
OHLCV bars; the engine consumes CLOSED 1m bars, so the live source polls the REST
endpoint each minute and yields newly-closed candles (the most-recent value is the
still-forming minute and is excluded).

Runtime is stdlib only: ``urllib`` for HTTP, the blocking call offloaded to a
thread via ``asyncio.to_thread`` so the event loop stays free.

Auth: pass ``api_key=`` or set ``TWELVEDATA_API_KEY`` in the environment.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..models import Candle, OrbError, validate

BASE_URL = "https://api.twelvedata.com/time_series"
DEFAULT_SYMBOL = "XAU/USD"


class TwelveDataError(OrbError):
    """Twelve Data API or response error."""


# --------------------------------------------------------------------------- #
# Parsing (pure — unit-testable without network)
# --------------------------------------------------------------------------- #
def _parse_dt(raw: str) -> datetime:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # last resort: ISO-8601
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_time_series(data: dict) -> list[Candle]:
    """Convert a Twelve Data ``time_series`` payload into ascending Candles."""
    if data.get("status") == "error":
        raise TwelveDataError(f"{data.get('code')}: {data.get('message')}")
    values = data.get("values")
    if not values:
        raise TwelveDataError(f"no values in response: {data!r}")

    out: list[Candle] = []
    for row in values:
        vol = row.get("volume")
        c = Candle(
            ts=_parse_dt(row["datetime"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(vol) if vol not in (None, "") else 0.0,
        )
        validate(c)
        out.append(c)
    out.sort(key=lambda c: c.ts)
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "orb/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:  # network / DNS / timeout
        raise TwelveDataError(f"HTTP error fetching {BASE_URL}: {e}") from e


def _resolve_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("TWELVEDATA_API_KEY")
    if not key:
        raise TwelveDataError("TWELVEDATA_API_KEY not set (pass api_key= or env var)")
    return key


def fetch_candles(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = "1min",
    outputsize: int = 500,
    api_key: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[Candle]:
    """Fetch historical candles (ascending, closed bars) from Twelve Data."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": str(outputsize),
        "apikey": _resolve_key(api_key),
        "timezone": "UTC",
        "order": "ASC",
        "format": "JSON",
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    return parse_time_series(_http_get_json(url))


# --------------------------------------------------------------------------- #
# Live poller (async source for CandleStream)
# --------------------------------------------------------------------------- #
async def stream_candles(
    symbol: str = DEFAULT_SYMBOL,
    api_key: str | None = None,
    interval: str = "1min",
    poll_seconds: float = 60.0,
    warmup_size: int = 50,
    max_polls: int | None = None,
    _fetch=None,
):
    """Async generator of newly-closed candles, polling once per ``poll_seconds``.

    The latest value each poll is the still-forming minute and is dropped; only
    bars strictly newer than the last emitted ts are yielded (dedup across polls).
    The first poll requests ``warmup_size`` bars so the engine can warm indicators
    within the current session; later polls request a small lookback.

    ``_fetch(symbol, interval, size, key)`` is injectable for tests; defaults to
    the live REST fetch run in a worker thread.
    """
    key = _resolve_key(api_key)
    fetch = _fetch or (lambda s, i, n, k: fetch_candles(s, i, n, k))
    last_ts = None
    polls = 0

    while max_polls is None or polls < max_polls:
        size = warmup_size if polls == 0 else max(5, 1)
        candles = await asyncio.to_thread(fetch, symbol, interval, size, key)
        polls += 1

        closed = candles[:-1] if len(candles) >= 1 else []
        for c in closed:
            if last_ts is None or c.ts > last_ts:
                last_ts = c.ts
                yield c

        if max_polls is not None and polls >= max_polls:
            break
        await asyncio.sleep(poll_seconds)


# --------------------------------------------------------------------------- #
# CLI convenience factories
# --------------------------------------------------------------------------- #
def xauusd_history(outputsize: int = 500, api_key: str | None = None) -> list[Candle]:
    return fetch_candles(DEFAULT_SYMBOL, "1min", outputsize, api_key)


def xauusd_live(poll_seconds: float = 60.0):
    """Async source for ``python -m orb live --source orb.feeds.twelvedata:xauusd_live``."""
    return stream_candles(DEFAULT_SYMBOL, poll_seconds=poll_seconds)
