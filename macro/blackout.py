"""Blackout-window math over a calendar of ``RawEvent``s. Pure + stdlib.

A high-impact event at ``ts`` blacks out entries over ``[ts - pre_min, ts + post_min]``
(default 30/30, aligned with Brain_X ``pre_market_blackout``). ``active_blackout``
tells the daemon whether *now* sits in any such window (the consumer just reads the
resulting ``blackout.active`` flag); ``upcoming_events`` fills the forward
``events[]`` list in the MacroState for transparency.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .normalizer import RawEvent

DEFAULT_PRE_MIN = 30
DEFAULT_POST_MIN = 30
DEFAULT_IMPACTS = ("high",)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def active_blackout(events, now: datetime, pre_min: int = DEFAULT_PRE_MIN,
                    post_min: int = DEFAULT_POST_MIN,
                    impacts=DEFAULT_IMPACTS) -> dict | None:
    """Return ``{"active": True, "until": iso, "reason": str}`` if ``now`` is inside
    any qualifying event window, else ``None``.

    When several windows overlap ``now``, ``until`` is the latest end and ``reason``
    joins the distinct event kinds (so a clustered CPI+FOMC day reads clearly).
    """
    pre = timedelta(minutes=pre_min)
    post = timedelta(minutes=post_min)
    hits = []
    for ev in events:
        if ev.impact not in impacts:
            continue
        start, end = ev.ts - pre, ev.ts + post
        if start <= now <= end:
            hits.append((end, ev.reason()))
    if not hits:
        return None
    until = max(end for end, _ in hits)
    reasons = list(dict.fromkeys(r for _, r in hits))   # de-dup, keep order
    return {"active": True, "until": _iso(until), "reason": ",".join(reasons)}


def upcoming_events(events, now: datetime, horizon_h: int = 48,
                    impacts=("high", "medium"),
                    pre_min: int = DEFAULT_PRE_MIN,
                    post_min: int = DEFAULT_POST_MIN) -> list[dict]:
    """Forward-looking events within ``horizon_h`` hours, for MacroState.events[]."""
    horizon = now + timedelta(hours=horizon_h)
    out = []
    for ev in events:
        if ev.impact not in impacts or not (now <= ev.ts <= horizon):
            continue
        out.append({
            "id": f"{ev.kind}-{ev.ts.strftime('%Y-%m-%dT%H:%M')}",
            "ts": _iso(ev.ts),
            "impact": ev.impact,
            "kind": ev.kind,
            "currency": ev.currency,
            "blackout_pre_min": pre_min,
            "blackout_post_min": post_min,
        })
    return out
