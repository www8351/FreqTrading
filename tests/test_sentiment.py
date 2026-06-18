"""M4 tests: lexicon sentiment, asset routing, RSS parse, soft-tilt behavior."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from macro import daemon, sentiment
from macro.build import build_state
from macro.collectors import news
from macro.sentiment import Headline, aggregate, merge_sentiment, route_assets, score_text
from macro.state_writer import neutral_state, write_state
from orb.macroguard import MacroGuard
from orb.models import Direction, Signal, SignalKind, State

UTC = timezone.utc
NOW = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)
SYMS = ("XAUUSD", "US100", "US500", "XAGUSD")


def hl(text, age_h=1.0):
    return Headline(ts=NOW - timedelta(hours=age_h), text=text)


def mk_entry(direction):
    return Signal(ts=NOW, kind=SignalKind.ENTRY, direction=direction, price=2000.0,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason="b", qty=0.04)


# --- score_text -------------------------------------------------------------
def test_score_text_positive_negative():
    assert score_text("Gold rallies to record high") == 1.0
    assert score_text("Stocks crash on recession fears") == -1.0
    assert score_text("Market opens Monday") is None


def test_score_text_negation():
    assert score_text("Gold not bullish today") == -1.0


# --- routing ----------------------------------------------------------------
def test_route_assets():
    assert route_assets("Gold price climbs", SYMS) == {"XAUUSD"}
    assert route_assets("Silver surges", SYMS) == {"XAGUSD"}
    assert route_assets("Nvidia chip demand for AI", SYMS) == {"US100"}
    assert route_assets("Stocks slump", SYMS) == {"US100", "US500"}
    assert route_assets("Fed signals rate cut", SYMS) == set(SYMS)   # global term
    assert route_assets("Rain in Spain", SYMS) == set()              # no 'ai' false hit


# --- aggregate --------------------------------------------------------------
def test_aggregate_weighted_mean_and_count():
    agg = aggregate([hl("Gold rallies", 1), hl("Gold tumbles", 1)], NOW, SYMS)
    assert agg["XAUUSD"]["n"] == 2
    assert -1.0 <= agg["XAUUSD"]["sentiment"] <= 1.0


def test_aggregate_recency_weight():
    fresh = aggregate([hl("Gold rallies", 0.5), hl("Gold tumbles", 20)], NOW, SYMS)
    assert fresh["XAUUSD"]["sentiment"] > 0          # recent positive dominates


def test_aggregate_ignores_stale():
    agg = aggregate([hl("Gold rallies", 30)], NOW, SYMS)   # > 24h lookback
    assert agg["XAUUSD"]["n"] == 0


# --- merge_sentiment --------------------------------------------------------
def test_merge_tilts_and_caps_confidence():
    s = neutral_state(generated_at="2026-06-17T14:00:00Z")
    agg = {"XAUUSD": {"sentiment": 0.8, "n": 5}, "US100": {"sentiment": 0.0, "n": 0},
           "US500": {"sentiment": 0.0, "n": 0}, "XAGUSD": {"sentiment": 0.0, "n": 0}}
    merge_sentiment(s, agg)
    assert s["assets"]["XAUUSD"]["bias"] == "bullish"
    assert s["global"]["confidence"] == 0.5          # capped (< veto threshold 0.6)


def test_merge_none_unchanged():
    s = neutral_state(generated_at="2026-06-17T14:00:00Z")
    assert merge_sentiment(s, None)["assets"]["XAUUSD"]["score"] == 0.0


# --- RSS parsing ------------------------------------------------------------
SAMPLE_RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel>'
              b'<item><title>Gold rallies to record high</title>'
              b'<pubDate>Wed, 17 Jun 2026 13:30:00 GMT</pubDate></item>'
              b'<item><title>Nasdaq tech stocks slump as chip fears mount</title>'
              b'<pubDate>Wed, 17 Jun 2026 13:00:00 GMT</pubDate></item>'
              b'</channel></rss>')


def test_parse_rss():
    heads = news.parse_rss(SAMPLE_RSS)
    assert len(heads) == 2
    assert heads[0].text.startswith("Gold rallies")
    assert heads[0].ts == datetime(2026, 6, 17, 13, 30, tzinfo=UTC)


def test_parse_rss_malformed_and_default_ts():
    assert news.parse_rss(b"<not xml") == []
    no_date = (b'<rss><channel><item><title>Gold jumps</title></item>'
               b'</channel></rss>')
    heads = news.parse_rss(no_date, default_ts=NOW)
    assert heads and heads[0].ts == NOW


# --- build_state integration ------------------------------------------------
def test_build_state_sentiment_tilt():
    agg = {"XAUUSD": {"sentiment": -0.8, "n": 5}, "US100": {"sentiment": 0.0, "n": 0},
           "US500": {"sentiment": 0.0, "n": 0}, "XAGUSD": {"sentiment": 0.0, "n": 0}}
    s = build_state([], NOW, news_sentiment=agg)
    assert s["assets"]["XAUUSD"]["bias"] == "bearish"
    assert s["global"]["confidence"] == 0.5


# --- soft signal: sentiment alone does NOT veto -----------------------------
def test_sentiment_alone_does_not_veto(tmp_path):
    p = str(tmp_path / "macro_state.json")
    agg = {"XAUUSD": {"sentiment": -0.9, "n": 8}, "US100": {"sentiment": 0.0, "n": 0},
           "US500": {"sentiment": 0.0, "n": 0}, "XAGUSD": {"sentiment": 0.0, "n": 0}}
    write_state(build_state([], NOW, news_sentiment=agg), p)
    g = MacroGuard("XAUUSD.ecn", p, now_fn=lambda: NOW, conf_min=0.6)
    # bearish gold tilt but confidence capped at 0.5 < 0.6 -> a LONG is NOT vetoed
    assert g.evaluate_entry(mk_entry(Direction.LONG)).action == "ALLOW"


# --- daemon with injected news provider -------------------------------------
def test_daemon_run_with_news_provider(tmp_path):
    out = tmp_path / "macro_state.json"
    provider = daemon._default_news_provider(symbols=SYMS,
                                             opener=lambda url: SAMPLE_RSS)
    daemon.run(out=str(out), opener=lambda url: b"[]", news_provider=provider,
               now_fn=lambda: NOW, write_interval=0.0, max_iters=1)
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk["assets"]["XAUUSD"]["score"] > 0     # "Gold rallies" -> bullish
    assert disk["assets"]["US100"]["score"] < 0      # "tech stocks slump" -> bearish
