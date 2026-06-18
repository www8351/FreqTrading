"""Scorer layer 4 — AI / semiconductor thematic bias for the equity indices.

Semiconductor momentum (NVDA/AVGO/TSM/AMD via Stooq, ``proxies.semis_momentum``)
is the cleanest free proxy for "AI theme strength". Strong up-momentum tilts the
tech-heavy **US100** (Nasdaq) bullish and, more lightly, **US500**; weakness tilts
them bearish. It does not touch the metals.

Confidence is magnitude-scaled and capped at 0.6 — a strong, broad semis move can
reach the veto bar for the indices, but a weak one only nudges bias. Pure + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

_CONF_CAP = 0.6
_W_US100 = 0.4
_W_US500 = 0.2
_BIAS_DEADBAND = 0.1


@dataclass(frozen=True, slots=True)
class ThemeResult:
    score: float                 # [-1..+1] basket momentum, + = AI/semis strong
    drivers: tuple[str, ...]
    confidence: float            # [0..1], magnitude-scaled, capped


def assess_semis(momentum_by_symbol: dict) -> ThemeResult:
    """Average the per-symbol semis momentum into one thematic verdict."""
    vals = [v for v in momentum_by_symbol.values() if v is not None]
    if not vals:
        return ThemeResult(0.0, (), 0.0)
    score = max(-1.0, min(1.0, sum(vals) / len(vals)))
    conf = min(_CONF_CAP, abs(score))
    n = len(vals)
    return ThemeResult(round(score, 3),
                       (f"semis_mom={score:+.2f}(n={n})",), round(conf, 3))


def _bias(score: float) -> str:
    return "bullish" if score > _BIAS_DEADBAND else "bearish" if score < -_BIAS_DEADBAND else "neutral"


def merge_thematic(state: dict, theme: ThemeResult | None) -> dict:
    """Tilt US100 (and lighter US500) by the semis theme (in place) + raise conf."""
    if theme is None or theme.score == 0.0:
        return state
    for sym, w in (("US100", _W_US100), ("US500", _W_US500)):
        av = state["assets"].get(sym)
        if av is None:
            continue
        sc = round(max(-1.0, min(1.0, av["score"] + w * theme.score)), 3)
        av["score"] = sc
        av["bias"] = _bias(sc)
        for d in theme.drivers:
            if d not in av["drivers"]:
                av["drivers"] = list(av["drivers"]) + [d]
    g = state["global"]
    g["confidence"] = max(g.get("confidence", 0.0), theme.confidence)
    return state
