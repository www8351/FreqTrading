#!/usr/bin/env python
"""M6 macro backtest gate — profit factor BEFORE vs AFTER the macro filter, per symbol.

Usage:
  python scripts/backtest_macro.py --trades trades.json --events calendar.json

trades : JSON list of {"ts":ISO, "symbol":.., "direction":"LONG|SHORT", "pnl":float}
         (or a CSV with header  ts,symbol,direction,pnl). Produce it from your
         baseline run (e.g. dump entries+realized pnl from scripts/sim_realistic.py).
events : JSON list of ForexFactory-style rows (title,country,date,impact,forecast,
         actual,previous) — your historical economic calendar over the test window.

This is the gate: only enable --macro-mode filter/guard on live bots once the
filtered PF holds up (and use it to calibrate macro/sensitivity.py + M3 thresholds).
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from macro.backtest import Trade, compare          # noqa: E402
from macro.collectors import forexfactory          # noqa: E402


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_trades(path: str) -> list[Trade]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f)) if path.endswith(".csv") else json.load(f)
    return [Trade(ts=_parse_ts(r["ts"]), symbol=str(r["symbol"]),
                  direction=str(r["direction"]), pnl=float(r["pnl"])) for r in rows]


def load_events(path: str):
    with open(path, encoding="utf-8") as f:
        return forexfactory.parse_calendar(json.load(f))


def _fmt(s: dict) -> str:
    pf = "inf" if (s["pf"] is None and s["n"]) else (s["pf"] if s["pf"] is not None else "-")
    return f"n={s['n']:<5} net={s['net']:<10} pf={pf!s:<7} win={s['winrate']}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="M6 macro backtest gate")
    p.add_argument("--trades", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--conf-min", dest="conf_min", type=float, default=0.6)
    p.add_argument("--default-stale", dest="default_stale",
                   choices=("allow", "block"), default="allow")
    p.add_argument("--pre-min", dest="pre_min", type=int, default=30)
    p.add_argument("--post-min", dest="post_min", type=int, default=30)
    a = p.parse_args(argv)

    trades, events = load_trades(a.trades), load_events(a.events)
    res = compare(trades, events, conf_min=a.conf_min,
                  default_when_stale=a.default_stale,
                  build_kw={"pre_min": a.pre_min, "post_min": a.post_min})

    print(f"# macro backtest: {len(trades)} trades, {len(events)} events, "
          f"conf_min={a.conf_min}, dropped={res['dropped']}")
    print(f"BASELINE  {_fmt(res['baseline'])}")
    print(f"FILTERED  {_fmt(res['filtered'])}")
    print("# per symbol:")
    for sym, d in res["by_symbol"].items():
        print(f"  {sym:<8} dropped={d['dropped']}")
        print(f"    base {_fmt(d['baseline'])}")
        print(f"    filt {_fmt(d['filtered'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
