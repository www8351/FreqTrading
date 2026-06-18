"""Bulk M1 history pull from the local MT5 terminal -> UTC CSVs.

Read-only: copy_rates only, no orders. Broker bar times are server-local
(JustMarkets = UTC+3 in DST / June); subtract the offset to emit true-UTC
timestamps matching the existing data/*.csv (engine converts UTC -> NY).

Usage:
    python scripts/fetch_mt5_history.py --bars 200000 --tz-offset-hours 3
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SYMBOLS = ["XAUUSD.ecn", "US100.ecn", "US500.ecn", "XAGUSD.ecn"]
TIMEFRAME_M1 = 1  # mt5.TIMEFRAME_M1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*", default=SYMBOLS)
    ap.add_argument("--bars", type=int, default=200000)
    ap.add_argument("--tz-offset-hours", type=float, default=3.0,
                    help="broker server TZ vs UTC (JustMarkets June = +3)")
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()

    try:
        import MetaTrader5 as mt5  # noqa: N816
    except ImportError:
        print("MetaTrader5 not importable", file=sys.stderr)
        return 2
    if not mt5.initialize():
        print(f"mt5.initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 3

    offset = timedelta(hours=args.tz_offset_hours)
    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)
    rc = 0
    try:
        for sym in args.symbols:
            if not mt5.symbol_select(sym, True):
                print(f"{sym}: symbol_select failed {mt5.last_error()}",
                      file=sys.stderr)
                rc = 1
                continue
            # copy_rates_from_pos caps ~50k bars/call -> paginate via start_pos
            chunk = 50000
            by_time: dict[int, tuple] = {}
            start = 0
            while len(by_time) < args.bars:
                r = mt5.copy_rates_from_pos(sym, TIMEFRAME_M1, start, chunk)
                if r is None or len(r) == 0:
                    break
                for row in r:
                    by_time[int(row["time"])] = row
                if len(r) < chunk:
                    break
                start += chunk
            if not by_time:
                print(f"{sym}: no rates {mt5.last_error()}", file=sys.stderr)
                rc = 1
                continue
            rates = [by_time[t] for t in sorted(by_time)]
            rows = []
            for r in rates:
                ts = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc) - offset
                ts = ts.replace(tzinfo=timezone.utc)
                rows.append((ts, r["open"], r["high"], r["low"], r["close"],
                             float(r["tick_volume"])))
            start = rows[0][0].strftime("%Y%m%d")
            end = rows[-1][0].strftime("%Y%m%d")
            fname = outdir / f"{sym.split('.')[0].lower()}_1m_{start}_{end}.csv"
            with open(fname, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ts", "open", "high", "low", "close", "volume"])
                for ts, o, h, low, c, v in rows:
                    w.writerow([ts.isoformat(), o, h, low, c, v])
            print(f"{sym}: {len(rows)} bars {rows[0][0]} .. {rows[-1][0]} "
                  f"-> {fname}")
    finally:
        mt5.shutdown()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
