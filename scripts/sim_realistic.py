"""Execution-true backtest: mirrors the LIVE limit-mode pipeline.

Models what scripts/backtest_trueopen.py ignores:
    - limit entries at the liquidity level + ONE addon at 80% toward shared SL
    - intrabar fills (limit touch) and intrabar SL hits via high/low,
      SL checked BEFORE profit on the same bar (conservative)
    - babysitter exits (reuses orb/babysitter.py verbatim): partial at +2R,
      stop chases the remainder at distance d, tighten-only
    - pending TTL (30m), spike-cancel (>=2min age), daily $110 breaker
    - spread + commission on every fill

Study dimensions: True Open zone (trueopen.py), Quarters Theory (quarters.py).

Usage:
    python scripts/sim_realistic.py data/xauusd_1m_20260321_20260612.csv
    flags: --spread 0.25 --commission 7.0 --qty 0.05
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb.babysitter import LONG, SHORT, Babysitter
from orb.engine import OrbEngine
from orb.models import Candle, Direction, OrbConfig, SignalKind
from orb.quarters import QuarterTracker, quarters
from orb.riskguard import DailyLossBreaker, SpikeCancel
from orb.trueopen import TrueOpenTracker

USD_PER_LOT_PER_DOLLAR = 100.0  # XAUUSD: 1 lot = 100 oz
ADDON_FRAC = 0.8
LIMIT_TTL_SEC = 30 * 60
SPIKE_MIN_AGE_SEC = 120


# --------------------------------------------------------------------------- #
@dataclass
class Pending:
    ticket: int
    side: int                 # LONG / SHORT (babysitter convention)
    px: float
    sl: float
    volume: float
    placed_ts: datetime
    label: str                # "entry" | "addon"
    tags: dict


@dataclass
class Position:
    ticket: int
    type: int                 # LONG / SHORT
    volume: float
    price_open: float
    sl: float
    open_ts: datetime
    tags: dict
    placed_ts: datetime | None = None   # signal/placement ts (when macro would veto)
    fills: list = field(default_factory=list)   # (reason, px, vol, pnl$)
    pnl: float = 0.0


class Sim:
    """Broker+market simulator for one candle stream."""

    def __init__(self, qty: float, spread: float, commission_rt: float,
                 value_per_move: float = USD_PER_LOT_PER_DOLLAR):
        self.qty = qty
        self.half_spread = spread / 2.0
        self.comm = commission_rt      # $ per 1.0 lot round-trip
        self.value_per_move = value_per_move  # $ per 1.0 price move per lot
        self.pendings: list[Pending] = []
        self.positions: list[Position] = []
        self.closed: list[dict] = []
        self._ticket = 0
        self.equity = 0.0
        self.curve: list[tuple[datetime, float]] = []

    # -- order placement (mirrors Mt5Broker._open_limit) ------------------- #
    def place_limit_set(self, sig, ts: datetime, tags: dict) -> None:
        short = sig.direction is Direction.SHORT
        d = abs(sig.price - sig.stop)
        if d <= 0:
            return
        l1 = sig.price + d if short else sig.price - d
        sl = l1 + d if short else l1 - d
        l2 = l1 + ADDON_FRAC * d if short else l1 - ADDON_FRAC * d
        side = SHORT if short else LONG
        for label, px in (("entry", l1), ("addon", l2)):
            self._ticket += 1
            self.pendings.append(Pending(self._ticket, side, px, sl,
                                         self.qty, ts, label, dict(tags)))

    # -- per-bar market mechanics ------------------------------------------ #
    def on_bar(self, c: Candle, sitter: Babysitter, spike: bool,
               halted: bool) -> None:
        # 1) TTL + spike-cancel on pendings
        keep = []
        for p in self.pendings:
            age = (c.ts - p.placed_ts).total_seconds()
            if age >= LIMIT_TTL_SEC:
                continue
            if spike and age >= SPIKE_MIN_AGE_SEC:
                continue
            keep.append(p)
        self.pendings = keep

        # 2) limit fills: bar touches the level (half-spread cost on fill)
        still = []
        for p in self.pendings:
            touched = (c.low <= p.px) if p.side == LONG else (c.high >= p.px)
            if touched:
                fill = p.px + self.half_spread if p.side == LONG \
                    else p.px - self.half_spread
                self.positions.append(Position(p.ticket, p.side, p.volume,
                                               fill, p.sl, c.ts, p.tags,
                                               placed_ts=p.placed_ts))
            else:
                still.append(p)
        self.pendings = still

        # 3) intrabar SL — checked before any profit-taking (conservative)
        for pos in list(self.positions):
            hit = (c.low <= pos.sl) if pos.type == LONG else (c.high >= pos.sl)
            if hit:
                self._close(pos, pos.sl, pos.volume, "sl", c.ts)

        # 4) babysitter on the close
        for act in sitter.on_bar(self.positions, c.close):
            pos = next((p for p in self.positions if p.ticket == act.ticket),
                       None)
            if pos is None:
                continue
            if act.kind == "partial_close":
                px = c.close - self.half_spread if pos.type == LONG \
                    else c.close + self.half_spread
                self._close(pos, px, min(act.volume, pos.volume),
                            "partial_2r", c.ts)
            else:
                tighten = (act.sl > pos.sl) if pos.type == LONG \
                    else (act.sl < pos.sl)
                if pos.sl == 0.0 or tighten:
                    pos.sl = act.sl

        # 5) daily-loss halt: flatten + pull pendings
        if halted:
            for pos in list(self.positions):
                px = c.close - self.half_spread if pos.type == LONG \
                    else c.close + self.half_spread
                self._close(pos, px, pos.volume, "daily_halt", c.ts)
            self.pendings.clear()

        self.curve.append((c.ts, self.equity))

    def _close(self, pos: Position, px: float, vol: float, reason: str,
               ts: datetime) -> None:
        sign = 1.0 if pos.type == LONG else -1.0
        gross = sign * (px - pos.price_open) * vol * self.value_per_move
        cost = self.comm * vol
        pnl = gross - cost
        pos.pnl += pnl
        pos.volume = round(pos.volume - vol, 8)
        pos.fills.append((reason, px, vol, pnl))
        self.equity += pnl
        if pos.volume <= 1e-9:
            self.closed.append({
                "ticket": pos.ticket,
                "dir": "LONG" if pos.type == LONG else "SHORT",
                "entry": pos.price_open, "open_ts": pos.open_ts,
                "signal_ts": pos.placed_ts,
                "close_ts": ts, "pnl": pos.pnl, "fills": pos.fills,
                **pos.tags,
            })
            self.positions.remove(pos)


# --------------------------------------------------------------------------- #
def load_csv(paths: list[str]) -> list[Candle]:
    rows: dict[datetime, Candle] = {}
    for path in paths:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                ts = datetime.fromisoformat(row["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                rows[ts] = Candle(ts=ts, open=float(row["open"]),
                                  high=float(row["high"]), low=float(row["low"]),
                                  close=float(row["close"]),
                                  volume=float(row.get("volume") or 0.0))
    return [rows[k] for k in sorted(rows)]


def run(candles: list[Candle], qty: float, spread: float, comm: float,
        max_daily_loss: float = 110.0, stop_min: float = 2.0,
        stop_max: float = 4.0, value_per_move: float = USD_PER_LOT_PER_DOLLAR
        ) -> list[dict]:
    cfg = OrbConfig(
        session_open_utc=candles[0].ts.time().replace(second=0, microsecond=0),
        session_len_min=1440, roc_min=0.15,
        stop_max_dist=stop_max, stop_min_dist=stop_min,
        tp_rrr=2.0, tp_close_frac=0.7, qty=qty,
        one_trade_per_session=False, rearm_after_exit=True,
        rearm_range="rebuild",
    )
    engine = OrbEngine(cfg)
    sim = Sim(qty, spread, comm, value_per_move)
    sitter = Babysitter(partial_frac=0.7, partial_at_r=2.0)
    spike = SpikeCancel(ratio=2.5)
    breaker = DailyLossBreaker(max_daily_loss)
    topen = TrueOpenTracker()
    qtr = QuarterTracker()

    day_start_equity = {}
    for c in candles:
        topen.update(c)
        qtr.update(c)
        sig = engine.on_candle(c)
        if sig is not None and sig.kind is SignalKind.ENTRY and sig.stop:
            if not breaker.halted:
                q = quarters(c.ts)
                tags = {
                    "zone": topen.zone(sig.price),
                    "bias": topen.bias(sig.price),
                    "day_q": q["day"], "m90_q": q["m90"],
                    "fair": qtr.value_zone(sig.price, "day"),
                    "fair90": qtr.value_zone(sig.price, "m90"),
                }
                sim.place_limit_set(sig, c.ts, tags)
        # breaker fed with simulated equity as "balance"
        day = c.ts.date()
        day_start_equity.setdefault(day, sim.equity)
        halted = breaker.update(day, 1000.0 + sim.equity)
        is_spike = spike.update(c.high, c.low)
        sim.on_bar(c, sitter, is_spike, halted)
    return sim.closed


# --------------------------------------------------------------------------- #
def metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "pnl": 0.0, "win": 0.0, "avg": 0.0, "pf": 0.0,
                "dd": 0.0}
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": n, "pnl": pnl, "win": 100.0 * len(wins) / n, "avg": pnl / n,
            "pf": (gw / -gl) if gl < 0 else float("inf"), "dd": dd}


def report(name: str, trades: list[dict]) -> None:
    m = metrics(trades)
    print(f"{name:<26} n={m['n']:<5} pnl=${m['pnl']:+10.2f} "
          f"win%={m['win']:5.1f} avg=${m['avg']:+7.2f} PF={m['pf']:5.2f} "
          f"maxDD=${m['dd']:8.2f}")


def trades_to_records(trades: list[dict], symbol: str) -> list[dict]:
    """Flatten sim.closed dicts to backtest_macro Trade records: the entry decision
    time (signal/placement ts, matching the live on_signal veto), symbol, dir, pnl."""
    recs = []
    for t in trades:
        ts = t.get("signal_ts") or t["open_ts"]
        recs.append({"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": symbol,
                     "direction": t["dir"], "pnl": round(t["pnl"], 2)})
    return recs


def write_trades_json(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    print(f"# wrote {len(records)} trades -> {path}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="*", default=["data/*.csv"])
    ap.add_argument("--qty", type=float, default=0.05)
    ap.add_argument("--spread", type=float, default=0.25)
    ap.add_argument("--commission", type=float, default=7.0,
                    help="$ per 1.0 lot round-trip")
    ap.add_argument("--symbol", default="XAUUSD",
                    help="symbol tag for emitted trades (default XAUUSD)")
    ap.add_argument("--emit-trades", dest="emit_trades",
                    help="write entry trades as JSON for scripts/backtest_macro.py")
    args = ap.parse_args()

    paths = [p for a in args.csvs for p in glob.glob(a)]
    if not paths:
        sys.exit("no CSV data found")
    candles = load_csv(paths)
    print(f"bars={len(candles)} {candles[0].ts} .. {candles[-1].ts}")
    print(f"costs: spread={args.spread} commission=${args.commission}/lot RT, "
          f"qty={args.qty}\n")

    trades = run(candles, args.qty, args.spread, args.commission)

    if args.emit_trades:
        write_trades_json(trades_to_records(trades, args.symbol), args.emit_trades)

    report("baseline (live config)", trades)
    report("deadzone-filter (LIVE now)",
           [t for t in trades if t["zone"] != "dead_zone"])
    report("Q3-only (day cycle)",
           [t for t in trades if t["day_q"] == "Q3"])
    report("Q3+Q4 (day cycle)",
           [t for t in trades if t["day_q"] in ("Q3", "Q4")])
    report("brainmd fair-value rule",
           [t for t in trades
            if (t["dir"] == "SHORT" and t["fair"] == "premium")
            or (t["dir"] == "LONG" and t["fair"] == "discount")])

    print("\nby day quarter:")
    for q in ("Q1", "Q2", "Q3", "Q4"):
        report(f"  day {q}", [t for t in trades if t["day_q"] == q])
    print("by 90m quarter:")
    for q in ("Q1", "Q2", "Q3", "Q4"):
        report(f"  m90 {q}", [t for t in trades if t["m90_q"] == q])
    print("by trueopen zone:")
    for z in ("premium", "discount", "dead_zone", None):
        sub = [t for t in trades if t["zone"] == z]
        if sub:
            report(f"  {z}", sub)
    print("by fair value (day q2 open) x dir:")
    for f in ("premium", "discount"):
        for d in ("LONG", "SHORT"):
            sub = [t for t in trades if t["fair"] == f and t["dir"] == d]
            if sub:
                report(f"  {f}/{d}", sub)


if __name__ == "__main__":
    main()
