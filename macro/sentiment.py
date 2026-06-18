"""Scorer layer 2 — news-headline sentiment, lightweight stdlib lexicon backend.

M4 ships a dependency-free finance lexicon scorer (no torch/transformers): score a
headline by net positive/negative finance terms (with light negation), route it to
the affected assets by keyword, then aggregate per asset as a half-life-weighted
mean over a recent window. A self-hosted FinBERT backend can later replace
``score_text`` behind the same interface (M4-later) — the aggregation/routing/merge
stay identical.

Sentiment is treated as a SOFT signal: it tilts per-asset bias and raises global
confidence only up to ``_SENT_CONF_CAP`` (< the veto threshold), so lexicon-grade
sentiment never vetoes a trade on its own — it only matters combined with a
calendar/geo signal. Pure + stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_POS = {
    "rally", "rallies", "surge", "surges", "soar", "soars", "jump", "jumps",
    "gain", "gains", "beat", "beats", "upgrade", "upgraded", "bullish", "optimism",
    "optimistic", "rebound", "recovery", "strong", "strength", "climbs", "rises",
    "rose", "boost", "boosts", "record", "outperform", "easing", "dovish", "soften",
}
_NEG = {
    "crash", "crashes", "plunge", "plunges", "slump", "slumps", "tumble", "tumbles",
    "fall", "falls", "fell", "drop", "drops", "miss", "misses", "downgrade",
    "downgraded", "bearish", "fear", "fears", "recession", "selloff", "sinks",
    "slides", "weak", "weakness", "default", "crisis", "war", "conflict",
    "sanctions", "hawkish", "layoffs", "slowdown", "hot", "spike",
}
_NEGATORS = {"not", "no", "never", "without", "nt", "less"}

# single-token keywords + multiword phrases that route a headline to an asset
_ROUTES = {
    "XAUUSD": {"gold", "bullion", "xau", "xauusd"},
    "XAGUSD": {"silver", "xag", "xagusd"},
    "US100": {"nasdaq", "tech", "semiconductor", "semiconductors", "chip", "chips",
              "ai", "nvidia", "amd", "broadcom", "tsmc", "apple", "microsoft", "us100"},
    "US500": {"sp500", "dow", "us500"},
}
_PHRASES = {
    "XAUUSD": ("gold price",),
    "US100": ("artificial intelligence", "wall street tech"),
    "US500": ("s&p", "s&p 500", "wall street", "blue chip"),
}
_EQUITY_TOKENS = {"equities", "stocks", "shares", "equity"}        # -> both indices
_GLOBAL_TOKENS = {"fed", "fomc", "inflation", "cpi", "recession", "rates", "rate",
                  "treasury", "yields", "dollar", "war", "tariffs", "geopolitical"}

_TOKEN_RE = re.compile(r"[a-z0-9&]+")
_SENT_CONF_CAP = 0.5            # lexicon sentiment confidence ceiling (< veto 0.6)
_SENT_CONF_FULL = 5            # headlines for ~full (capped) confidence
_BIAS_DEADBAND = 0.1


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def score_text(text: str) -> float | None:
    """Net headline sentiment in [-1..+1], or None if no sentiment terms.
    Light negation: a negator flips the next sentiment token."""
    pos = neg = 0
    negate = False
    for t in _tokens(text):
        if t in _NEGATORS:
            negate = True
            continue
        s = 1 if t in _POS else -1 if t in _NEG else 0
        if s != 0:
            if negate:
                s = -s
            if s > 0:
                pos += 1
            else:
                neg += 1
        negate = False
    total = pos + neg
    return None if total == 0 else (pos - neg) / total


def route_assets(text: str, symbols) -> set[str]:
    """Which assets a headline is about (keyword/phrase routing). Global macro terms
    fan out to every symbol; equity terms to both indices."""
    low = text.lower()
    toks = set(_tokens(text))
    hit: set[str] = set()
    for sym, kws in _ROUTES.items():
        if sym in symbols and toks & kws:
            hit.add(sym)
    for sym, phrases in _PHRASES.items():
        if sym in symbols and any(p in low for p in phrases):
            hit.add(sym)
    if toks & _EQUITY_TOKENS:
        hit.update(s for s in ("US100", "US500") if s in symbols)
    if toks & _GLOBAL_TOKENS:
        hit.update(symbols)
    return hit


@dataclass(frozen=True, slots=True)
class Headline:
    ts: datetime              # tz-aware UTC
    text: str
    source: str = "news"


def aggregate(headlines, now: datetime, symbols, lookback_h: float = 24.0,
              half_life_h: float = 8.0) -> dict:
    """Half-life-weighted mean sentiment per asset over the recent window."""
    acc = {s: 0.0 for s in symbols}
    wt = {s: 0.0 for s in symbols}
    cnt = {s: 0 for s in symbols}
    for h in headlines:
        age_h = (now - h.ts).total_seconds() / 3600.0
        if age_h < 0 or age_h > lookback_h:
            continue
        sv = score_text(h.text)
        if sv is None:
            continue
        targets = route_assets(h.text, symbols)
        if not targets:
            continue
        w = 0.5 ** (age_h / half_life_h)
        for sym in targets:
            if sym in acc:
                acc[sym] += w * sv
                wt[sym] += w
                cnt[sym] += 1
    return {s: {"sentiment": round(acc[s] / wt[s], 3) if wt[s] > 0 else 0.0,
                "n": cnt[s]} for s in symbols}


def _bias(score: float) -> str:
    return "bullish" if score > _BIAS_DEADBAND else "bearish" if score < -_BIAS_DEADBAND else "neutral"


def merge_sentiment(state: dict, agg: dict | None, weight: float = 0.3) -> dict:
    """Tilt per-asset scores by sentiment (in place) + raise capped confidence."""
    if not agg:
        return state
    max_n = 0
    for sym, av in state["assets"].items():
        a = agg.get(sym)
        if not a or a["n"] == 0 or a["sentiment"] == 0.0:
            continue
        sc = round(max(-1.0, min(1.0, av["score"] + weight * a["sentiment"])), 3)
        av["score"] = sc
        av["bias"] = _bias(sc)
        tag = f"sentiment={a['sentiment']:+.2f}"
        if tag not in av["drivers"]:
            av["drivers"] = list(av["drivers"]) + [tag]
        max_n = max(max_n, a["n"])
    if max_n > 0:
        conf = min(_SENT_CONF_CAP, max_n / _SENT_CONF_FULL * _SENT_CONF_CAP)
        g = state["global"]
        g["confidence"] = max(g.get("confidence", 0.0), round(conf, 3))
    return state
