"""Pure MacroState builder for M1: neutral base + calendar blackout + events[].

Kept separate from the daemon (network/sleep) so the state shape is unit-testable
by feeding events + a fixed ``now``. Global regime/per-asset scoring stays neutral
until M2.
"""

from __future__ import annotations

from datetime import datetime

from . import DEFAULT_SYMBOLS
from . import geopolitics, scorer, sentiment, thematic
from .blackout import (DEFAULT_IMPACTS, DEFAULT_POST_MIN, DEFAULT_PRE_MIN,
                       active_blackout, upcoming_events)
from .state_writer import neutral_state


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_state(events, now: datetime, symbols=DEFAULT_SYMBOLS, ttl_sec: int = 300,
                pre_min: int = DEFAULT_PRE_MIN, post_min: int = DEFAULT_POST_MIN,
                impacts=DEFAULT_IMPACTS, horizon_h: int = 48,
                score_lookback_h: float = 36.0, half_life_h: float = 12.0,
                geo=None, news_sentiment=None, theme=None) -> dict:
    """Build the full MacroState at ``now``:

    - **scorer (M2):** released-event surprise -> per-asset bias + global regime.
    - **sentiment (M4):** optional headline-sentiment aggregate tilts per-asset bias.
    - **thematic (M5):** optional AI/semis :class:`ThemeResult` tilts US100/US500.
    - **blackout (M1):** active high-impact window override + forward events[].
    - **geopolitics (M3):** optional :class:`GeoResult` tilts bias to risk-off and,
      on a confirmed ``war_spike``, sets a hard blackout.

    Blackout takes precedence at the consumer (it vetoes regardless of score).
    """
    s = neutral_state(symbols=symbols, ttl_sec=ttl_sec, generated_at=_iso(now))

    scored = scorer.score(events, now, symbols, score_lookback_h, half_life_h)
    s["assets"] = scored["assets"]
    s["global"].update(scored["global"])       # risk_regime / risk_score / confidence

    # M4/M5: soft tilts (after calendar scores, before blackout/geo).
    s = sentiment.merge_sentiment(s, news_sentiment)
    s = thematic.merge_thematic(s, theme)

    bo = active_blackout(events, now, pre_min, post_min, impacts)
    if bo is not None:
        s["global"]["blackout"] = bo
    s["events"] = upcoming_events(events, now, horizon_h,
                                  pre_min=pre_min, post_min=post_min)

    # M3: geopolitics tilt / war-spike override (after calendar layers).
    s = geopolitics.merge_geo(s, geo, now)
    return s
