"""M1 sidecar daemon: keep ``macro_state.json`` fresh with calendar blackouts.

Two cadences, decoupled on purpose:
- **fetch** the calendar slowly (it changes by the day) — default every 15 min;
- **write** the state often (default every 60s) so ``blackout.active`` flips at
  minute resolution around an event and ``generated_at`` stays inside ``ttl_sec``.

Fail-safe: a failed fetch never crashes the loop and never clears a good calendar —
the daemon keeps the last events and keeps writing; if it dies entirely the state
ages past TTL and consumers fall back to ``default_when_stale=allow`` (trade as
today). Network + sleep live here; the testable core is ``build_state`` (in
``build.py``) and ``run_once``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import DEFAULT_SYMBOLS, geopolitics, sentiment, thematic
from .blackout import DEFAULT_POST_MIN, DEFAULT_PRE_MIN
from .build import build_state
from .collectors import forexfactory, gdelt, news, proxies
from .state_writer import write_state

log = logging.getLogger("macro.daemon")


def run_once(events, out: str, now: datetime, geo=None, news_sentiment=None,
             theme=None, **kw) -> dict:
    """Build the state from ``events`` (+ optional geo/news/theme) and write it."""
    state = build_state(events, now, geo=geo, news_sentiment=news_sentiment,
                        theme=theme, **kw)
    write_state(state, out)
    return state


def _default_news_provider(feeds=news.DEFAULT_FEEDS, symbols=DEFAULT_SYMBOLS,
                           opener=None, lookback_h: float = 24.0,
                           half_life_h: float = 8.0):
    """provider(now) -> per-asset sentiment aggregate, or None on failure."""
    def provider(now):
        try:
            heads = news.fetch_all(feeds, opener=opener, default_ts=now)
            if not heads:
                return None
            return sentiment.aggregate(heads, now, symbols, lookback_h, half_life_h)
        except Exception as e:                       # noqa: BLE001 (degrade)
            log.warning("news_provider FAILED err=%s", e)
            return None
    return provider


def _default_thematic_provider(symbols=DEFAULT_SYMBOLS, opener=None,
                               lookback: int = 10):
    """provider(now) -> AI/semis ThemeResult, or None on failure."""
    def provider(now):
        try:
            mom = proxies.semis_momentum(opener=opener, lookback=lookback)
            if not mom:
                return None
            return thematic.assess_semis(mom)
        except Exception as e:                       # noqa: BLE001 (degrade)
            log.warning("thematic_provider FAILED err=%s", e)
            return None
    return provider


def _default_geo_provider(query: str = gdelt.DEFAULT_QUERY, fred_key: str | None = None,
                          gdelt_opener=None, fred_opener=None):
    """Build a provider(now) -> GeoResult|None that fetches GDELT tone + VIX confirm.

    Any failure -> None (the daemon keeps the last geo and degrades). Opt-in: the
    daemon only calls geo at all when a provider is supplied."""
    def provider(now):
        try:
            tones = [v for _, v in gdelt.fetch_timeline(query, mode="timelinetone",
                                                         opener=gdelt_opener)]
            vols = [v for _, v in gdelt.fetch_timeline(query, mode="timelinevol",
                                                       opener=gdelt_opener)]
            if len(tones) < 2 or len(vols) < 2:
                return None
            tn, tb, vz = gdelt.tone_features(tones, vols)
            try:
                vix = proxies.get_vix(api_key=fred_key, opener=fred_opener)
            except Exception:                       # noqa: BLE001 (proxy optional)
                vix = None
            return geopolitics.assess(tn, tb, vz, vix=vix)
        except Exception as e:                       # noqa: BLE001 (degrade on any error)
            log.warning("geo_provider FAILED err=%s", e)
            return None
    return provider


def _try_fetch(url: str, opener=None):
    try:
        ev = forexfactory.fetch(url, opener=opener)
        log.info("calendar_fetch ok url=%s events=%d", url, len(ev))
        return ev
    except Exception as e:                      # noqa: BLE001 (degrade on any error)
        log.warning("calendar_fetch FAILED url=%s err=%s", url, e)
        return None


def run(out: str = "macro_state.json", url: str = forexfactory.DEFAULT_URL,
        fetch_interval: float = 900.0, write_interval: float = 60.0,
        ttl_sec: int = 300, symbols=DEFAULT_SYMBOLS,
        pre_min: int = DEFAULT_PRE_MIN, post_min: int = DEFAULT_POST_MIN,
        opener=None, now_fn=None, geo_provider=None, news_provider=None,
        thematic_provider=None, max_iters: int | None = None) -> None:
    """Run the write loop. ``max_iters`` bounds it for tests; None = forever.

    ``geo_provider`` (M3), ``news_provider`` (M4) and ``thematic_provider`` (M5) are
    opt-in: when None, that layer is skipped. All refresh at the slow fetch cadence;
    a None result (fetch failed) keeps the last value so the layer degrades rather
    than flapping.
    """
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    events: list = []
    geo = None
    news_sent = None
    theme = None
    last_fetch: float | None = None
    i = 0
    while max_iters is None or i < max_iters:
        i += 1
        mono = time.monotonic()
        now = now_fn()
        if last_fetch is None or (mono - last_fetch) >= fetch_interval:
            fetched = _try_fetch(url, opener=opener)
            if fetched is not None:
                events = fetched
                last_fetch = mono
            elif last_fetch is None:
                last_fetch = mono               # don't hammer a dead host every write
            if geo_provider is not None:
                g = geo_provider(now)
                if g is not None:               # keep last geo on a failed refresh
                    geo = g
            if news_provider is not None:
                ns = news_provider(now)
                if ns is not None:              # keep last sentiment on failed refresh
                    news_sent = ns
            if thematic_provider is not None:
                th = thematic_provider(now)
                if th is not None:              # keep last theme on failed refresh
                    theme = th
        try:
            state = run_once(events, out, now, geo=geo, news_sentiment=news_sent,
                             theme=theme, symbols=symbols, ttl_sec=ttl_sec,
                             pre_min=pre_min, post_min=post_min)
            bo = state["global"]["blackout"]
            if bo["active"]:
                log.info("BLACKOUT active until=%s reason=%s", bo["until"], bo["reason"])
        except OSError as e:
            log.warning("state_write FAILED err=%s", e)
        if max_iters is None or i < max_iters:
            time.sleep(write_interval)
