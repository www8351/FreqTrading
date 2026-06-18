"""Backtest: does the True Open bias/zone filter improve the ORB strategy?

Replays the ORB engine over historical XAU/USD 1m candles (live-like config),
tags every ENTRY with the TrueOpenTracker state at that bar, then compares
PnL of the baseline against entry-filter variants:

    baseline      all engine entries
    bias          long only when bias=bullish, short only when bias=bearish
    zone          long only in discount, short only in premium
    bias+zone     both filters

PnL model: virtual fills at signal prices, $100 per $1 move per 1.0 lot
(XAUUSD contract = 100 oz). Partial TP exits honoured via sig.qty.

Usage:
    python scripts/backtest_trueopen.py data/*.csv
    python scripts/backtest_trueopen.py --fetch 5   # fetch N 5000-bar chunks
"""

from __future__ import annotations

import csv
import glob
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.cli import load_dotenv
from orb.engine import OrbEngine
from orb.models import Candle, Direction, OrbConfig, SignalKind
from orb.trueopen import TrueOpenTracker

QTY = 0.05
USD_PER_LOT_PER_DOLLAR = 100.0  # XAUUSD: 1 lot = 100 oz


def load_csv(path: str) -> list[Candle]:
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Candle(
                ts=datetime.fromisoformat(row["ts"]).replace(tzinfo=timezone.utc)
                if "+" not in row["ts"] and "Z" not in row["ts"]
                else datetime.fromisoformat(row["ts"].replace("Z", "+00:00")),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            ))
    return out


def fetch_chunks(n: int) -> list[Candle]:
    """Fetch n back-to-back 5000-bar chunks from Twelve Data (newest last)."""
    import time as _t
    from orb.feeds.twelvedata import fetch_candles
    load_dotenv()
    candles: list[Candle] = []
    end: datetime | None = None
    for i in range(n):
        kw = {"end_date": end.strftime("%Y-%m-%d %H:%M:%S")} if end else {}
        chunk = fetch_candles(outputsize=5000, **kw)
        print(f"chunk {i + 1}/{n}: {len(chunk)} bars "
              f"{chunk[0].ts} .. {chunk[-1].ts}", file=sys.stderr)
        candles = chunk + candles
        end = chunk[0].ts - timedelta(minutes=1)
        if i + 1 < n:
            _t.sleep(9)  # free tier: 8 req/min
    # dedup on ts, ascending
    seen, out = set(), []
    for c in candles:
        if c.ts not in seen:
            seen.add(c.ts)
            out.append(c)
    out.sort(key=lambda c: c.ts)
    p = Path("data")
    p.mkdir(exist_ok=True)
    fn = p / f"xauusd_1m_{out[0].ts:%Y%m%d}_{out[-1].ts:%Y%m%d}.csv"
    with open(fn, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in out:
            w.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])
    print(f"saved {len(out)} bars -> {fn}", file=sys.stderr)
    return out


def run_engine(candles: list[Candle]) -> list[dict]:
    """Replay live-like config; return closed trades with entry tags."""
    cfg = OrbConfig(
        session_open_utc=candles[0].ts.time().replace(second=0, microsecond=0),
        session_len_min=1440,
        roc_min=0.15,
        stop_max_dist=4.0,
        stop_min_dist=2.0,
        tp_rrr=2.0,
        tp_close_frac=0.7,
        qty=QTY,
        one_trade_per_session=False,
        rearm_after_exit=True,
        rearm_range="rebuild",
    )
    engine = OrbEngine(cfg)
    tracker = TrueOpenTracker()

    trades: list[dict] = []
    open_trade: dict | None = None
    for c in candles:
        tracker.update(c)
        sig = engine.on_candle(c)
        if sig is None:
            continue
        if sig.kind is SignalKind.ENTRY:
            open_trade = {
                "ts": sig.ts, "dir": sig.direction, "entry": sig.price,
                "qty": QTY, "remaining": QTY, "pnl": 0.0,
                "bias": tracker.bias(c.close), "zone": tracker.zone(c.close),
                "exits": [],
            }
        elif sig.kind is SignalKind.EXIT and open_trade is not None:
            sign = 1.0 if open_trade["dir"] is Direction.LONG else -1.0
            vol = sig.qty if (sig.qty and sig.qty < open_trade["remaining"]) \
                else open_trade["remaining"]
            pnl = sign * (sig.price - open_trade["entry"]) * vol \
                * USD_PER_LOT_PER_DOLLAR
            open_trade["pnl"] += pnl
            open_trade["remaining"] -= vol
            open_trade["exits"].append((sig.reason, sig.price, vol, pnl))
            if open_trade["remaining"] <= 1e-9:
                trades.append(open_trade)
                open_trade = None
    return trades


def keep(t: dict, bias: bool, zone: bool) -> bool:
    if bias:
        want = "bullish" if t["dir"] is Direction.LONG else "bearish"
        if t["bias"] != want:
            return False
    if zone:
        want = "discount" if t["dir"] is Direction.LONG else "premium"
        if t["zone"] != want:
            return False
    return True


def report(name: str, trades: list[dict]) -> None:
    n = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    wr = 100.0 * len(wins) / n if n else 0.0
    avg = pnl / n if n else 0.0
    gw = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    pf = (gw / -gl) if gl < 0 else float("inf")
    print(f"{name:<12} trades={n:<4} pnl=${pnl:+9.2f} win%={wr:5.1f} "
          f"avg=${avg:+7.2f} PF={pf:5.2f}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--fetch":
        candles = fetch_chunks(int(args[1]) if len(args) > 1 else 4)
    else:
        paths = [p for a in (args or ["data/*.csv"]) for p in glob.glob(a)]
        if not paths:
            sys.exit("no CSVs found; run with --fetch N first")
        candles = []
        for p in sorted(paths):
            candles.extend(load_csv(p))
        candles.sort(key=lambda c: c.ts)
        # dedup
        ded, seen = [], set()
        for c in candles:
            if c.ts not in seen:
                seen.add(c.ts)
                ded.append(c)
        candles = ded

    print(f"bars={len(candles)} {candles[0].ts} .. {candles[-1].ts}\n")
    trades = run_engine(candles)

    tagged = [t for t in trades if t["bias"] is not None]
    print(f"closed trades={len(trades)} (bias-taggable={len(tagged)})\n")
    report("baseline", trades)
    report("bias", [t for t in trades if keep(t, True, False)])
    report("zone", [t for t in trades if keep(t, False, True)])
    report("bias+zone", [t for t in trades if keep(t, True, True)])

    print("\nby bias at entry:")
    for b in ("bullish", "bearish", None):
        sub = [t for t in trades if t["bias"] == b]
        if sub:
            report(f"  {b}", sub)
    print("by zone at entry:")
    for z in ("premium", "discount", "dead_zone", None):
        sub = [t for t in trades if t["zone"] == z]
        if sub:
            report(f"  {z}", sub)
    print("by zone x direction:")
    for z in ("premium", "discount", "dead_zone"):
        for d in (Direction.LONG, Direction.SHORT):
            sub = [t for t in trades if t["zone"] == z and t["dir"] is d]
            if sub:
                report(f"  {z}/{d.name}", sub)
    print("by bias x direction:")
    for b in ("bullish", "bearish"):
        for d in (Direction.LONG, Direction.SHORT):
            sub = [t for t in trades if t["bias"] == b and t["dir"] is d]
            if sub:
                report(f"  {b}/{d.name}", sub)


if __name__ == "__main__":
    main()
