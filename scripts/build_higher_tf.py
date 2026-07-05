"""Build M30/M45/M90 CSVs from a native M15 base CSV (own bars, not MT5-native).

MT5 has no TIMEFRAME_M45/M90 constants; M45/M90 are exact multiples of M15
(3x, 6x) so they're built here via the existing `aggregate_candles` bucket
logic (same one scripts/sim_realistic.py uses for M5/M15) instead of pulling
from the broker again.

Usage:
    python scripts/build_higher_tf.py data/xauusd_m15_20220426_20260703.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.sim_realistic import aggregate_candles, load_csv

MINUTES = {"m30": 30, "m45": 45, "m90": 90, "h1": 60, "h2": 120, "h4": 240}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: build_higher_tf.py <m15_csv> [more_m15_csvs...]",
              file=sys.stderr)
        return 2
    for path in sys.argv[1:]:
        src = Path(path)
        candles = load_csv([str(src)])
        if not candles:
            print(f"{src}: no candles", file=sys.stderr)
            continue
        stem_parts = src.stem.split("_")  # <sym>_m15_<start>_<end>
        sym, start, end = stem_parts[0], stem_parts[-2], stem_parts[-1]
        for tag, minutes in MINUTES.items():
            out_candles = aggregate_candles(candles, minutes)
            out_path = src.with_name(f"{sym}_{tag}_{start}_{end}.csv")
            with open(out_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ts", "open", "high", "low", "close", "volume"])
                for c in out_candles:
                    w.writerow([c.ts.isoformat(), c.open, c.high, c.low,
                                c.close, c.volume])
            print(f"{src.name}: {len(out_candles)} {tag} bars -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
