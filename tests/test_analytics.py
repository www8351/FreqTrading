"""Tests for orb.analytics — hand-computed fixture, every stat asserted.

Fixture: 6 trades across 3 UTC days (2026-01-05..07), sorted-by-close order:
    T1  open 05 08:05  close 05 08:10  (5m,  <15m)   pnl +100  LONG   h8
    T2  open 05 09:00  close 05 09:30  (30m, 15-60m) pnl  -50  SHORT  h9
    T3  open 05 13:00  close 05 15:00  (2h,  1-4h)   pnl  +30  LONG   h13
    T4  open 06 08:20  close 06 14:20  (6h,  4-24h)  pnl  -80  SHORT  h8
    T5  open 06 09:15  close 07 15:15  (30h, >24h)   pnl +200  LONG   h9
    T6  open 07 10:00  close 07 10:10  (10m, <15m)   pnl  -20  SHORT  h10

Hand math (start_balance=1000):
    net        = 100-50+30-80+200-20            = 180
    gross_win  = 100+30+200                     = 330
    gross_loss = |−50−80−20|                    = 150
    PF         = 330/150                        = 2.2
    trade_win% = 3/6                            = 50.0
    daily nets (close date): 05: +80, 06: -80, 07: +180
    day_win%   = 2/3                            = 66.666...
    avg_win    = 330/3                          = 110.0
    avg_loss   = 150/3                          = 50.0
    payoff     = 110/50                         = 2.2
    expectancy = 180/6                          = 30.0
    equity by CLOSE order (T6 10:10 closes before T5 15:15 on Jan 7!):
        T1,T2,T3,T4,T6,T5: 1000 ->1100 ->1050 ->1080 ->1000 ->980 ->1180
        peak 1100 trough 980 -> max_dd_abs = 120
        max_dd_pct = 120/1100*100               = 10.909...
    recovery   = 180/120                        = 1.5
    largest_day_share = 180/(80+180)            = 0.69230...
    daily_stddev = pstdev([80,-80,180]); mean 60, sq dev 400+19600+14400
                 = sqrt(34400/3)                = 107.08252...
    trading_days = 3; avg_daily_net = 180/3     = 60.0
"""

from datetime import datetime, timezone

import pytest

from orb.analytics import (TradeRecord, by_duration, by_hour, compute_stats,
                           daily_table, format_report, from_sim)


def _dt(day: int, h: int, m: int) -> datetime:
    return datetime(2026, 1, day, h, m, tzinfo=timezone.utc)


@pytest.fixture
def trades() -> list[TradeRecord]:
    return [
        TradeRecord(_dt(5, 8, 5), _dt(5, 8, 10), 100.0, "LONG", "XAUUSD", 1.0, "tp"),
        TradeRecord(_dt(5, 9, 0), _dt(5, 9, 30), -50.0, "SHORT", "XAUUSD", 1.0, "sl"),
        TradeRecord(_dt(5, 13, 0), _dt(5, 15, 0), 30.0, "LONG", "XAUUSD", 1.0, "tp"),
        TradeRecord(_dt(6, 8, 20), _dt(6, 14, 20), -80.0, "SHORT", "XAUUSD", 1.0, "sl"),
        TradeRecord(_dt(6, 9, 15), _dt(7, 15, 15), 200.0, "LONG", "XAUUSD", 1.0, "tp"),
        TradeRecord(_dt(7, 10, 0), _dt(7, 10, 10), -20.0, "SHORT", "XAUUSD", 1.0, "sl"),
    ]


# --------------------------------------------------------------------------- #
class TestComputeStats:
    def test_all_stats(self, trades):
        s = compute_stats(trades, start_balance=1000.0)
        assert s["n"] == 6
        assert s["net"] == pytest.approx(180.0)
        assert s["gross_win"] == pytest.approx(330.0)
        assert s["gross_loss"] == pytest.approx(150.0)
        assert s["profit_factor"] == pytest.approx(2.2)
        assert s["trade_win_pct"] == pytest.approx(50.0)
        assert s["day_win_pct"] == pytest.approx(200.0 / 3.0)
        assert s["avg_win"] == pytest.approx(110.0)
        assert s["avg_loss"] == pytest.approx(50.0)
        assert s["payoff_ratio"] == pytest.approx(2.2)
        assert s["expectancy"] == pytest.approx(30.0)
        assert s["max_dd_abs"] == pytest.approx(120.0)
        assert s["max_dd_pct"] == pytest.approx(120.0 / 1100.0 * 100.0)
        assert s["recovery_factor"] == pytest.approx(1.5)
        assert s["largest_day_share"] == pytest.approx(180.0 / 260.0)
        # pstdev([80,-80,180]) = sqrt(34400/3)
        assert s["daily_stddev"] == pytest.approx((34400.0 / 3.0) ** 0.5)
        assert s["trading_days"] == 3
        assert s["avg_daily_net"] == pytest.approx(60.0)

    def test_unsorted_input_same_dd(self, trades):
        # compute_stats must order by close_ts internally
        s = compute_stats(list(reversed(trades)), start_balance=1000.0)
        assert s["max_dd_abs"] == pytest.approx(120.0)

    def test_no_losses_pf_none(self):
        t = [TradeRecord(_dt(5, 8, 0), _dt(5, 9, 0), 10.0)]
        s = compute_stats(t, start_balance=100.0)
        assert s["profit_factor"] is None      # inf-safe: None when no losses
        assert s["avg_loss"] is None
        assert s["payoff_ratio"] is None
        assert s["recovery_factor"] is None    # dd == 0
        assert s["max_dd_abs"] == 0.0

    def test_no_wins(self):
        t = [TradeRecord(_dt(5, 8, 0), _dt(5, 9, 0), -10.0)]
        s = compute_stats(t, start_balance=100.0)
        assert s["profit_factor"] == 0.0
        assert s["avg_win"] is None
        assert s["payoff_ratio"] is None
        assert s["largest_day_share"] is None  # no positive days

    def test_empty(self):
        s = compute_stats([], start_balance=1000.0)
        assert s["n"] == 0
        assert s["net"] == 0.0
        assert s["gross_win"] == 0.0
        assert s["gross_loss"] == 0.0
        assert s["profit_factor"] is None
        assert s["trade_win_pct"] == 0.0
        assert s["day_win_pct"] == 0.0
        assert s["avg_win"] is None
        assert s["avg_loss"] is None
        assert s["payoff_ratio"] is None
        assert s["expectancy"] == 0.0
        assert s["max_dd_abs"] == 0.0
        assert s["max_dd_pct"] == 0.0
        assert s["recovery_factor"] is None
        assert s["largest_day_share"] is None
        assert s["daily_stddev"] == 0.0
        assert s["trading_days"] == 0
        assert s["avg_daily_net"] == 0.0


# --------------------------------------------------------------------------- #
class TestDailyTable:
    def test_rows(self, trades):
        rows = daily_table(trades)
        assert [r["date"] for r in rows] == ["2026-01-05", "2026-01-06",
                                             "2026-01-07"]
        assert [r["n"] for r in rows] == [3, 1, 2]
        assert [r["net"] for r in rows] == [pytest.approx(80.0),
                                            pytest.approx(-80.0),
                                            pytest.approx(180.0)]
        assert [r["cum"] for r in rows] == [pytest.approx(80.0),
                                            pytest.approx(0.0),
                                            pytest.approx(180.0)]

    def test_empty(self):
        assert daily_table([]) == []


class TestByHour:
    def test_buckets(self, trades):
        rows = by_hour(trades)
        assert [r["hour"] for r in rows] == [8, 9, 10, 13]
        h = {r["hour"]: r for r in rows}
        # h8: T1(+100), T4(-80)
        assert h[8]["n"] == 2
        assert h[8]["net"] == pytest.approx(20.0)
        assert h[8]["win_pct"] == pytest.approx(50.0)
        assert h[8]["profit_factor"] == pytest.approx(100.0 / 80.0)
        # h9: T2(-50), T5(+200)
        assert h[9]["n"] == 2
        assert h[9]["net"] == pytest.approx(150.0)
        assert h[9]["win_pct"] == pytest.approx(50.0)
        assert h[9]["profit_factor"] == pytest.approx(4.0)
        # h10: T6(-20) — loss only
        assert h[10]["n"] == 1
        assert h[10]["net"] == pytest.approx(-20.0)
        assert h[10]["win_pct"] == pytest.approx(0.0)
        assert h[10]["profit_factor"] == 0.0
        # h13: T3(+30) — win only -> PF undefined -> None
        assert h[13]["n"] == 1
        assert h[13]["net"] == pytest.approx(30.0)
        assert h[13]["win_pct"] == pytest.approx(100.0)
        assert h[13]["profit_factor"] is None

    def test_empty(self):
        assert by_hour([]) == []


class TestByDuration:
    def test_buckets(self, trades):
        rows = by_duration(trades)
        assert [r["bucket"] for r in rows] == ["<15m", "15-60m", "1-4h",
                                               "4-24h", ">24h"]
        b = {r["bucket"]: r for r in rows}
        assert b["<15m"]["n"] == 2                       # T1, T6
        assert b["<15m"]["net"] == pytest.approx(80.0)
        assert b["<15m"]["win_pct"] == pytest.approx(50.0)
        assert b["15-60m"]["n"] == 1                     # T2
        assert b["15-60m"]["net"] == pytest.approx(-50.0)
        assert b["15-60m"]["win_pct"] == pytest.approx(0.0)
        assert b["1-4h"]["n"] == 1                       # T3
        assert b["1-4h"]["net"] == pytest.approx(30.0)
        assert b["1-4h"]["win_pct"] == pytest.approx(100.0)
        assert b["4-24h"]["n"] == 1                      # T4
        assert b["4-24h"]["net"] == pytest.approx(-80.0)
        assert b["4-24h"]["win_pct"] == pytest.approx(0.0)
        assert b[">24h"]["n"] == 1                       # T5 (30h)
        assert b[">24h"]["net"] == pytest.approx(200.0)
        assert b[">24h"]["win_pct"] == pytest.approx(100.0)

    def test_only_nonempty(self):
        t = [TradeRecord(_dt(5, 8, 0), _dt(5, 8, 5), 1.0)]
        rows = by_duration(t)
        assert [r["bucket"] for r in rows] == ["<15m"]

    def test_empty(self):
        assert by_duration([]) == []


# --------------------------------------------------------------------------- #
class TestFromSim:
    def test_maps_sim_closed_dicts(self):
        # Shaped exactly like Sim._close appends (scripts/sim_realistic.py):
        # ticket/dir/entry/open_ts/signal_ts/close_ts/pnl/fills + **tags
        closed = [
            {   # ORB path: tags = zone/bias/day_q/m90_q/fair/fair90
                "ticket": 1, "dir": "LONG", "entry": 3350.0,
                "open_ts": _dt(5, 8, 5), "signal_ts": _dt(5, 8, 4),
                "close_ts": _dt(5, 9, 0), "pnl": 12.5,
                "fills": [("partial_2r", 3354.0, 0.7, 10.0),
                          ("sl", 3352.0, 0.3, 2.5)],
                "zone": "premium", "bias": 1, "day_q": "q2", "m90_q": "q1",
                "fair": "above", "fair90": "inside",
            },
            {   # SVP path: tags = {"dir": ..., "reason": ...}
                "ticket": 2, "dir": "SHORT", "entry": 3360.0,
                "open_ts": _dt(5, 10, 0), "signal_ts": _dt(5, 10, 0),
                "close_ts": _dt(5, 10, 30), "pnl": -7.0,
                "fills": [("sl", 3362.0, 0.5, -7.0)],
                "reason": "edge_rotation_short",
            },
        ]
        recs = from_sim(closed)
        assert len(recs) == 2
        r1, r2 = recs
        assert r1.open_ts == _dt(5, 8, 5)
        assert r1.close_ts == _dt(5, 9, 0)
        assert r1.pnl == pytest.approx(12.5)
        assert r1.direction == "LONG"
        assert r1.volume == pytest.approx(1.0)   # sum of fill volumes
        assert r1.reason == "sl"                 # final fill reason
        assert r1.symbol == ""                   # not in sim dicts
        assert r2.direction == "SHORT"
        assert r2.volume == pytest.approx(0.5)
        assert r2.reason == "sl"
        assert r2.pnl == pytest.approx(-7.0)

    def test_tolerates_missing_optional_keys(self):
        closed = [{"open_ts": _dt(5, 8, 0), "close_ts": _dt(5, 9, 0),
                   "pnl": 3.0}]
        recs = from_sim(closed)
        assert len(recs) == 1
        assert recs[0].direction == ""
        assert recs[0].volume == 0.0
        assert recs[0].reason == ""

    def test_skips_records_missing_required_keys(self):
        # fail-safe: unusable record -> dropped, never raise
        closed = [{"pnl": 1.0}, {"open_ts": _dt(5, 8, 0)}]
        assert from_sim(closed) == []

    def test_empty(self):
        assert from_sim([]) == []


# --------------------------------------------------------------------------- #
class TestFormatReport:
    def test_contains_stats_and_tables(self, trades):
        out = format_report(trades, start_balance=1000.0, title="XAUUSD ORB")
        assert "XAUUSD ORB" in out
        assert "180.00" in out          # net
        assert "2.20" in out            # profit factor
        assert "2026-01-05" in out      # daily table
        assert "<15m" in out            # duration table
        assert "hour" in out.lower()    # hour table header

    def test_empty(self):
        out = format_report([], start_balance=1000.0)
        assert "no trades" in out.lower()

    def test_frozen_record(self):
        r = TradeRecord(_dt(5, 8, 0), _dt(5, 9, 0), 1.0)
        with pytest.raises(Exception):
            r.pnl = 2.0  # type: ignore[misc]
