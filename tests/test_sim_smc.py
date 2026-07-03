"""SMC harness parity smoke: run_smc returns Sim.closed dicts, analytics can
consume them, and the cli argparser accepts --strategy smc. Fast + deterministic
(synthetic 1m fixture, no I/O)."""

from datetime import datetime, timedelta, timezone

import scripts.sim_realistic as sr
from orb import analytics
from orb.models import Candle


def _trending_candles(n: int = 400) -> list[Candle]:
    """Deterministic synthetic 1m stream: a slow uptrend with intrabar range,
    enough bars to build the M15/H4/D1 aggregates the SmcEngine needs."""
    t0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    out: list[Candle] = []
    px = 2000.0
    for i in range(n):
        # gentle drift + a deterministic wiggle so highs/lows differ from OC
        drift = 0.15 if (i // 30) % 2 == 0 else -0.05
        o = px
        c = px + drift
        hi = max(o, c) + 0.30
        lo = min(o, c) - 0.30
        out.append(Candle(ts=t0 + timedelta(minutes=i), open=o, high=hi,
                          low=lo, close=c, volume=100.0 + (i % 7)))
        px = c
    return out


def test_run_smc_returns_closed_dicts_without_raising():
    candles = _trending_candles()
    closed = sr.run_smc(candles, spread=1.10, comm=7.0, start_balance=1000.0)
    assert isinstance(closed, list)
    for t in closed:
        assert "pnl" in t and "open_ts" in t and "close_ts" in t
        assert "dir" in t and "fills" in t


def test_run_smc_feeds_analytics():
    candles = _trending_candles()
    closed = sr.run_smc(candles, spread=1.10, comm=7.0, start_balance=1000.0)
    recs = analytics.from_sim(closed)
    stats = analytics.compute_stats(recs, start_balance=1000.0)
    assert stats["n"] == len(recs)
    # format_report must not raise on the produced records
    analytics.format_report(recs, start_balance=1000.0, title="SMC XAUUSD")


def test_run_smc_accepts_overrides():
    candles = _trending_candles()
    # a filtered smc_ov kwarg (valid SmcConfig field) must flow through cleanly
    closed = sr.run_smc(candles, spread=1.10, comm=7.0, start_balance=1000.0,
                        min_confluences=2, max_trades_per_day=3)
    assert isinstance(closed, list)


def test_argparser_accepts_strategy_smc():
    ap = sr.build_argparser()
    ns = ap.parse_args(["data/x.csv", "--strategy", "smc"])
    assert ns.strategy == "smc"


def test_argparser_rejects_unknown_strategy():
    ap = sr.build_argparser()
    import pytest
    with pytest.raises(SystemExit):
        ap.parse_args(["data/x.csv", "--strategy", "nope"])
