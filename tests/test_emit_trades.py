"""Wiring test: sim_realistic trade dicts -> trades JSON -> backtest_macro gate."""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import backtest_macro            # noqa: E402
import sim_realistic             # noqa: E402
from macro.backtest import compare              # noqa: E402
from macro.collectors import forexfactory       # noqa: E402

UTC = timezone.utc


def test_trades_to_records_uses_signal_ts():
    # macro vetoes at placement (signal) time, not fill time -> use signal_ts
    sig = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    fill = datetime(2026, 6, 17, 12, 5, tzinfo=UTC)
    recs = sim_realistic.trades_to_records(
        [{"signal_ts": sig, "open_ts": fill, "dir": "LONG", "pnl": 12.345}], "XAUUSD")
    assert recs == [{"ts": "2026-06-17T12:00:00Z", "symbol": "XAUUSD",
                     "direction": "LONG", "pnl": 12.35}]


def test_trades_to_records_falls_back_to_open_ts():
    dt = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)
    recs = sim_realistic.trades_to_records(
        [{"signal_ts": None, "open_ts": dt, "dir": "SHORT", "pnl": -3.0}], "US100")
    assert recs[0]["ts"] == "2026-06-17T12:00:00Z" and recs[0]["symbol"] == "US100"


def test_emit_roundtrip_into_gate(tmp_path):
    sig = datetime(2026, 6, 17, 12, 30, tzinfo=UTC)        # inside CPI blackout
    p = tmp_path / "tr.json"
    sim_realistic.write_trades_json(
        sim_realistic.trades_to_records(
            [{"signal_ts": sig, "open_ts": sig, "dir": "LONG", "pnl": 10.0}], "XAUUSD"),
        str(p))
    trades = backtest_macro.load_trades(str(p))
    events = forexfactory.parse_calendar([
        {"title": "CPI m/m", "country": "USD", "date": "2026-06-17T12:30:00Z",
         "impact": "High", "forecast": "0.2%", "actual": "0.6%"}])
    res = compare(trades, events)
    assert res["dropped"] == 1                              # blackout veto round-trips
