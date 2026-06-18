"""GDELT DOC 2.0 collector — global news tone + volume for geopolitics/war-spike.

Free, no API key, ~15-min updates. We query conflict terms and pull two timelines:
``timelinetone`` (average article tone, negative = bad news) and ``timelinevol``
(share of coverage). A sharp tone DROP together with a volume SPIKE is the
war-spike candidate; market proxies (``proxies.py``) confirm it before any hard
action (``geopolitics.assess``).

Pure ``parse_timeline`` + ``tone_features``; network ``fetch_timeline`` behind an
injectable opener (no live network in tests). Free + open-source per D-013.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_QUERY = ('(war OR invasion OR airstrike OR missile OR "military strike" '
                 'OR conflict OR sanctions) sourcelang:english')
_UA = "Mozilla/5.0 (orb-macro sidecar; +local)"


def _default_opener(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted)
        return r.read()


def _parse_gdelt_ts(raw: str) -> datetime:
    # GDELT timeline dates: "YYYYMMDDTHHMMSSZ"
    return datetime.strptime(str(raw), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def parse_timeline(data) -> list[tuple[datetime, float]]:
    """Decode a GDELT timeline JSON into ascending (ts, value) pairs. Tolerant."""
    out: list[tuple[datetime, float]] = []
    series = (data or {}).get("timeline", [])
    if not series:
        return out
    for point in series[0].get("data", []):
        try:
            out.append((_parse_gdelt_ts(point["date"]), float(point["value"])))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda p: p[0])
    return out


def fetch_timeline(query: str = DEFAULT_QUERY, mode: str = "timelinetone",
                   timespan: str = "3d", opener=None) -> list[tuple[datetime, float]]:
    params = urllib.parse.urlencode({"query": query, "mode": mode,
                                     "format": "json", "timespan": timespan})
    raw = (opener or _default_opener)(f"{BASE}?{params}")
    return parse_timeline(json.loads(raw))


def tone_features(tones: list[float], vols: list[float]) -> tuple[float, float, float]:
    """Reduce tone + volume series to (tone_now, tone_baseline, volume_zscore).

    Baseline = mean of all-but-last; volume z = (now - mean_base) / std_base, with a
    saturated value when the baseline has no variance (constant then a jump)."""
    tone_now = tones[-1]
    base = tones[:-1] or tones
    tone_base = sum(base) / len(base)

    vbase = vols[:-1] or vols
    vmean = sum(vbase) / len(vbase)
    var = sum((x - vmean) ** 2 for x in vbase) / len(vbase)
    std = var ** 0.5
    vnow = vols[-1]
    if std == 0:
        vol_z = 10.0 if vnow > vmean else 0.0
    else:
        vol_z = (vnow - vmean) / std
    return tone_now, tone_base, vol_z
