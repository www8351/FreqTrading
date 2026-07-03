"""Command-line interface: replay a CSV of candles or run a live async source.

Channels:
    stdout  -> Signals (the product; ENTRY/EXIT/REJECT)
    stderr  -> StateTransitions, logs, startup config dump, SUMMARY

Line format is compact, single-line, pipe-delimited key=val for grep/awk.
``--json`` emits JSONL on stdout instead.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import itertools
import json
import logging
import os
import sys
from datetime import datetime, time, timezone

from .engine import OrbEngine
from .models import (Candle, CandleError, OrbConfig, OrbError, OutOfOrderError,
                     Signal, State)
from .smc import SMC_MAGIC, SmcConfig, SmcEngine
from .svp import SVP_MAGIC, SvpConfig, SvpEngine, compute_lot


# --------------------------------------------------------------------------- #
# CSV loading
# --------------------------------------------------------------------------- #
_REQUIRED = ("ts", "open", "high", "low", "close")


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    if raw.isdigit():  # epoch seconds
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_csv(path: str):
    """Yield Candles from a CSV with header ts,open,high,low,close[,volume]."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise CandleError(f"CSV missing columns {missing} in {path}")
        for i, row in enumerate(reader, start=2):  # line 1 is the header
            try:
                yield Candle(
                    ts=_parse_ts(row["ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            except (ValueError, KeyError) as e:
                raise CandleError(f"{path}:{i}: bad row {row}: {e}") from e


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def _fmt_num(v: float | None, dp: int) -> str:
    return "-" if v is None else f"{v:.{dp}f}"


def fmt_signal(sig: Signal, dp: int) -> str:
    d = sig.direction.value if sig.direction else "-"
    parts = [
        sig.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SIGNAL", sig.kind.value, f"{d:<5}",
        f"px={_fmt_num(sig.price, dp)}",
        f"roc={_fmt_num(sig.roc, 2)}",
        f"rvol={_fmt_num(sig.rvol, 2)}",
        f"atr={_fmt_num(sig.atr, dp)}",
        f"stop={_fmt_num(sig.stop, dp)}",
        f"tp={_fmt_num(sig.tp, dp)}",
        f"qty={'-' if sig.qty is None else sig.qty}",
        f"reason={sig.reason}",
    ]
    if sig.bars_held is not None:
        parts.append(f"bars={sig.bars_held}")
    return " | ".join(parts)


def signal_json(sig: Signal) -> str:
    d = dataclasses.asdict(sig)
    d["ts"] = sig.ts.isoformat()
    d["kind"] = sig.kind.value
    d["direction"] = sig.direction.value if sig.direction else None
    d["state_from"] = sig.state_from.value
    d["state_to"] = sig.state_to.value
    return json.dumps(d)


def fmt_transition(tr, dp: int) -> str:
    return " | ".join([
        tr.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "TRANS", f"{tr.state_from.value}->{tr.state_to.value}",
        tr.event, tr.detail,
    ]).rstrip()


# --------------------------------------------------------------------------- #
# Config assembly
# --------------------------------------------------------------------------- #
def build_config(args) -> OrbConfig:
    base: dict = {}
    if args.config:
        with open(args.config) as f:
            base = json.load(f)
        if "session_open_utc" in base and isinstance(base["session_open_utc"], str):
            hh, mm = base["session_open_utc"].split(":")
            base["session_open_utc"] = time(int(hh), int(mm))

    def setif(key, val):
        if val is not None:
            base[key] = val

    setif("range_minutes", args.range_min)
    setif("atr_period", args.atr_period)
    setif("atr_mult", args.atr_mult)
    setif("roc_period", args.roc_period)
    setif("roc_min", args.roc_min)
    setif("rvol_period", args.rvol_period)
    setif("rvol_min", args.rvol_min)
    setif("session_len_min", args.session_len)
    setif("qty", args.qty)
    setif("tp_rrr", args.tp_rrr)
    setif("tp_close_frac", args.tp_close)
    setif("stop_max_dist", args.stop_max)
    setif("stop_min_dist", args.stop_min)
    if args.use_rvol:
        base["use_rvol"] = True
    if args.reentry:
        base["reentry_on"] = args.reentry
    if args.long_only:
        base["allow_short"] = False
    if args.short_only:
        base["allow_long"] = False
    if args.rearm:
        base["one_trade_per_session"] = False
        base["rearm_after_exit"] = True
    if args.rearm_range:
        base["rearm_range"] = args.rearm_range
    if args.session_open:
        hh, mm = args.session_open.split(":")
        base["session_open_utc"] = time(int(hh), int(mm))
    return OrbConfig(**base)


def build_svp_config(args) -> SvpConfig:
    """Assemble an SvpConfig from --svp-* flags (only used with --strategy svp)."""
    base: dict = {}

    def setif(key, val):
        if val is not None:
            base[key] = val

    setif("session_len_min", args.session_len)
    setif("ticks_per_row", getattr(args, "svp_ticks_per_row", None))
    setif("tick_size", getattr(args, "svp_tick_size", None))
    setif("value_area_pct", getattr(args, "svp_va_pct", None))
    setif("hvn_frac", getattr(args, "svp_hvn_frac", None))
    setif("lvn_frac", getattr(args, "svp_lvn_frac", None))
    setif("risk_pct", getattr(args, "svp_risk_pct", None))
    setif("min_session_bars", getattr(args, "svp_min_bars", None))
    setif("stop_buffer_ticks", getattr(args, "svp_buffer_ticks", None))
    if getattr(args, "svp_enable_lvn", False):
        base["enable_lvn"] = True
    if getattr(args, "svp_enable_absorption", False):
        base["enable_absorption_proxy"] = True
    if getattr(args, "svp_tpo_fallback", False):
        base["tpo_fallback"] = True
    if args.long_only:
        base["allow_short"] = False
    if args.short_only:
        base["allow_long"] = False
    if args.session_open:
        hh, mm = args.session_open.split(":")
        base["session_open_utc"] = time(int(hh), int(mm))
    return SvpConfig(**base)


def build_smc_config(args) -> SmcConfig:
    """Assemble an SmcConfig from --smc-* flags (only used with --strategy smc)."""
    base: dict = {}

    def setif(key, val):
        if val is not None:
            base[key] = val

    setif("min_confluences", getattr(args, "smc_min_confluences", None))
    setif("risk_pct", getattr(args, "smc_risk_pct", None))
    setif("disp_atr_mult", getattr(args, "smc_disp_atr_mult", None))
    setif("poc_tol", getattr(args, "smc_poc_tol", None))
    setif("stop_max_dist", getattr(args, "smc_stop_max_dist", None))
    setif("max_trades_per_day", getattr(args, "smc_max_trades_per_day", None))
    setif("trail_mode", getattr(args, "smc_trail_mode", None))
    setif("final_tp_r", getattr(args, "smc_final_tp_r", None))
    return SmcConfig(**base)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_replay(args) -> int:
    candles = load_csv(args.candles)
    if args.session_open == "auto":
        first = next(candles, None)
        if first is None:
            print(f"FATAL | no candles in {args.candles}", file=sys.stderr)
            return 2
        args.session_open = first.ts.strftime("%H:%M")
        candles = itertools.chain([first], candles)
    strategy = getattr(args, "strategy", "orb")
    is_svp = strategy == "svp"
    is_smc = strategy == "smc"
    if is_svp:
        cfg = build_svp_config(args)
    elif is_smc:
        cfg = build_smc_config(args)
    else:
        cfg = build_config(args)
    dp = cfg.instrument_dp
    if not args.quiet:
        print(f"# config {cfg}", file=sys.stderr)

    counts = {"ENTRY": 0, "EXIT": 0, "REJECT": 0}
    sessions: set[str] = set()

    def on_transition(tr):
        if not args.quiet:
            print(fmt_transition(tr, dp), file=sys.stderr)

    def on_signal(sig: Signal):
        counts[sig.kind.value] += 1

    if is_svp:
        engine = SvpEngine(cfg, on_transition=on_transition, on_signal=on_signal)
    elif is_smc:
        engine = SmcEngine(cfg, on_transition=on_transition, on_signal=on_signal)
    else:
        engine = OrbEngine(cfg, on_transition=on_transition, on_signal=on_signal)
    try:
        for c in candles:
            sig = engine.on_candle(c)
            snap = engine.snapshot()
            sid = snap.get("session_id") or snap.get("date")
            if sid:
                sessions.add(sid)
            if sig is not None:
                print(signal_json(sig) if args.json else fmt_signal(sig, dp))
    except (CandleError, OutOfOrderError) as e:
        print(f"FATAL | {e}", file=sys.stderr)
        return 2

    print(
        f"SUMMARY | sessions={len(sessions)} entries={counts['ENTRY']} "
        f"exits={counts['EXIT']} rejects={counts['REJECT']}",
        file=sys.stderr,
    )
    if not any(counts.values()):
        so = getattr(cfg, "session_open_utc", None)
        hint = (f"session open {so:%H:%M} UTC may fall outside the data window; "
                f"try --session-open auto (uses first candle time) or an explicit "
                f"HH:MM inside the data." if so is not None
                else "the strategy found no setups in this data window.")
        print(f"WARN | no signals: {hint}", file=sys.stderr)
    return 0


def cmd_fetch(args) -> int:
    """Download historical candles from a provider and write a replay CSV."""
    from .feeds.twelvedata import fetch_candles

    candles = fetch_candles(
        symbol=args.symbol, interval="1min", outputsize=args.outputsize,
        start_date=args.start, end_date=args.end,
    )
    w = csv.writer(sys.stdout) if args.out == "-" else None
    f = None
    if w is None:
        f = open(args.out, "w", newline="")
        w = csv.writer(f)
    try:
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        c.open, c.high, c.low, c.close, c.volume])
    finally:
        if f is not None:
            f.close()
    dest = "stdout" if args.out == "-" else args.out
    print(f"# wrote {len(candles)} candles -> {dest}", file=sys.stderr)
    return 0


def cmd_live(args) -> int:
    import asyncio
    import importlib

    from .stream import CandleStream

    cfg = build_config(args)
    strategy = getattr(args, "strategy", "orb")
    is_svp = strategy == "svp"
    is_smc = strategy == "smc"
    svp_cfg = build_svp_config(args) if is_svp else None
    smc_cfg = build_smc_config(args) if is_smc else None
    if is_svp:
        dp = svp_cfg.instrument_dp
    elif is_smc:
        dp = smc_cfg.instrument_dp
    else:
        dp = cfg.instrument_dp
    mod_name, _, factory = args.source.partition(":")
    if not factory:
        print("FATAL | --source must be 'module:factory'", file=sys.stderr)
        return 2
    src = getattr(importlib.import_module(mod_name), factory)()

    breaker = None
    if args.max_daily_loss:
        from .riskguard import DailyLossBreaker

        breaker = DailyLossBreaker(args.max_daily_loss)
        print(f"# daily loss breaker: ${args.max_daily_loss}", file=sys.stderr)

    spike = None
    if args.spike_cancel:
        from .riskguard import SpikeCancel

        spike = SpikeCancel(ratio=args.spike_cancel)
        print(f"# spike-cancel: bar range >= {args.spike_cancel}x avg(20) pulls "
              f"unfilled limits", file=sys.stderr)

    trueopen = None
    if getattr(args, "trueopen_filter", "off") != "off":
        from .trueopen import TrueOpenTracker

        trueopen = TrueOpenTracker()
        print("# trueopen-filter: dead_zone entries skipped (close between "
              "TDO / session open / week open)", file=sys.stderr)

    quarter_filter = getattr(args, "quarter_filter", "off")
    if quarter_filter != "off":
        from .quarters import day_quarter  # noqa: F401  (used in on_signal)

        allowed = {"q2q3": ("Q2", "Q3"), "q3": ("Q3",),
                   "q3q4": ("Q3", "Q4")}[quarter_filter]
        print(f"# quarter-filter: entries only in day {'+'.join(allowed)} "
              f"(NY 6h cycle: Q2=London Q3=AM)", file=sys.stderr)

    macro = None
    macro_mode = getattr(args, "macro_mode", "off")
    if macro_mode != "off":
        from .macroguard import MacroGuard

        macro = MacroGuard(
            symbol=args.symbol,
            state_path=getattr(args, "macro_state_path", "macro_state.json"),
            default_when_stale=getattr(args, "macro_default_stale", "allow"),
            conf_min=getattr(args, "macro_conf_min", 0.6),
        )
        print(f"# macro-{macro_mode}: state={macro.state_path} "
              f"stale={macro.default_when_stale} conf_min={macro.conf_min} "
              f"asset={macro.asset_key}", file=sys.stderr)
    _macro_ro = {"on": False}   # risk-off fire-once latch (guard mode)

    broker = None
    if args.broker == "mt5":
        from .broker import Mt5Broker

        if is_smc:
            _magic = SMC_MAGIC
        elif is_svp:
            _magic = SVP_MAGIC
        else:
            _magic = 20260610
        broker = Mt5Broker(symbol=args.symbol,
                           default_qty=0.01 if (is_svp or is_smc)
                           else (cfg.qty or 0.01),
                           allow_live=args.live,
                           magic=_magic,
                           server_tp=False if (is_svp or is_smc)
                           else (cfg.tp_close_frac >= 1.0),
                           entry_mode="market" if (is_svp or is_smc)
                           else args.entry)
        info = broker.connect()
        print(f"# broker mt5 {info} magic={broker.magic} "
              f"strategy={strategy}", file=sys.stderr)

    def on_transition(tr):
        if not args.quiet:
            print(fmt_transition(tr, dp), file=sys.stderr)

    def on_signal(sig: Signal):
        print(signal_json(sig) if args.json else fmt_signal(sig, dp))
        sys.stdout.flush()
        if broker is None:
            return
        from .models import SignalKind

        if breaker is not None and breaker.halted and sig.kind is SignalKind.ENTRY:
            print("# HALTED daily loss limit: entry skipped", file=sys.stderr)
            return
        if trueopen is not None and sig.kind is SignalKind.ENTRY \
                and trueopen.zone(sig.price) == "dead_zone":
            # engine stays BREAKOUT but no order exists; the on_bar sync
            # (force_flat when broker is flat) clears it next bar.
            print("# TRUEOPEN_SKIP dead_zone: entry skipped", file=sys.stderr)
            return
        if quarter_filter != "off" and sig.kind is SignalKind.ENTRY:
            from .quarters import day_quarter

            q = day_quarter(sig.ts)
            if q not in allowed:
                print(f"# QUARTER_SKIP {q}: entry skipped (allowed "
                      f"{'+'.join(allowed)})", file=sys.stderr)
                return
        if macro is not None and sig.kind is SignalKind.ENTRY:
            dec = macro.evaluate_entry(sig)
            if dec.action == "VETO":
                if macro_mode in ("filter", "guard"):
                    print(f"# MACRO_VETO {dec.reason}: entry skipped",
                          file=sys.stderr)
                    return
                print(f"# MACRO_SHADOW would_veto {dec.reason}", file=sys.stderr)
            elif dec.qty is not None and sig.qty is not None \
                    and abs(dec.qty - sig.qty) > 1e-9:
                if macro_mode in ("filter", "guard"):
                    import dataclasses
                    print(f"# MACRO_SCALE qty {sig.qty}->{dec.qty} ({dec.reason})",
                          file=sys.stderr)
                    sig = dataclasses.replace(sig, qty=dec.qty)
                else:
                    print(f"# MACRO_SHADOW would_scale qty {sig.qty}->{dec.qty}",
                          file=sys.stderr)
        if (is_svp or is_smc) and sig.kind is SignalKind.ENTRY:
            # structural-stop dynamic sizing: lot so the loss at the structural
            # stop is risk_pct of balance, capped to the remaining daily budget.
            # smc MUST always inject qty (execute() falls back to default_qty
            # when sig.qty is None), so this branch runs for both strategies.
            tag = "SMC" if is_smc else "SVP"
            risk_pct = smc_cfg.risk_pct if is_smc else svp_cfg.risk_pct
            if sig.stop is None:
                print(f"# {tag}_SKIP no structural stop", file=sys.stderr)
                return
            specs = broker.symbol_specs()
            bal = broker.balance()
            remaining = None
            if breaker is not None:
                remaining = max(0.0, breaker.max_daily_loss + breaker.day_pnl)
            lot = compute_lot(bal, risk_pct, sig.price, sig.stop,
                              specs["value_per_move"], specs["volume_min"],
                              specs["volume_step"], specs["volume_max"],
                              max_risk=remaining)
            stop_dist = abs(sig.price - sig.stop)
            if lot <= 0:
                print(f"# {tag}_SKIP lot=0 stop_dist={stop_dist:.{dp}f} bal={bal} "
                      f"budget={remaining}", file=sys.stderr)
                return
            sig = dataclasses.replace(sig, qty=lot)
            print(f"# {tag}_SIZE lot={lot} risk={risk_pct}% "
                  f"stop_dist={stop_dist:.{dp}f}", file=sys.stderr)
        try:
            res = broker.execute(sig)
            if res is not None:
                print(f"# order {res}", file=sys.stderr)
        except OrbError as e:
            print(f"ORDER_FAIL | {e}", file=sys.stderr)

    if is_svp:
        engine = SvpEngine(svp_cfg, on_transition=on_transition)
    elif is_smc:
        engine = SmcEngine(smc_cfg, on_transition=on_transition)
    else:
        engine = OrbEngine(cfg, on_transition=on_transition)

    sitter = None
    if broker is not None and (broker.entry_mode == "limit" or is_svp or is_smc):
        if is_smc:
            from .smc.exits import LadderExitManager

            sitter = LadderExitManager(
                partial_levels=smc_cfg.partial_levels,
                final_tp_r=smc_cfg.final_tp_r, be_at_r=smc_cfg.be_at_r,
                trail_start_r=smc_cfg.trail_start_r, trail_mode=smc_cfg.trail_mode,
                trail_atr_mult=smc_cfg.trail_atr_mult,
                trail_buffer=smc_cfg.trail_buffer,
                swing_lookback=smc_cfg.swing_lookback, atr_period=smc_cfg.atr_period,
                trail_tf_min=smc_cfg.trigger_tf_min)
            print(f"# ladder exits: partials {smc_cfg.partial_levels} "
                  f"final={smc_cfg.final_tp_r}R be={smc_cfg.be_at_r}R "
                  f"trail={smc_cfg.trail_mode}@{smc_cfg.trail_start_r}R",
                  file=sys.stderr)
        else:
            from .babysitter import Babysitter

            if is_svp:
                sitter = Babysitter(partial_frac=svp_cfg.partial_frac,
                                    partial_at_r=svp_cfg.partial_at_r)
            else:
                sitter = Babysitter(partial_frac=cfg.tp_close_frac
                                    if cfg.tp_close_frac < 1.0 else 0.7,
                                    partial_at_r=cfg.tp_rrr or 2.0)
            print(f"# babysitter: {sitter.partial_frac:.0%} off at "
                  f"+{sitter.partial_at_r}R, stop chases the rest", file=sys.stderr)

    def on_bar(c):
        """After every bar: sync engine vs broker.

        - Server-side SL/TP filled -> force the engine flat (no ghost trades).
        - Engine ratcheted its trail  -> push the new SL to the server so the
          tightened stop is real even if the bot dies or price spikes intrabar.
        """
        if trueopen is not None:
            trueopen.update(c)
        if broker is None:
            return
        if spike is not None and spike.update(c.high, c.low) \
                and broker.has_pending():
            print(f"# SPIKE_CANCEL: abnormal bar ({c.high - c.low:.2f}), "
                  f"pulling unfilled limits", file=sys.stderr)
            try:
                broker.cancel_pending(min_age_sec=120)
            except OrbError as e:
                print(f"CANCEL_FAIL | {e}", file=sys.stderr)
        if breaker is not None:
            try:
                was = breaker.halted
                if breaker.update(c.ts.date(), broker.balance()) and not was:
                    print("# DAILY_LOSS_HALT: closing positions, no new entries "
                          "until next UTC day", file=sys.stderr)
                    broker.close_all("daily_loss_halt")
                    if broker.entry_mode == "limit":
                        broker.cancel_pending()
            except OrbError as e:
                print(f"BREAKER_FAIL | {e}", file=sys.stderr)
        if macro is not None and macro_mode == "guard":
            try:
                ro, reason = macro.risk_off_now()
                if ro and not _macro_ro["on"]:
                    _macro_ro["on"] = True
                    print(f"# MACRO_RISK_OFF {reason}: closing positions, "
                          f"halting entries", file=sys.stderr)
                    broker.close_all(f"macro_risk_off:{reason}")
                    if broker.entry_mode == "limit":
                        broker.cancel_pending()
                elif not ro and _macro_ro["on"]:
                    _macro_ro["on"] = False
                    print("# MACRO_RISK_ON: risk-off cleared", file=sys.stderr)
            except OrbError as e:
                print(f"MACRO_FAIL | {e}", file=sys.stderr)
        if broker.entry_mode == "limit" or is_svp or is_smc:
            if broker.entry_mode == "limit" and args.limit_ttl:
                try:
                    n = broker.cancel_expired(int(args.limit_ttl * 60))
                    if n:
                        print(f"# limit_ttl: {n} stale limit(s) expired "
                              f"(>{args.limit_ttl}min)", file=sys.stderr)
                except OrbError as e:
                    print(f"TTL_FAIL | {e}", file=sys.stderr)
            # ladder needs the 1m feed to build higher-TF trail context;
            # Babysitter/svp have no observe() so this is smc-only.
            if hasattr(sitter, "observe"):
                sitter.observe(c)
            # babysitter manages every fill: 70% off at +2R, then chase the
            # remainder with the stop at distance d
            try:
                for act in sitter.on_bar(broker.my_positions(), c.close):
                    if act.kind == "partial_close":
                        res = broker.close_ticket(act.ticket, act.volume)
                        if res is not None:
                            print(f"# partial_2r ticket={act.ticket} {res}",
                                  file=sys.stderr)
                    else:
                        res = broker.modify_sl(act.ticket, act.sl)
                        if res is not None and not args.quiet:
                            print(f"# chase_sl ticket={act.ticket} "
                                  f"sl={act.sl:.{dp}f}", file=sys.stderr)
            except OrbError as e:
                print(f"BABYSIT_FAIL | {e}", file=sys.stderr)
            if engine.state is State.BREAKOUT and not broker.has_position() \
                    and not broker.has_pending():
                sig = engine.force_flat(c.ts)
                if sig is not None:
                    print(signal_json(sig) if args.json else fmt_signal(sig, dp))
                    sys.stdout.flush()
            return
        if engine.state is not State.BREAKOUT:
            return
        if not broker.has_position():
            sig = engine.force_flat(c.ts)
            if sig is not None:
                print(signal_json(sig) if args.json else fmt_signal(sig, dp))
                sys.stdout.flush()
            return
        pos = engine.position
        if pos is not None:
            try:
                res = broker.update_stop(pos.stop)
                if res is not None and not args.quiet:
                    print(f"# sl_update {pos.stop:.{dp}f}", file=sys.stderr)
            except OrbError as e:
                print(f"SL_UPDATE_FAIL | {e}", file=sys.stderr)

    stream = CandleStream(engine, on_signal=on_signal, on_bar=on_bar)
    try:
        asyncio.run(stream.run(src))
    finally:
        if broker is not None:
            broker.shutdown()
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _add_svp_flags(p) -> None:
    """--svp-* tuning flags (shared by live and replay)."""
    p.add_argument("--svp-ticks-per-row", dest="svp_ticks_per_row", type=int,
                   help="SVP profile row width in ticks (default 10)")
    p.add_argument("--svp-tick-size", dest="svp_tick_size", type=float,
                   help="SVP tick size (default 0.01 = gold)")
    p.add_argument("--svp-va-pct", dest="svp_va_pct", type=float,
                   help="SVP value-area fraction (default 0.70)")
    p.add_argument("--svp-hvn-frac", dest="svp_hvn_frac", type=float,
                   help="SVP HVN threshold as a fraction of max row vol (0.70)")
    p.add_argument("--svp-lvn-frac", dest="svp_lvn_frac", type=float,
                   help="SVP LVN threshold as a fraction of mean row vol (0.30)")
    p.add_argument("--svp-risk-pct", dest="svp_risk_pct", type=float,
                   help="SVP risk per trade as %% of balance (default 5.0)")
    p.add_argument("--svp-min-bars", dest="svp_min_bars", type=int,
                   help="SVP session bars before any entry (default 20)")
    p.add_argument("--svp-buffer-ticks", dest="svp_buffer_ticks", type=float,
                   help="SVP structural-stop buffer beyond the shelf, in ticks "
                        "(default 50 = $0.50 for gold)")
    p.add_argument("--svp-enable-lvn", dest="svp_enable_lvn", action="store_true",
                   help="SVP: enable LVN break entries (off by default)")
    p.add_argument("--svp-enable-absorption", dest="svp_enable_absorption",
                   action="store_true",
                   help="SVP: enable the directionless absorption proxy (off; "
                        "NOT true delta — tick volume is undirected)")
    p.add_argument("--svp-tpo-fallback", dest="svp_tpo_fallback",
                   action="store_true",
                   help="SVP: build the profile from time-at-price (TPO) when a "
                        "bar has no tick volume (for volume-less feeds; live "
                        "mt5feed has tick volume, so leave off)")


def _add_smc_flags(p) -> None:
    """--smc-* tuning flags (shared by live and replay)."""
    p.add_argument("--smc-min-confluences", dest="smc_min_confluences", type=int,
                   help="SMC confluence checks required to fire, 1..6 (default 3; "
                        "htf_poi is always mandatory)")
    p.add_argument("--smc-risk-pct", dest="smc_risk_pct", type=float,
                   help="SMC risk per trade as %% of balance, (0, 10] (default 2.0)")
    p.add_argument("--smc-disp-atr-mult", dest="smc_disp_atr_mult", type=float,
                   help="SMC displacement bar range vs ATR minimum (default 1.2)")
    p.add_argument("--smc-poc-tol", dest="smc_poc_tol", type=float,
                   help="SMC price distance counted as 'at POC' (default 2.0)")
    p.add_argument("--smc-stop-max-dist", dest="smc_stop_max_dist", type=float,
                   help="SMC reject entries with structural stops wider than this "
                        "(default 15.0)")
    p.add_argument("--smc-max-trades-per-day", dest="smc_max_trades_per_day",
                   type=int, help="SMC max entries per UTC day (default 2)")
    p.add_argument("--smc-trail-mode", dest="smc_trail_mode",
                   choices=("swing", "atr"),
                   help="SMC ladder trail: swing (default) or atr")
    p.add_argument("--smc-final-tp-r", dest="smc_final_tp_r", type=float,
                   help="SMC ladder final take-profit in R; 0 = runner trails out "
                        "(default 10.0)")


def _add_common(p) -> None:
    p.add_argument("--config", help="JSON config file (CLI flags override)")
    p.add_argument("--range-min", dest="range_min", type=int)
    p.add_argument("--atr-period", dest="atr_period", type=int)
    p.add_argument("--atr-mult", dest="atr_mult", type=float)
    p.add_argument("--roc-period", dest="roc_period", type=int)
    p.add_argument("--roc-min", dest="roc_min", type=float)
    p.add_argument("--rvol-period", dest="rvol_period", type=int)
    p.add_argument("--rvol-min", dest="rvol_min", type=float)
    p.add_argument("--use-rvol", dest="use_rvol", action="store_true")
    p.add_argument("--qty", type=float, help="lot size attached to signals (e.g. 0.01)")
    p.add_argument("--tp-rrr", dest="tp_rrr", type=float,
                   help="fixed take-profit at RRR x initial risk (e.g. 3 = 1:3)")
    p.add_argument("--tp-close", dest="tp_close", type=float,
                   help="fraction closed at TP (e.g. 0.7); rest rides the trail")
    p.add_argument("--stop-max", dest="stop_max", type=float,
                   help="hard cap on stop distance in price units (gold: 4.0 = 40 pips)")
    p.add_argument("--stop-min", dest="stop_min", type=float,
                   help="hard floor on stop distance - trail never chokes tighter "
                        "(gold: 2.0 = 20 pips)")
    p.add_argument("--session-open", dest="session_open",
                   help="HH:MM UTC, or 'auto' (replay only: first candle time)")
    p.add_argument("--session-len", dest="session_len", type=int)
    p.add_argument("--reentry", choices=("close", "intrabar"))
    p.add_argument("--long-only", dest="long_only", action="store_true")
    p.add_argument("--short-only", dest="short_only", action="store_true")
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--rearm-range", dest="rearm_range", choices=("rebuild", "keep"),
                   help="after a rearmed exit: rebuild fresh range (default) or keep")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--log-level", default="WARNING")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orb", description="XAU/USD ORB signal engine")
    sub = parser.add_subparsers(dest="command", required=True)

    rp = sub.add_parser("replay", help="replay a CSV of 1m candles (backtest)")
    rp.add_argument("candles", help="CSV: ts,open,high,low,close[,volume]")
    rp.add_argument("--strategy", choices=("orb", "svp", "smc"), default="orb",
                    help="orb (default), svp or smc (backtest that strategy's "
                         "engine; --svp-*/--smc-* flags tune it)")
    _add_svp_flags(rp)
    _add_smc_flags(rp)
    _add_common(rp)
    rp.set_defaults(func=cmd_replay)

    lp = sub.add_parser("live", help="run an async candle source")
    lp.add_argument("--source", default="orb.feeds.twelvedata:xauusd_live",
                    help="module:factory -> async iterator/Queue")
    lp.add_argument("--broker", choices=("mt5",),
                    help="send ENTRY/EXIT signals as real orders")
    lp.add_argument("--symbol", default="XAUUSD.ecn",
                    help="broker symbol name (default XAUUSD.ecn)")
    lp.add_argument("--live", action="store_true",
                    help="allow trading on a NON-demo account (default: demo only)")
    lp.add_argument("--limit-ttl", dest="limit_ttl", type=float, default=30.0,
                    help="cancel unfilled limits after this many minutes "
                         "(default 30; 0 = never)")
    lp.add_argument("--spike-cancel", dest="spike_cancel", type=float,
                    help="pull unfilled limits when a bar's range >= this x the "
                         "avg of the last 20 bars (e.g. 2.5)")
    lp.add_argument("--trueopen-filter", dest="trueopen_filter",
                    choices=("off", "deadzone"), default="off",
                    help="deadzone: skip entries when price sits between the "
                         "true opens (TDO / session / week) — backtested as "
                         "the largest bleed segment")
    lp.add_argument("--quarter-filter", dest="quarter_filter",
                    choices=("off", "q2q3", "q3", "q3q4"), default="off",
                    help="restrict entries to day quarters (NY 6h cycle: "
                         "Q1 Asia 18-00 / Q2 London 00-06 / Q3 AM 06-12 / "
                         "Q4 PM 12-18). q2q3 = London+AM")
    lp.add_argument("--entry", choices=("market", "limit"), default="market",
                    help="limit: enter at the liquidity level (old stop spot) "
                         "with one pre-placed add-on toward the SL")
    lp.add_argument("--strategy", choices=("orb", "svp", "smc"), default="orb",
                    help="orb (default, opening-range breakout), svp (Session "
                         "Volume Profile Edge Rotation; market entry, structural "
                         "stops, dynamic 5%% sizing, magic 20260620), or smc (Smart "
                         "Money Concepts A+ MTF; market entry, structural stops, "
                         "dynamic sizing, ladder exits, magic 20260621)")
    _add_svp_flags(lp)
    _add_smc_flags(lp)
    lp.add_argument("--max-daily-loss", dest="max_daily_loss", type=float,
                    help="halt trading for the rest of the UTC day after losing "
                         "this many account-currency units (e.g. 110)")
    lp.add_argument("--macro-mode", dest="macro_mode",
                    choices=("off", "shadow", "filter", "guard"), default="off",
                    help="fundamental/macro layer: off (default, no "
                         "effect), shadow (log decisions only), filter (veto/scale "
                         "entries), guard (filter + risk-off close on blackout/"
                         "war-spike)")
    lp.add_argument("--macro-state-path", dest="macro_state_path",
                    default="macro_state.json",
                    help="path to the MacroState JSON written by the macro sidecar")
    lp.add_argument("--macro-default-stale", dest="macro_default_stale",
                    choices=("allow", "block"), default="allow",
                    help="when macro state is missing/stale: allow (trade as today, "
                         "default) or block (no new entries)")
    lp.add_argument("--macro-conf-min", dest="macro_conf_min", type=float,
                    default=0.6,
                    help="min macro confidence to act on a bias conflict / risk-off")
    _add_common(lp)
    lp.set_defaults(func=cmd_live)

    fp = sub.add_parser("fetch", help="download historical candles -> replay CSV")
    fp.add_argument("--symbol", default="XAU/USD")
    fp.add_argument("--outputsize", type=int, default=500)
    fp.add_argument("--start", help="start_date YYYY-MM-DD [HH:MM:SS]")
    fp.add_argument("--end", help="end_date YYYY-MM-DD [HH:MM:SS]")
    fp.add_argument("--out", default="-", help="output CSV path ('-' = stdout)")
    fp.set_defaults(func=cmd_fetch)
    return parser


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (no overrides)."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def main(argv=None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    level_name = getattr(args, "log_level", "WARNING")
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.WARNING),
        format="%(levelname)s %(name)s %(message)s", stream=sys.stderr,
    )
    try:
        return args.func(args)
    except OrbError as e:
        print(f"FATAL | {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
