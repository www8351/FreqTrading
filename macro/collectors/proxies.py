"""Market-proxy collector.

Two roles:
- **Risk-off confirmation (M3):** VIX / broad-dollar from FRED (free key) — a GDELT
  tone-spike is only a war-spike when the market agrees (VIX elevated).
- **AI/semis thematic (M5):** semiconductor momentum from Stooq daily CSV (free, no
  key, stdlib ``urllib`` + ``csv``) — NVDA/AVGO/TSM/AMD; strong momentum tilts the
  tech-heavy US100 (and, lighter, US500) bullish. See ``macro.thematic``.

Stdlib only; injectable opener for tests (no live network in the suite).
"""

from __future__ import annotations

import csv
import io
import urllib.request
from datetime import date

from . import fred

VIX_SERIES = "VIXCLS"
DXY_SERIES = "DTWEXBGS"

# Stooq daily-CSV semis basket (".us" = US equities). SOX index is "^sox".
SEMIS = ("nvda.us", "avgo.us", "tsm.us", "amd.us")
STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}&i=d"
_UA = "Mozilla/5.0 (orb-macro sidecar; +local)"


def get_vix(api_key: str | None = None, opener=None) -> float | None:
    obs = fred.fetch_series(VIX_SERIES, api_key=api_key, opener=opener, limit=5)
    return obs[-1][1] if obs else None


def get_dollar(api_key: str | None = None, opener=None) -> float | None:
    obs = fred.fetch_series(DXY_SERIES, api_key=api_key, opener=opener, limit=5)
    return obs[-1][1] if obs else None


def _stooq_opener(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted)
        return r.read()


def parse_stooq(csv_bytes: bytes) -> list[tuple[date, float]]:
    """Decode a Stooq daily CSV (Date,Open,High,Low,Close,Volume) into ascending
    (date, close) pairs. Tolerant of error bodies / junk rows -> []."""
    out: list[tuple[date, float]] = []
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", "replace")))
    for row in reader:
        try:
            out.append((date.fromisoformat(row["Date"]), float(row["Close"])))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def fetch_stooq(symbol: str, opener=None) -> list[tuple[date, float]]:
    return parse_stooq((opener or _stooq_opener)(STOOQ_URL.format(sym=symbol)))


def momentum(closes: list[float], lookback: int = 10,
             scale: float = 0.1) -> float | None:
    """Normalized rate-of-change over ``lookback`` bars, clamped to [-1..+1]
    (``scale`` = the move that saturates: 0.1 -> a 10% move == +1). None if short."""
    if len(closes) < lookback + 1:
        return None
    past, now = closes[-1 - lookback], closes[-1]
    if past <= 0:
        return None
    return max(-1.0, min(1.0, (now / past - 1.0) / scale))


def semis_momentum(symbols=SEMIS, opener=None, lookback: int = 10) -> dict[str, float]:
    """Per-symbol normalized momentum for the semis basket. Skips failed symbols."""
    out: dict[str, float] = {}
    for sym in symbols:
        try:
            closes = [c for _, c in fetch_stooq(sym, opener=opener)]
            m = momentum(closes, lookback=lookback)
            if m is not None:
                out[sym] = m
        except Exception:                            # noqa: BLE001 (one bad symbol != fatal)
            continue
    return out
