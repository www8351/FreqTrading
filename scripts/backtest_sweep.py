"""Honest backtest of the True-Open Sweep-Reversal model (Python port of
pine/True_Open_Sweep_Strategy.pine).

Setup (same logic as the Pine strategy):
    bias      price vs NY True Open (orb/trueopen.py)
    sweep     bar takes the prior HTF (4H) candle's high/low  (ERL liquidity)
    trigger   the bar CLOSES back across the swept level       (CISD reclaim)
    stop      beyond the sweep wick + buffer
    target    fixed Reward:Risk (rr), swept 2..10
    entry     limit (rest at the swept level) OR market (at signal close)

Execution is cost-true (the whole point — TradingView fills are optimistic):
    - half-spread charged on EVERY fill (entry + exit)
    - commission per closed-volume chunk ($/lot round-trip)
    - intrabar SL checked BEFORE take-profit (conservative)
    - positions opened on a bar are not SL/TP-checked until the next bar
    - limit fills earliest on the bar AFTER the signal (no same-bar look-ahead)
    - risk_pct structural sizing (orb/svp/sizing.compute_lot)
    - daily-loss halt at a %% of the day's opening balance (orb/riskguard)

Reuses the proven harness primitives from sim_realistic.py (load_csv,
aggregate_candles, metrics, report) so numbers are comparable to the SVP/ORB
studies. sim_realistic.py itself is untouched.

Usage:
    python scripts/backtest_sweep.py data/xauusd_1m_*.csv
    python scripts/backtest_sweep.py data/xauusd_1m_*.csv \
        --spread 1.10 --rrs 2,3,5,10 --tfs 1,3,5,10,15 --entry both
"""

from __future__ import annotations

import argparse
import glob
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.models import Candle
from orb.riskguard import DailyLossBreaker
from orb.svp import compute_lot
from orb.trueopen import TrueOpenTracker
from scripts.sim_realistic import (
    USD_PER_LOT_PER_DOLLAR,
    aggregate_candles,
    load_csv,
    metrics,
)

LONG, SHORT = 1, -1


# --------------------------------------------------------------------------- #
class HtfErl:
    """Tracks the prior COMPLETED higher-timeframe candle's high/low (the ERL
    liquidity pool). Buckets aligned to midnight UTC like aggregate_candles.
    ``update`` folds the current bar into the developing bucket and returns the
    (high, low) of the last completed bucket — i.e. request.security(high[1])."""

    def __init__(self, minutes: int) -> None:
        self.m = minutes
        self.key: tuple | None = None
        self.hi = self.lo = None
        self.prev_hi = self.prev_lo = None

    def update(self, c: Candle) -> tuple[float | None, float | None]:
        mins = c.ts.hour * 60 + c.ts.minute
        bucket = mins - (mins % self.m)
        key = (c.ts.date(), bucket)
        if self.key is None:
            self.key, self.hi, self.lo = key, c.high, c.low
        elif key == self.key:
            self.hi, self.lo = max(self.hi, c.high), min(self.lo, c.low)
        else:
            self.prev_hi, self.prev_lo = self.hi, self.lo
            self.key, self.hi, self.lo = key, c.high, c.low
        return self.prev_hi, self.prev_lo


@dataclass
class Pos:
    side: int
    entry: float
    sl: float
    tp: float
    risk0: float          # entry-to-stop distance at fill (for the +2R partial)
    vol: float
    open_ts: datetime
    reason: str
    partialed: bool = False
    pnl: float = 0.0


@dataclass
class Pend:
    side: int
    px: float             # limit level (pre-spread)
    sl: float
    placed_ts: datetime
    reason: str


class SweepSim:
    def __init__(self, spread: float, comm: float, value_per_move: float,
                 use_partial: bool) -> None:
        self.half = spread / 2.0
        self.comm = comm
        self.vpm = value_per_move
        self.use_partial = use_partial
        self.equity = 0.0
        self.pos: Pos | None = None
        self.closed: list[dict] = []

    def _close(self, px: float, vol: float, reason: str, ts: datetime) -> None:
        p = self.pos
        gross = p.side * (px - p.entry) * vol * self.vpm
        pnl = gross - self.comm * vol
        p.pnl += pnl
        p.vol = round(p.vol - vol, 8)
        self.equity += pnl
        if p.vol <= 1e-9:
            self.closed.append({
                "dir": "LONG" if p.side == LONG else "SHORT",
                "entry": p.entry, "open_ts": p.open_ts, "signal_ts": p.open_ts,
                "close_ts": ts, "pnl": p.pnl, "reason": p.reason,
            })
            self.pos = None

    def manage_exits(self, c: Candle) -> None:
        """SL first (conservative), then +2R partial, then TP — on this bar's range."""
        p = self.pos
        if p is None:
            return
        if p.side == LONG:
            if c.low <= p.sl:
                self._close(p.sl - self.half, p.vol, "sl", c.ts)
                return
            if self.use_partial and not p.partialed:
                tp2r = p.entry + 2.0 * p.risk0
                if tp2r < p.tp and c.high >= tp2r:
                    self._close(tp2r - self.half, round(p.vol * 0.7, 8),
                                "partial_2r", c.ts)
                    if self.pos is not None:
                        self.pos.partialed = True
            if self.pos is not None and c.high >= p.tp:
                self._close(p.tp - self.half, self.pos.vol, "tp", c.ts)
        else:
            if c.high >= p.sl:
                self._close(p.sl + self.half, p.vol, "sl", c.ts)
                return
            if self.use_partial and not p.partialed:
                tp2r = p.entry - 2.0 * p.risk0
                if tp2r > p.tp and c.low <= tp2r:
                    self._close(tp2r + self.half, round(p.vol * 0.7, 8),
                                "partial_2r", c.ts)
                    if self.pos is not None:
                        self.pos.partialed = True
            if self.pos is not None and c.low <= p.tp:
                self._close(p.tp + self.half, self.pos.vol, "tp", c.ts)

    def flatten(self, c: Candle, reason: str) -> None:
        if self.pos is None:
            return
        px = (c.close - self.half) if self.pos.side == LONG else (c.close + self.half)
        self._close(px, self.pos.vol, reason, c.ts)


# --------------------------------------------------------------------------- #
def run_sweep(candles: list[Candle], *, htf_min: int, rr: float,
              entry_mode: str, use_bias: bool, spread: float, comm: float,
              risk_pct: float, start_balance: float, max_daily_loss_pct: float,
              stop_buf: float, ttl_sec: float, value_per_move: float,
              vol_min: float, vol_step: float, vol_max: float,
              use_partial: bool) -> list[dict]:
    erl = HtfErl(htf_min)
    topen = TrueOpenTracker()
    breaker = DailyLossBreaker(max_daily_loss_pct=max_daily_loss_pct)
    sim = SweepSim(spread, comm, value_per_move, use_partial)
    pend: Pend | None = None

    for c in candles:
        prev_hi, prev_lo = erl.update(c)
        topen.update(c)
        bal = start_balance + sim.equity
        halted = breaker.update(c.ts.date(), bal)

        # 1) exits for an open position (this bar's range)
        sim.manage_exits(c)

        # 2) daily halt: flatten + drop pending
        if halted:
            sim.flatten(c, "daily_halt")
            pend = None

        # 3) limit fill (earliest the bar AFTER it was placed; no exit check now)
        if pend is not None and not halted:
            age = (c.ts - pend.placed_ts).total_seconds()
            if age >= ttl_sec:
                pend = None
            elif age > 0 and sim.pos is None:
                touched = (c.low <= pend.px) if pend.side == LONG \
                    else (c.high >= pend.px)
                if touched:
                    fill = (pend.px + sim.half) if pend.side == LONG \
                        else (pend.px - sim.half)
                    risk = (fill - pend.sl) if pend.side == LONG \
                        else (pend.sl - fill)
                    if risk > 0:
                        remaining = max(0.0, breaker.day_cap + breaker.day_pnl)
                        lot = compute_lot(bal, risk_pct, fill, pend.sl,
                                          value_per_move, vol_min, vol_step,
                                          vol_max, max_risk=remaining)
                        if lot > 0:
                            tp = (fill + rr * risk) if pend.side == LONG \
                                else (fill - rr * risk)
                            sim.pos = Pos(pend.side, fill, pend.sl, tp, risk,
                                          lot, c.ts, pend.reason)
                    pend = None

        # 4) signal detection (flat only); open market now, rest a limit for later
        bias = topen.bias(c.close)
        bias_bull = (not use_bias) or bias == "bullish"
        bias_bear = (not use_bias) or bias == "bearish"
        long_sig = (prev_lo is not None and c.low < prev_lo
                    and c.close > prev_lo and bias_bull)
        short_sig = (prev_hi is not None and c.high > prev_hi
                     and c.close < prev_hi and bias_bear)

        if not halted and sim.pos is None and pend is None and (long_sig or short_sig):
            if long_sig:
                side, level, sl, reason = LONG, prev_lo, c.low - stop_buf, "sweep_low_reclaim"
            else:
                side, level, sl, reason = SHORT, prev_hi, c.high + stop_buf, "sweep_high_reclaim"
            if entry_mode == "market":
                fill = (c.close + sim.half) if side == LONG else (c.close - sim.half)
                risk = (fill - sl) if side == LONG else (sl - fill)
                if risk > 0:
                    remaining = max(0.0, breaker.day_cap + breaker.day_pnl)
                    lot = compute_lot(bal, risk_pct, fill, sl, value_per_move,
                                      vol_min, vol_step, vol_max, max_risk=remaining)
                    if lot > 0:
                        tp = (fill + rr * risk) if side == LONG else (fill - rr * risk)
                        sim.pos = Pos(side, fill, sl, tp, risk, lot, c.ts, reason)
            else:  # limit
                pend = Pend(side, level, sl, c.ts, reason)

    return sim.closed


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="*", default=["data/xauusd_1m_*.csv"])
    ap.add_argument("--htf", type=int, default=240, help="HTF minutes for ERL (default 240=4H)")
    ap.add_argument("--tfs", default="1,3,5,10,15", help="chart timeframes in minutes")
    ap.add_argument("--rrs", default="2,3,5,10", help="reward:risk multiples to sweep")
    ap.add_argument("--entry", choices=("limit", "market", "both"), default="both")
    ap.add_argument("--spread", type=float, default=1.10, help="$ spread (gold honest ~1.10)")
    ap.add_argument("--commission", type=float, default=7.0, help="$/lot round-trip")
    ap.add_argument("--risk-pct", dest="risk_pct", type=float, default=1.0)
    ap.add_argument("--start-balance", dest="start_balance", type=float, default=1000.0)
    ap.add_argument("--max-daily-loss-pct", dest="max_daily_loss_pct", type=float, default=10.0)
    ap.add_argument("--stop-buf-ticks", dest="stop_buf_ticks", type=int, default=50)
    ap.add_argument("--tick-size", dest="tick_size", type=float, default=0.01)
    ap.add_argument("--limit-ttl-min", dest="limit_ttl_min", type=float, default=30.0)
    ap.add_argument("--no-bias", dest="use_bias", action="store_false")
    ap.add_argument("--partial", dest="use_partial", action="store_true",
                    help="70%% partial at +2R (only bites when rr>2)")
    args = ap.parse_args()

    paths = [p for a in args.csvs for p in glob.glob(a)]
    if not paths:
        sys.exit("no CSV data found")
    base = load_csv(paths)
    tfs = [int(x) for x in args.tfs.split(",") if x.strip()]
    rrs = [float(x) for x in args.rrs.split(",") if x.strip()]
    modes = ["limit", "market"] if args.entry == "both" else [args.entry]
    stop_buf = args.stop_buf_ticks * args.tick_size

    print(f"bars(1m)={len(base)} {base[0].ts} .. {base[-1].ts}")
    print(f"costs: spread=${args.spread} commission=${args.commission}/lot RT | "
          f"risk={args.risk_pct}%/trade daily-halt={args.max_daily_loss_pct}% "
          f"start=${args.start_balance:.0f} | HTF={args.htf}m bias={'on' if args.use_bias else 'off'}"
          f" partial={'on' if args.use_partial else 'off'} stop_buf=${stop_buf:.2f}\n")

    agg = {tf: aggregate_candles(base, tf) for tf in tfs}

    for mode in modes:
        print(f"=== entry={mode} ===")
        print(f"{'TF':>4} {'rr':>4} {'n':>5} {'net$':>10} {'net%':>8} "
              f"{'win%':>6} {'PF':>6} {'maxDD%':>7}")
        best = None
        for tf in tfs:
            for rr in rrs:
                trades = run_sweep(
                    agg[tf], htf_min=args.htf, rr=rr, entry_mode=mode,
                    use_bias=args.use_bias, spread=args.spread,
                    comm=args.commission, risk_pct=args.risk_pct,
                    start_balance=args.start_balance,
                    max_daily_loss_pct=args.max_daily_loss_pct,
                    stop_buf=stop_buf, ttl_sec=args.limit_ttl_min * 60.0,
                    value_per_move=USD_PER_LOT_PER_DOLLAR,
                    vol_min=0.01, vol_step=0.01, vol_max=50.0,
                    use_partial=args.use_partial)
                m = metrics(trades, args.start_balance)
                net_pct = m["pnl"] / args.start_balance * 100.0
                pf = m["pf"]
                pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
                print(f"{tf:>3}m {rr:>4.1f} {m['n']:>5} {m['pnl']:>+10.2f} "
                      f"{net_pct:>+7.1f}% {m['win']:>5.1f} {pf_s:>6} {m['dd_pct']:>6.1f}%")
                if m["n"] >= 1 and (best is None or net_pct > best[0]):
                    best = (net_pct, tf, rr, m)
        if best is not None:
            _, tf, rr, m = best
            pf_b = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
            print(f"-> best: {tf}m rr={rr:.1f}  net={best[0]:+.1f}%  "
                  f"n={m['n']} PF={pf_b} maxDD={m['dd_pct']:.1f}%")
        print()


if __name__ == "__main__":
    main()
