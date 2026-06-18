"""Data-source adapters that produce engine `Candle` objects.

Adapters are decoupled from the engine: each exposes a historical fetch
(-> list[Candle] for engine.replay) and/or an async live source (-> CandleStream).

Currently implemented:
    twelvedata  XAU/USD 1m via the Twelve Data cloud API (REST historical + poll live)
"""

from .twelvedata import (
    TwelveDataError,
    fetch_candles,
    parse_time_series,
    stream_candles,
    xauusd_history,
    xauusd_live,
)

__all__ = [
    "TwelveDataError", "fetch_candles", "parse_time_series", "stream_candles",
    "xauusd_history", "xauusd_live",
]
