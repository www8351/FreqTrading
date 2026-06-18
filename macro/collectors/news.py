"""News-headline collector — RSS (stdlib xml + urllib). Free, no key.

Pulls item titles + publish times from finance RSS feeds; the lexicon scorer in
``macro.sentiment`` turns them into per-asset sentiment. ``parse_rss`` is pure
(stdlib ``xml.etree``); ``fetch_rss`` does network behind an injectable opener (no
live network in tests). Free + open-source per D-013.
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..sentiment import Headline

# A couple of broad finance feeds (keyless). Extend/replace as needed.
DEFAULT_FEEDS = (
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.investing.com/rss/news_25.rss",
)
_UA = "Mozilla/5.0 (orb-macro sidecar; +local)"


def _default_opener(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted)
        return r.read()


def _parse_pub(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_rss(xml_bytes: bytes, source: str = "rss",
              default_ts: datetime | None = None) -> list[Headline]:
    """Decode RSS 2.0 ``<item>``s into Headlines. Items with no parseable date fall
    back to ``default_ts`` (drop if that is None). Tolerant of malformed XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out: list[Headline] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        ts = _parse_pub(item.findtext("pubDate")) or default_ts
        if ts is None:
            continue
        out.append(Headline(ts=ts, text=title, source=source))
    return out


def fetch_rss(url: str, opener=None, default_ts: datetime | None = None) -> list[Headline]:
    return parse_rss((opener or _default_opener)(url), source=url, default_ts=default_ts)


def fetch_all(feeds=DEFAULT_FEEDS, opener=None,
              default_ts: datetime | None = None) -> list[Headline]:
    """Fetch every feed, concatenating headlines. A failed feed is skipped."""
    out: list[Headline] = []
    for url in feeds:
        try:
            out.extend(fetch_rss(url, opener=opener, default_ts=default_ts))
        except Exception:                            # noqa: BLE001 (one bad feed != fatal)
            continue
    return out
