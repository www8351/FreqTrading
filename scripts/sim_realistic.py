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
from orb.riskguard import ConsecutiveLossGuard, DailyLossBreaker, SpikeCancel
from orb.trueopen import TrueOpenTracker

USD_PER_LOT_PER_DOLLAR = 100.0  # XAUUSD: 1 lot = 100 oz
ADDON_FRAC = 0.8
LIMIT_TTL_SEC = 30 * 60
SPIKE_MIN_AGE_SEC = 120

# SVP timeframe aggregation: 1m input -> these bar sizes (all divide 60 evenly).
_TF_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15}
# Bars before a profile is "ready" (~40-90 min wall-clock per timeframe).
_TF_MIN_BARS = {"1m": 20, "2m": 20, "3m": 16, "5m": 12, "15m": 6}


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

    def place_market(self, sig, ts: datetime, lot: float, tags: dict) -> None:
        """Open a market position immediately (SVP path): fill at the signal
        close +/- half-spread, SL = the structural stop on the signal."""
        short = sig.direction is Direction.SHORT
        fill = sig.price - self.half_spread if short else sig.price + self.half_spread
        side = SHORT if short else LONG
        self._ticket += 1
        self.positions.append(Position(self._ticket, side, lot, fill, sig.stop,
                                       ts, dict(tags), placed_ts=ts))

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


def aggregate_candles(candles: list[Candle], minutes: int) -> list[Candle]:
    """Aggregate 1m bars into N-minute bars, buckets aligned to the UTC hour
    (5m at :00,:05,...; 15m at :00,:15,:30,:45). OHLC = first-open / max-high /
    min-low / last-close, volume summed, bucket ts = bucket start. Time gaps
    separate buckets naturally; the trailing partial bucket is emitted.
    ``minutes <= 1`` is an identity pass."""
    if minutes <= 1:
        return candles
    out: list[Candle] = []
    key: datetime | None = None
    o = h = l = cl = vol = 0.0
    for c in candles:
        floored = c.ts.replace(minute=c.ts.minute - c.ts.minute % minutes,
                               second=0, microsecond=0)
        if key is None:
            key, o, h, l, cl, vol = floored, c.open, c.high, c.low, c.close, c.volume
        elif floored == key:
            h, l, cl, vol = max(h, c.high), min(l, c.low), c.close, vol + c.volume
        elif floored > key:
            out.append(Candle(ts=key, open=o, high=h, low=l, close=cl, volume=vol))
            key, o, h, l, cl, vol = floored, c.open, c.high, c.low, c.close, c.volume
        # floored < key: out-of-order straggler (load_csv sorts) -> drop
    if key is not None:
        out.append(Candle(ts=key, open=o, high=h, low=l, close=cl, volume=vol))
    return out


def _orb_cfg(candles: list[Candle], qty: float, stop_min: float, stop_max: float,
             roc_min: float, tp_rrr: float, tp_close_frac: float) -> OrbConfig:
    return OrbConfig(
        session_open_utc=candles[0].ts.time().replace(second=0, microsecond=0),
        session_len_min=1440, roc_min=roc_min,
        stop_max_dist=stop_max, stop_min_dist=stop_min,
        tp_rrr=tp_rrr, tp_close_frac=tp_close_frac, qty=qty,
        one_trade_per_session=False, rearm_after_exit=True,
        rearm_range="rebuild",
    )


def run(candles: list[Candle], qty: float, spread: float, comm: float,
        max_daily_loss: float = 110.0, stop_min: float = 2.0,
        stop_max: float = 4.0, value_per_move: float = USD_PER_LOT_PER_DOLLAR,
        roc_min: float = 0.15, tp_rrr: float = 2.0, tp_close_frac: float = 0.7,
        partial_frac: float = 0.7, partial_at_r: float = 2.0,
        spike_ratio: float = 2.5) -> list[dict]:
    cfg = _orb_cfg(candles, qty, stop_min, stop_max, roc_min, tp_rrr, tp_close_frac)
    engine = OrbEngine(cfg)
    sim = Sim(qty, spread, comm, value_per_move)
    sitter = Babysitter(partial_frac=partial_frac, partial_at_r=partial_at_r)
    spike = SpikeCancel(ratio=spike_ratio)
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


def run_svp(candles: list[Candle], risk_pct: float = 3.0, spread: float = 0.25,
            comm: float = 7.0, max_daily_loss_pct: float = 10.0,
            value_per_move: float = USD_PER_LOT_PER_DOLLAR,
            start_balance: float = 1000.0, vol_min: float = 0.01,
            vol_step: float = 0.01, vol_max: float = 50.0,
            tpo_fallback: bool = True, timeframe: str = "1m",
            **svp_ov) -> list[dict]:
    """Execution-true SVP backtest: market entries, structural stops, dynamic
    risk_pct sizing, babysitter exits (70% at +2R, chase the rest). The new
    market entry is opened AFTER on_bar so it is never SL-checked on its own bar.

    Daily loss halts at ``max_daily_loss_pct`` of the day's starting balance.
    ``min_session_bars`` auto-scales to the timeframe unless overridden in svp_ov.

    ``tpo_fallback`` defaults True: the historical CSVs carry no tick volume, so
    the profile is built from time-at-price (TPO). Live uses real tick volume.
    """
    from orb.svp import SvpConfig, SvpEngine, compute_lot

    if "min_session_bars" not in svp_ov:
        svp_ov["min_session_bars"] = _TF_MIN_BARS.get(timeframe, 20)
    cfg = SvpConfig(
        session_open_utc=candles[0].ts.time().replace(second=0, microsecond=0),
        session_len_min=1440, risk_pct=risk_pct, tpo_fallback=tpo_fallback,
        **svp_ov,
    )
    engine = SvpEngine(cfg)
    sim = Sim(qty=0.0, spread=spread, commission_rt=comm,
              value_per_move=value_per_move)
    sitter = Babysitter(partial_frac=cfg.partial_frac, partial_at_r=cfg.partial_at_r,
                        breakeven_at_r=cfg.breakeven_at_r)
    spike = SpikeCancel(ratio=2.5)
    breaker = DailyLossBreaker(max_daily_loss_pct=max_daily_loss_pct)
    # consecutive-loss circuit breaker (per UTC-day session); 0 = off
    loss_guard = ConsecutiveLossGuard(cfg.max_consecutive_losses)
    seen_closed = 0

    for c in candles:
        sig = engine.on_candle(c)
        loss_guard.on_period(c.ts.date())           # reset streak on a new session
        bal = start_balance + sim.equity
        halted = breaker.update(c.ts.date(), bal)
        is_spike = spike.update(c.high, c.low)
        sim.on_bar(c, sitter, is_spike, halted)     # exits for existing positions
        # feed any trades that just closed into the consecutive-loss streak
        for t in sim.closed[seen_closed:]:
            loss_guard.record(t["pnl"])
        seen_closed = len(sim.closed)
        if (sig is not None and sig.kind is SignalKind.ENTRY and sig.stop
                and not halted and not loss_guard.blocked):
            remaining = max(0.0, breaker.day_cap + breaker.day_pnl)
            lot = compute_lot(bal, cfg.risk_pct, sig.price, sig.stop,
                              value_per_move, vol_min, vol_step, vol_max,
                              max_risk=remaining)
            if lot > 0:
                sim.place_market(sig, c.ts, lot,
                                 {"dir": sig.direction.value, "reason": sig.reason})
    return sim.closed


# --------------------------------------------------------------------------- #
def metrics(trades: list[dict], start_balance: float = 1000.0) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "pnl": 0.0, "win": 0.0, "avg": 0.0, "pf": 0.0,
                "dd": 0.0, "dd_pct": 0.0}
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    eq = peak = dd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    dd_pct = (dd / start_balance * 100.0) if start_balance > 0 else 0.0
    return {"n": n, "pnl": pnl, "win": 100.0 * len(wins) / n, "avg": pnl / n,
            "pf": (gw / -gl) if gl < 0 else float("inf"), "dd": dd,
            "dd_pct": dd_pct}


def report(name: str, trades: list[dict], start_balance: float = 1000.0) -> None:
    m = metrics(trades, start_balance)
    print(f"{name:<26} n={m['n']:<5} pnl=${m['pnl']:+10.2f} "
          f"win%={m['win']:5.1f} avg=${m['avg']:+7.2f} PF={m['pf']:5.2f} "
          f"maxDD=${m['dd']:8.2f} ({m['dd_pct']:5.1f}%)")


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


def _parse_killzones(spec: str) -> tuple[tuple[int, int], ...]:
    """Parse "07:00-10:00,12:30-15:00" (UTC) -> ((420,600),(750,900)) minutes."""
    def mins(t: str) -> int:
        h, m = t.strip().split(":")
        return int(h) * 60 + int(m)
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        lo, hi = part.split("-")
        out.append((mins(lo), mins(hi)))
    return tuple(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="*", default=["data/*.csv"])
    ap.add_argument("--qty", type=float, default=0.05)
    ap.add_argument("--spread", type=float, default=0.25)
    ap.add_argument("--commission", type=float, default=7.0,
                    help="$ per 1.0 lot round-trip")
    ap.add_argument("--symbol", default="XAUUSD",
                    help="symbol tag for emitted trades (default XAUUSD)")
    ap.add_argument("--strategy", choices=("orb", "svp"), default="orb",
                    help="orb (default) or svp (Session Volume Profile Edge Rotation)")
    ap.add_argument("--timeframe", choices=tuple(_TF_MINUTES), default="1m",
                    help="aggregate 1m input to this bar size before running "
                         "(svp only; 1m = no aggregation)")
    ap.add_argument("--svp-risk-pct", dest="svp_risk_pct", type=float, default=3.0,
                    help="SVP risk per trade as %% of balance (default 3.0)")
    ap.add_argument("--start-balance", dest="start_balance", type=float,
                    default=1000.0, help="starting account balance (default 1000)")
    ap.add_argument("--max-daily-loss-pct", dest="max_daily_loss_pct", type=float,
                    default=10.0, help="halt for the rest of the UTC day after "
                    "losing this %% of the day's starting balance (svp; default 10)")
    ap.add_argument("--svp-enable-lvn", dest="svp_enable_lvn", action="store_true")
    # --- filters & risk management (all OFF by default) --------------------
    ap.add_argument("--svp-trend-filter", dest="svp_trend_filter",
                    choices=("off", "open", "structure", "both", "either"),
                    default="off",
                    help="daily-bias trend filter: LONG only if bullish, SHORT only "
                         "if bearish (open=open vs prior POC, structure=swing HH/HL, "
                         "both=AND, either=OR; default off)")
    ap.add_argument("--svp-atr-period", dest="svp_atr_period", type=int, default=14)
    ap.add_argument("--svp-atr-stop-mult", dest="svp_atr_stop_mult", type=float,
                    default=0.0, help="ATR stop = entry +/- mult*ATR (canonical "
                                      "1.5-2.0); 0 keeps the structural shelf stop")
    ap.add_argument("--svp-breakeven-r", dest="svp_breakeven_r", type=float,
                    default=0.0,
                    help="move stop to entry once profit reaches this many R (0=off)")
    ap.add_argument("--svp-killzones", dest="svp_killzones", default="",
                    help="allowed UTC windows e.g. 07:00-10:00,12:30-15:00 (empty=all)")
    ap.add_argument("--svp-block-open-min", dest="svp_block_open_min", type=int,
                    default=0, help="block the first N min after session open")
    ap.add_argument("--svp-block-close-min", dest="svp_block_close_min", type=int,
                    default=0, help="block the last N min before session close")
    ap.add_argument("--svp-use-delta", dest="svp_use_delta", action="store_true",
                    help="require volume-exhaustion confirm before fading (LIVE only; "
                         "bypassed on zero-volume CSVs)")
    ap.add_argument("--svp-max-consec-losses", dest="svp_max_consec_losses",
                    type=int, default=0,
                    help="stop new entries after N losing trades in a row (0=off)")
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

    if args.strategy == "svp":
        tf_min = _TF_MINUTES[args.timeframe]
        if tf_min > 1:
            candles = aggregate_candles(candles, tf_min)
            print(f"# aggregated to {args.timeframe}: bars={len(candles)}")
        print(f"# risk={args.svp_risk_pct}%/trade daily={args.max_daily_loss_pct}% "
              f"start=${args.start_balance:.0f}")
        print(f"# filters: trend={args.svp_trend_filter} "
              f"atr_stop={args.svp_atr_stop_mult}xATR{args.svp_atr_period} "
              f"be={args.svp_breakeven_r}R consec={args.svp_max_consec_losses} "
              f"killzones='{args.svp_killzones or '-'}' "
              f"blkopen={args.svp_block_open_min} blkclose={args.svp_block_close_min} "
              f"delta={args.svp_use_delta}\n")
        sb = args.start_balance
        trades = run_svp(candles, risk_pct=args.svp_risk_pct, spread=args.spread,
                         comm=args.commission,
                         max_daily_loss_pct=args.max_daily_loss_pct,
                         start_balance=sb, timeframe=args.timeframe,
                         enable_lvn=args.svp_enable_lvn,
                         trend_filter_mode=args.svp_trend_filter,
                         atr_period=args.svp_atr_period,
                         atr_stop_mult=args.svp_atr_stop_mult,
                         breakeven_at_r=args.svp_breakeven_r,
                         killzones=_parse_killzones(args.svp_killzones),
                         block_open_min=args.svp_block_open_min,
                         block_close_min=args.svp_block_close_min,
                         use_delta_confirmation=args.svp_use_delta,
                         max_consecutive_losses=args.svp_max_consec_losses)
        report("svp edge-rotation", trades, sb)
        print("\nby setup:")
        for r in sorted({t["reason"] for t in trades}):
            report(f"  {r}", [t for t in trades if t["reason"] == r], sb)
        print("\nby direction:")
        for d in ("LONG", "SHORT"):
            sub = [t for t in trades if t["dir"] == d]
            if sub:
                report(f"  {d}", sub, sb)
        if args.emit_trades:
            write_trades_json(trades_to_records(trades, args.symbol),
                              args.emit_trades)
        return

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
    report("fair-value rule",
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
