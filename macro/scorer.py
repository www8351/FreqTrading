"""Scorer layer 1 — released-event *surprise* -> per-asset bias + global regime.

For each recently-released calendar event with both a forecast and an actual,
compute a normalized surprise, look up its per-asset coefficients in the
sensitivity table, weight by impact and recency (half-life decay), and accumulate
into a per-asset score in [-1..+1]. Global ``risk_regime`` is derived from the
equities-vs-gold spread; ``confidence`` is the strongest contributing weight (so a
fresh high-impact print drives a confident bias that fades over the day).

Pure + stdlib. The consumer (``MacroGuard`` in filter mode) vetoes an entry only
when an asset's score sign opposes the trade AND global confidence >= conf_min.
"""

from __future__ import annotations

from datetime import datetime

from .normalizer import RawEvent, parse_value
from .sensitivity import SENSITIVITY

IMPACT_WEIGHT = {"high": 1.0, "medium": 0.4, "low": 0.1}
_BIAS_DEADBAND = 0.1


def surprise(ev: RawEvent) -> float | None:
    """Normalized surprise in [-1..+1]: (actual - forecast) / |forecast|, clamped.
    None when either figure is missing/unparseable (event not yet released)."""
    a, f = parse_value(ev.actual), parse_value(ev.forecast)
    if a is None or f is None:
        return None
    raw = (a - f) / max(abs(f), 1e-9)
    return max(-1.0, min(1.0, raw))


def _bias(score: float) -> str:
    if score > _BIAS_DEADBAND:
        return "bullish"
    if score < -_BIAS_DEADBAND:
        return "bearish"
    return "neutral"


def score(events, now: datetime, symbols, lookback_h: float = 36.0,
          half_life_h: float = 12.0) -> dict:
    """Return ``{"assets": {...}, "global": {...}}`` from recent released events."""
    acc = {s: 0.0 for s in symbols}
    drivers: dict[str, list[str]] = {s: [] for s in symbols}
    confidence = 0.0

    for ev in events:
        if ev.impact not in ("high", "medium"):
            continue
        age_h = (now - ev.ts).total_seconds() / 3600.0
        if age_h < 0 or age_h > lookback_h:          # only released + recent
            continue
        sv = surprise(ev)
        if sv is None:
            continue
        coeffs = SENSITIVITY.get(ev.kind)
        if not coeffs:
            continue
        weight = IMPACT_WEIGHT.get(ev.impact, 0.0) * (0.5 ** (age_h / half_life_h))
        confidence = max(confidence, weight)
        tag = f"{ev.kind}:{'hot' if sv > 0 else 'cool'}"
        for sym, coeff in coeffs.items():
            if sym not in acc:
                continue
            acc[sym] += weight * coeff * sv
            if tag not in drivers[sym]:
                drivers[sym].append(tag)

    assets = {}
    for s in symbols:
        sc = round(max(-1.0, min(1.0, acc[s])), 3)
        assets[s] = {"bias": _bias(sc), "score": sc, "horizon": "intraday",
                     "drivers": drivers[s]}

    eq = [assets[s]["score"] for s in ("US100", "US500") if s in assets]
    gold = [assets[s]["score"] for s in ("XAUUSD", "XAGUSD") if s in assets]
    eq_b = sum(eq) / len(eq) if eq else 0.0
    gold_b = sum(gold) / len(gold) if gold else 0.0
    risk = max(-1.0, min(1.0, (eq_b - gold_b) / 2.0))   # equities up & gold down = risk-on
    regime = "risk_on" if risk > 0.3 else "risk_off" if risk < -0.3 else "neutral"
    return {
        "assets": assets,
        "global": {"risk_regime": regime, "risk_score": round(risk, 3),
                   "confidence": round(max(0.0, min(1.0, confidence)), 3)},
    }
