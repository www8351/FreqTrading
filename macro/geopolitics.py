"""Scorer layer 3 — geopolitics / war-spike from GDELT tone + market-proxy confirm.

Two severities, deliberately separated to bound false positives (the M3 risk):

- **war_spike** = news tone-spike AND a market-proxy confirm (VIX elevated). This is
  the only geopolitics signal that takes *hard* action: it sets ``blackout.active``
  (reason ``war_spike``), so a ``guard``-mode bot CLOSES open positions + halts
  entries. High confidence.
- **risk_off** = either signal alone (tone-spike OR VIX elevated). *Soft*: it tilts
  per-asset bias (metals up, equities down) and can veto bias-conflicting NEW
  entries in ``filter``/``guard`` mode, but never closes an open position.

Thresholds are priors — calibrate against backtest in M6. Pure + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# risk-off tilt direction per bare asset key (metals bid, equities offered)
_TILT_SIGN = {"XAUUSD": +1.0, "XAGUSD": +1.0, "US100": -1.0, "US500": -1.0}


@dataclass(frozen=True, slots=True)
class GeoResult:
    risk_off: bool
    war_spike: bool
    score: float                 # [0..1] severity (risk-off magnitude)
    drivers: tuple[str, ...]
    confidence: float            # [0..1]


def assess(tone_now: float, tone_base: float, vol_z: float,
           vix: float | None = None, *, tone_drop_min: float = 2.0,
           vol_z_min: float = 1.5, vix_threshold: float = 25.0) -> GeoResult:
    """Combine a GDELT tone drop + volume spike with a VIX confirm into a verdict."""
    drivers: list[str] = []
    tone_drop = tone_base - tone_now                  # + = tone fell (worse news)
    tone_spike = tone_drop >= tone_drop_min and vol_z >= vol_z_min
    if tone_spike:
        drivers.append(f"gdelt_tone_drop={tone_drop:.1f}")
    proxy_confirm = vix is not None and vix >= vix_threshold
    if proxy_confirm:
        drivers.append(f"vix={vix:.1f}")

    war_spike = tone_spike and proxy_confirm
    risk_off = tone_spike or proxy_confirm

    score = 0.0
    if tone_spike:
        score += 0.6 * min(1.0, tone_drop / (2 * tone_drop_min))
    if proxy_confirm:
        score += 0.2 + 0.2 * min(1.0, (vix - vix_threshold) / 15.0)
    score = min(1.0, score)
    confidence = 0.9 if war_spike else (0.6 if risk_off else 0.0)
    return GeoResult(risk_off, war_spike, round(score, 3), tuple(drivers), confidence)


def _bias(score: float) -> str:
    return "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral"


def merge_geo(state: dict, geo: GeoResult | None, now: datetime,
              cooldown_min: int = 120) -> dict:
    """Apply a GeoResult onto an already-scored MacroState (in place) + return it."""
    if geo is None or not geo.risk_off:
        return state

    for sym, av in state["assets"].items():
        sign = _TILT_SIGN.get(sym)
        if sign is None:
            continue
        sc = round(max(-1.0, min(1.0, av["score"] + sign * geo.score)), 3)
        av["score"] = sc
        av["bias"] = _bias(sc)
        if "risk_off" not in av["drivers"]:
            av["drivers"] = list(av["drivers"]) + ["risk_off"]

    g = state["global"]
    g["risk_regime"] = "risk_off"
    g["risk_score"] = round(min(g.get("risk_score", 0.0), -geo.score), 3)
    g["confidence"] = max(g.get("confidence", 0.0), geo.confidence)
    if geo.war_spike:
        until = (now + timedelta(minutes=cooldown_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        g["blackout"] = {"active": True, "until": until, "reason": "war_spike"}
    return state
