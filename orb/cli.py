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
from datetime import datetime, time, timedelta, timezone

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


def _utcnow() -> datetime:
    """Wall clock for the warmup gate; module-level so tests can pin it."""
    return datetime.now(timezone.utc)


def _stale(ts: datetime, start: datetime, grace: timedelta) -> bool:
    """True when ``ts`` predates process ``start`` by more than ``grace``."""
    return ts < start - grace


def _warmup_graces(is_smc: bool, trigger_tf_min: int) -> tuple[timedelta, timedelta]:
    """(bar_grace, sig_grace) for the warmup gate.

    Bars are M1 -> 3 min covers feed latency. Signal.ts is the TRIGGER-TF bar
    OPEN time, so a perfectly live smc signal can be ~trigger_tf_min old —
    only signals whose decision bar closed >= ~2 min before launch are stale.
    """
    bar = timedelta(minutes=3)
    sig = timedelta(minutes=(trigger_tf_min if is_smc else 1) + 2)
    return bar, sig


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
    setif("final_tp_r", getattr(args, "smc_final_tp_r", None))
    setif("stage1_at_r", getattr(args, "smc_stage1_at_r", None))
    setif("stage2_at_r", getattr(args, "smc_stage2_at_r", None))
    setif("stage2_min_lock_r", getattr(args, "smc_stage2_min_lock_r", None))
    setif("comm_per_lot", getattr(args, "smc_comm_per_lot", None))
    setif("stop_buffer", getattr(args, "smc_stop_buffer", None))
    setif("ticks_per_row", getattr(args, "smc_ticks_per_row", None))
    setif("trigger_tf_min", getattr(args, "smc_trigger_tf_min", None))
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

    consec = None
    if getattr(args, "max_consec_losses", 0):
        from .riskguard import ConsecutiveLossGuard

        consec = ConsecutiveLossGuard(args.max_consec_losses)
        print(f"# consec-loss guard: entries halt after "
              f"{args.max_consec_losses} straight losing closes (daily reset)",
              file=sys.stderr)

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

    killzones = None
    if getattr(args, "killzones", None):
        from .execguard import SessionGate, parse_killzones

        try:
            killzones = SessionGate(parse_killzones(args.killzones))
        except ValueError as e:
            print(f"FATAL | {e}", file=sys.stderr)
            return 2
        print(f"# killzones: entries only inside {args.killzones} UTC "
              f"(fixed UTC, no DST tracking)", file=sys.stderr)

    spread_gate = None
    if getattr(args, "max_spread", None) is not None:
        from .execguard import SpreadGate

        try:
            spread_gate = SpreadGate(args.max_spread)
        except ValueError as e:
            print(f"FATAL | {e}", file=sys.stderr)
            return 2
        print(f"# spread-gate: entries skipped while spread > "
              f"{args.max_spread}", file=sys.stderr)

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

    # ---- Part 2 event pipeline: one hub fans every broker action out to the
    # configured sinks (JSONL trade log / copy-trade broadcaster / consec-loss
    # guard). No consumer -> no hub -> broker on_event=None (zero overhead).
    hub = None
    bcaster = None
    if getattr(args, "trade_log", None) or getattr(args, "broadcast", None) \
            or consec is not None:
        from .tradeevents import EventHub, TradeEventLog, to_payload

        hub = EventHub()
        if getattr(args, "trade_log", None):
            hub.add(TradeEventLog(args.trade_log).write)
            print(f"# trade-log: {args.trade_log}", file=sys.stderr)
        if getattr(args, "broadcast", None):
            secret = os.environ.get("COPYTRADE_SECRET")
            if not secret:
                print("FATAL | --broadcast requires COPYTRADE_SECRET in the "
                      "environment (never pass the secret as a CLI arg)",
                      file=sys.stderr)
                return 2
            from .broadcast import Broadcaster

            bcaster = Broadcaster(args.broadcast, secret.encode(),
                                  spool_path=args.broadcast_spool)
            hub.add(lambda ev: bcaster.publish(to_payload(ev)))
            print(f"# broadcast: {args.broadcast} "
                  f"spool={args.broadcast_spool}", file=sys.stderr)
        if consec is not None:
            def _consec_sink(ev):
                if ev.action in ("close", "partial_close") \
                        and ev.pnl is not None:
                    consec.record(ev.pnl)
            hub.add(_consec_sink)

    broker = None
    state = None
    if args.broker == "mt5":
        from .broker import Mt5Broker
        from .brokerstate import BrokerStateCache

        if getattr(args, "resolve_symbol", False):
            import MetaTrader5 as _mt5  # noqa: N816 — scan needs a live handle

            from .symbols import SymbolResolveError, resolve_symbol

            if not _mt5.initialize():
                print(f"FATAL | mt5.initialize failed: {_mt5.last_error()}",
                      file=sys.stderr)
                return 2
            try:
                resolved = resolve_symbol(_mt5, args.symbol)
            except SymbolResolveError as e:
                print(f"FATAL | {e}", file=sys.stderr)
                return 2
            print(f"# symbol_resolved {args.symbol} -> {resolved}",
                  file=sys.stderr)
            args.symbol = resolved

        retry = None
        if getattr(args, "retry_policy", "off") == "on":
            from .broker.retcodes import RetryPolicy

            retry = RetryPolicy(max_retries=args.max_retries)
            print(f"# retry-policy: on max_retries={args.max_retries}",
                  file=sys.stderr)

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
                           else args.entry,
                           on_event=hub.emit if hub is not None else None,
                           strategy=strategy,
                           retry=retry)
        info = broker.connect()
        # Background cache: keep blocking balance/positions IPC off the candle
        # hot path; on_bar reads the snapshot, falling back while it is cold.
        state = BrokerStateCache(broker)
        print(f"# broker mt5 {info} magic={broker.magic} "
              f"strategy={strategy}", file=sys.stderr)
        if getattr(args, "max_slippage", None) is not None:
            # server-side hard cap: deviation (points) = max_slippage / point
            _pt = getattr(broker._mt5.symbol_info(args.symbol),
                          "point", 0.0) or 0.0
            if _pt > 0:
                broker.deviation = int(round(args.max_slippage / _pt))
                print(f"# max-slippage: {args.max_slippage} -> "
                      f"deviation={broker.deviation} points", file=sys.stderr)
            else:
                print(f"# max-slippage: {args.max_slippage} but symbol point "
                      f"unknown; keeping deviation={broker.deviation}",
                      file=sys.stderr)

    # --warmup-gate: while the feed replays history (warmup feeds like
    # mt5feed:btcusd_live), engine state builds but nothing may reach the
    # broker — a stale ENTRY would fire a live order at a historical price,
    # and stale bars would drive SL modifications on real positions.
    warm_start = _utcnow()
    _bar_grace, _sig_grace = _warmup_graces(
        is_smc, smc_cfg.trigger_tf_min if is_smc else 0)
    warm = {"on": bool(getattr(args, "warmup_gate", False)),
            "bars": 0, "sigs": 0}
    if warm["on"]:
        print(f"# warmup-gate: broker actions suppressed for candles older "
              f"than {warm_start - _bar_grace:%Y-%m-%d %H:%M}Z "
              f"(signal grace {int(_sig_grace.total_seconds() // 60)}min)",
              file=sys.stderr)

    def on_transition(tr):
        if not args.quiet:
            print(fmt_transition(tr, dp), file=sys.stderr)

    def on_signal(sig: Signal):
        if warm["on"] and _stale(sig.ts, warm_start, _sig_grace):
            # ALL kinds suppressed (EXIT would market-close real positions);
            # stderr only, so the stdout signals log stays tradable-only.
            warm["sigs"] += 1
            print(f"# WARMUP_SIG {fmt_signal(sig, dp)}", file=sys.stderr)
            return
        print(signal_json(sig) if args.json else fmt_signal(sig, dp))
        sys.stdout.flush()
        if broker is None:
            return
        from .models import SignalKind

        if breaker is not None and breaker.halted and sig.kind is SignalKind.ENTRY:
            print("# HALTED daily loss limit: entry skipped", file=sys.stderr)
            return
        if consec is not None and consec.blocked \
                and sig.kind is SignalKind.ENTRY:
            print(f"# CONSEC_SKIP {consec.streak} straight losses: entry "
                  f"skipped until next UTC day", file=sys.stderr)
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
        if killzones is not None and sig.kind is SignalKind.ENTRY \
                and not killzones.allows(sig.ts):
            print(f"# KILLZONE_SKIP {sig.ts:%H:%M}: entry skipped (windows "
                  f"{args.killzones} UTC)", file=sys.stderr)
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
        if not (is_svp or is_smc) and sig.kind is SignalKind.ENTRY \
                and getattr(args, "risk_pct", None) is not None:
            # ORB equity sizing (clone of the SVP block above): lot so the
            # loss at the signal stop is risk_pct of balance, capped to the
            # remaining daily budget. Flag default None keeps fixed --qty.
            if sig.stop is None:
                print("# ORB_SKIP no stop on signal", file=sys.stderr)
                return
            specs = broker.symbol_specs()
            bal = broker.balance()
            remaining = None
            if breaker is not None:
                remaining = max(0.0, breaker.max_daily_loss + breaker.day_pnl)
            lot = compute_lot(bal, args.risk_pct, sig.price, sig.stop,
                              specs["value_per_move"], specs["volume_min"],
                              specs["volume_step"], specs["volume_max"],
                              max_risk=remaining)
            stop_dist = abs(sig.price - sig.stop)
            if lot <= 0:
                print(f"# ORB_SKIP lot=0 stop_dist={stop_dist:.{dp}f} "
                      f"bal={bal} budget={remaining}", file=sys.stderr)
                return
            sig = dataclasses.replace(sig, qty=lot)
            print(f"# ORB_SIZE lot={lot} risk={args.risk_pct}% "
                  f"stop_dist={stop_dist:.{dp}f}", file=sys.stderr)
        if spread_gate is not None and sig.kind is SignalKind.ENTRY:
            # freshest tick, checked IMMEDIATELY before the order goes out
            try:
                tick = broker.current_spread()
            except OrbError as e:
                print(f"SPREAD_FAIL | {e}: entry skipped", file=sys.stderr)
                return
            allowed_sp, spread = spread_gate.allows(tick["bid"], tick["ask"])
            if not allowed_sp:
                print(f"# SPREAD_SKIP spread={spread} "
                      f"max={spread_gate.max_spread}: entry skipped",
                      file=sys.stderr)
                return
        try:
            res = broker.execute(sig)
            if res is not None:
                print(f"# order {res}", file=sys.stderr)
        except OrbError as e:
            print(f"ORDER_FAIL | {e}", file=sys.stderr)
            return
        if (sig.kind is SignalKind.ENTRY and res is not None
                and broker.entry_mode == "market" and res.get("price")
                and (getattr(args, "max_slippage", None) is not None
                     or getattr(args, "rr_floor", None) is not None)):
            # post-fill R:R verification against the ORIGINAL signal levels:
            # the broker re-anchors SL/TP around the fill (risk distance kept)
            # so slippage shows up here as achieved-R:R degradation.
            from .execguard import assess_fill

            fa = assess_fill(sig.price, float(res["price"]), sig.stop,
                             sig.tp, max_slippage=args.max_slippage,
                             rr_floor=args.rr_floor)
            print(f"# FILL slippage={fa.slippage} "
                  f"rr_planned={_fmt_num(fa.rr_planned, 2)} "
                  f"rr_achieved={_fmt_num(fa.rr_achieved, 2)} "
                  f"risk_inflation_r={_fmt_num(fa.risk_inflation_r, 3)}",
                  file=sys.stderr)
            if fa.breach or fa.degraded:
                why = "slippage" if fa.breach else "rr_floor"
                policy = getattr(args, "slippage_policy", "keep")
                print(f"# ALERT {why}_breach slippage={fa.slippage} "
                      f"rr_achieved={_fmt_num(fa.rr_achieved, 2)} "
                      f"policy={policy}", file=sys.stderr)
                if policy == "close":
                    try:
                        broker.close_all("slippage_abort")
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

            specs0 = broker.symbol_specs()

            def _live_spread() -> float:
                try:
                    return broker.current_spread()["spread"]
                except OrbError:
                    return smc_cfg.stop_buffer  # conservative fallback

            sitter = LadderExitManager(
                partial_levels=smc_cfg.partial_levels,
                final_tp_r=smc_cfg.final_tp_r,
                stage1_at_r=smc_cfg.stage1_at_r, stage2_at_r=smc_cfg.stage2_at_r,
                stage2_min_lock_r=smc_cfg.stage2_min_lock_r,
                stage2_buffer=smc_cfg.stop_buffer,
                comm_per_lot=smc_cfg.comm_per_lot,
                value_per_move=specs0["value_per_move"], spread_fn=_live_spread,
                vol_min=specs0["volume_min"], vol_step=specs0["volume_step"])
            print(f"# ladder exits: partials {smc_cfg.partial_levels} "
                  f"final={smc_cfg.final_tp_r}R stage1={smc_cfg.stage1_at_r}R "
                  f"stage2={smc_cfg.stage2_at_r}R(min_lock={smc_cfg.stage2_min_lock_r}R)",
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
        if consec is not None:
            consec.on_period(c.ts.date())  # streak resets on a new UTC day
        if warm["on"]:
            if _stale(c.ts, warm_start, _bar_grace):
                warm["bars"] += 1
                if spike is not None:
                    spike.update(c.high, c.low)  # keep the range average seeded
                return  # no broker reads/writes during warmup replay
            warm["on"] = False
            print(f"# WARMUP_DONE bars={warm['bars']} "
                  f"suppressed_signals={warm['sigs']}", file=sys.stderr)
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
                if breaker.update(c.ts.date(), state.balance()) and not was:
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
            # babysitter manages every fill: 70% off at +2R, then chase the
            # remainder with the stop at distance d. LadderExitManager wants
            # the full closed candle (N+1 confirmation), not just the close.
            bar_arg = c if getattr(sitter, "SUPPORTS_CANDLE", False) else c.close
            try:
                for act in sitter.on_bar(state.positions(), bar_arg):
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

    async def _run() -> None:
        if state is not None:
            state.start()  # background broker-state refresher (off the hot path)
        try:
            await stream.run(src)
        finally:
            if state is not None:
                await state.aclose()

    try:
        asyncio.run(_run())
    finally:
        if bcaster is not None:
            bcaster.close()
        if broker is not None:
            broker.shutdown()
        if broker is not None and getattr(broker, "retcode_counts", None):
            print(f"# retcode_counts {dict(broker.retcode_counts)}",
                  file=sys.stderr)
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
    p.add_argument("--smc-final-tp-r", dest="smc_final_tp_r", type=float,
                   help="SMC ladder final take-profit in R; 0 = runner trails out "
                        "(default 10.0)")
    p.add_argument("--smc-stage1-at-r", dest="smc_stage1_at_r", type=float,
                   help="SMC stage1 (BE+costs) SL trigger in R (default 1.0)")
    p.add_argument("--smc-stage2-at-r", dest="smc_stage2_at_r", type=float,
                   help="SMC stage2 (final profit lock) SL trigger in R (default 2.0)")
    p.add_argument("--smc-stage2-min-lock-r", dest="smc_stage2_min_lock_r",
                   type=float,
                   help="SMC stage2 SL floor in R, never looser (default 1.0)")
    p.add_argument("--smc-comm-per-lot", dest="smc_comm_per_lot", type=float,
                   help="SMC $ round-trip commission/lot for the stage1 cost "
                        "buffer (default 7.0)")
    p.add_argument("--smc-stop-buffer", dest="smc_stop_buffer", type=float,
                   help="SMC structural SL buffer beyond the OB extreme, in "
                        "price units (default 0.5, gold scale; BTC ~40)")
    p.add_argument("--smc-ticks-per-row", dest="smc_ticks_per_row", type=int,
                   help="SMC volume-profile ticks per row (default 100 = $1 "
                        "rows on gold at tick 0.01; BTC ~3000 = $30 rows)")
    p.add_argument("--smc-trigger-tf-min", dest="smc_trigger_tf_min", type=int,
                   help="SMC trigger timeframe in minutes, must divide 1440 "
                        "and be < htf (H4=240) (default 30)")


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
    lp.add_argument("--warmup-gate", dest="warmup_gate", action="store_true",
                    help="suppress ALL broker actions while the feed replays "
                         "history older than process start (use with warmup "
                         "feeds like orb.feeds.mt5feed:btcusd_live)")
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
    # ---- Part 2 execution layer (all default OFF; see docs/copytrade_schema.md)
    lp.add_argument("--max-spread", dest="max_spread", type=float,
                    help="skip entries while the live spread (ask-bid) exceeds "
                         "this, in price units (gold: 0.4 = 40 cents)")
    lp.add_argument("--killzones", dest="killzones",
                    help="UTC entry windows 'HH:MM-HH:MM[,HH:MM-HH:MM]' "
                         "(e.g. 12:00-16:00 = London/NY overlap); entries "
                         "outside are skipped. Fixed UTC - no DST tracking")
    lp.add_argument("--resolve-symbol", dest="resolve_symbol",
                    action="store_true",
                    help="resolve --symbol to the broker's actual variant "
                         "(XAUUSD -> XAUUSD.ecn/.pro/m/...) before connecting")
    lp.add_argument("--retry-policy", dest="retry_policy",
                    choices=("off", "on"), default="off",
                    help="retcode-policy resend loop (fresh-price requotes, "
                         "exponential backoff, double-fill recovery); "
                         "off (default) = single-send")
    lp.add_argument("--max-retries", dest="max_retries", type=int, default=3,
                    help="retry budget when --retry-policy on (default 3)")
    lp.add_argument("--max-slippage", dest="max_slippage", type=float,
                    help="slippage tolerance in price units: sets the order "
                         "deviation (server-side cap) and flags post-fill "
                         "breaches against the ORIGINAL signal levels")
    lp.add_argument("--slippage-policy", dest="slippage_policy",
                    choices=("keep", "close"), default="keep",
                    help="on a post-fill slippage/R:R breach: keep the "
                         "position and alert (default) or close it immediately")
    lp.add_argument("--rr-floor", dest="rr_floor", type=float,
                    help="minimum achieved R:R at the actual fill price; "
                         "below it the slippage policy fires")
    lp.add_argument("--risk-pct", dest="risk_pct", type=float,
                    help="ORB equity sizing: lot so the loss at the signal "
                         "stop is this %% of balance (default: fixed --qty)")
    lp.add_argument("--max-consec-losses", dest="max_consec_losses", type=int,
                    default=0,
                    help="skip new entries after this many consecutive losing "
                         "closes in a UTC day (0 = off)")
    lp.add_argument("--trade-log", dest="trade_log",
                    help="append schema-v1 trade events to this JSONL file")
    lp.add_argument("--broadcast", dest="broadcast",
                    help="POST trade events to this leader-node URL "
                         "(secret from env COPYTRADE_SECRET, never a flag)")
    lp.add_argument("--broadcast-spool", dest="broadcast_spool",
                    default="broadcast_spool.jsonl",
                    help="offline spool for unsent broadcasts (default "
                         "broadcast_spool.jsonl)")
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
