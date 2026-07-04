"""Pull closed trades from a live MT5 account by magic and print the pro report.

Read-only: only ``history_deals_get`` is called — no orders, no modifications.
Deals are grouped by ``position_id`` and collapsed into one
``orb.analytics.TradeRecord`` per position (partials fold into a single record).
The report itself is produced by :func:`orb.analytics.format_report`.

Works for the existing live bots (XAUUSD ORB 20260610, US100 ORB 20260611) and
the SVP / SMC magics — any magic can be passed on the CLI.

Usage:
    python scripts/live_report.py --magic 20260611 --days 30
    python scripts/live_report.py --known --days 14
    python scripts/live_report.py --magic 20260621 --start-balance 1000

The MT5 module is injectable (``main(mt5=...)``) so tests run without a
terminal; a live pull needs the MetaTrader 5 terminal running and logged in.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timedelta, timezone

# Allow ``python scripts/live_report.py`` from anywhere: put the workspace root
# (which holds the ``orb`` package) on sys.path. Tests get this via conftest.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from orb.analytics import TradeRecord, format_report  # noqa: E402

__all__ = ["KNOWN", "fetch_deals", "build_report", "main"]

# Known live magics -> human-readable preset label (for the report header).
KNOWN: dict[int, str] = {
    20260610: "XAUUSD ORB",
    20260611: "US100 ORB",
    20260620: "SVP",
    20260621: "SMC",
}


def _utc(posix_time) -> datetime:
    """POSIX int/float deal time -> tz-aware UTC datetime."""
    return datetime.fromtimestamp(int(posix_time), tz=timezone.utc)


def fetch_deals(mt5, magic: int, days: int, now=None) -> list[TradeRecord]:
    """Group this ``magic``'s history deals of the last ``days`` into records.

    One :class:`TradeRecord` per ``position_id``. Per position:
      * open_ts  = time of the DEAL_ENTRY_IN deal (else the earliest deal),
      * close_ts = time of the last out-deal,
      * pnl      = sum over the position's deals of
                   ``profit + commission + swap + fee``,
      * direction= entry deal type (DEAL_TYPE_BUY -> LONG, else SHORT),
      * symbol   = the deal's symbol,
      * volume   = the entry deal's volume.

    Positions with no in-deal are skipped (fail-safe). Partials collapse into
    ONE record.
    """
    now = now or datetime.now(timezone.utc)
    from_dt = now - timedelta(days=days)
    deals = mt5.history_deals_get(from_dt, now) or ()

    # Group this magic's deals by position.
    groups: dict[object, list] = {}
    for d in deals:
        if getattr(d, "magic", None) != magic:
            continue
        groups.setdefault(d.position_id, []).append(d)

    out: list[TradeRecord] = []
    entry_in = getattr(mt5, "DEAL_ENTRY_IN", 0)
    type_buy = getattr(mt5, "DEAL_TYPE_BUY", 0)

    for _pos, ds in groups.items():
        ds_sorted = sorted(ds, key=lambda d: int(d.time))
        ins = [d for d in ds_sorted if d.entry == entry_in]
        if ins:
            entry = ins[0]
        else:
            # No explicit in-deal: fail-safe skip (cannot define the position).
            continue

        outs = [d for d in ds_sorted if d is not entry]
        close_deal = outs[-1] if outs else entry

        pnl = 0.0
        for d in ds_sorted:
            pnl += (getattr(d, "profit", 0.0) or 0.0)
            pnl += (getattr(d, "commission", 0.0) or 0.0)
            pnl += (getattr(d, "swap", 0.0) or 0.0)
            pnl += (getattr(d, "fee", 0.0) or 0.0)

        direction = "LONG" if entry.type == type_buy else "SHORT"
        out.append(TradeRecord(
            open_ts=_utc(entry.time),
            close_ts=_utc(close_deal.time),
            pnl=pnl,
            direction=direction,
            symbol=getattr(entry, "symbol", ""),
            volume=float(getattr(entry, "volume", 0.0) or 0.0),
        ))

    out.sort(key=lambda r: r.close_ts)
    return out


def build_report(trades: list[TradeRecord], *, magic: int, days: int,
                 start_balance: float) -> str:
    """Wrap :func:`orb.analytics.format_report` with a magic/window header."""
    label = KNOWN.get(magic)
    name = f"magic {magic}" + (f" ({label})" if label else "")
    title = f"LIVE REPORT  {name}  last {days}d"
    return format_report(trades, start_balance=start_balance, title=title)


def _resolve_start_balance(mt5, cli_value) -> float:
    if cli_value is not None:
        return float(cli_value)
    try:
        acct = mt5.account_info()
        if acct is not None and getattr(acct, "balance", None) is not None:
            return float(acct.balance)
    except Exception:  # noqa: BLE001 - never let a balance probe crash the run
        pass
    return 1000.0


def main(argv=None, mt5=None) -> int:
    ap = argparse.ArgumentParser(
        description="Print the orb.analytics pro report for a live MT5 magic.")
    ap.add_argument("--magic", action="append", type=int,
                    help="magic number (repeatable)")
    ap.add_argument("--days", type=int, default=30,
                    help="look-back window in days (default 30)")
    ap.add_argument("--known", action="store_true",
                    help="report every known preset magic")
    ap.add_argument("--start-balance", type=float, default=None,
                    help="equity-curve base (default: account balance or 1000)")
    ap.add_argument("--mt5-path", default=None,
                    help="path to terminal64.exe (passed to mt5.initialize)")
    args = ap.parse_args(argv)

    magics: list[int] = []
    if args.known:
        magics.extend(KNOWN)
    if args.magic:
        magics.extend(args.magic)
    # de-dupe, preserve order
    seen: set[int] = set()
    magics = [m for m in magics if not (m in seen or seen.add(m))]
    if not magics:
        print("no magic given: pass --magic <int> or --known", file=sys.stderr)
        return 2

    owns_mt5 = mt5 is None
    if mt5 is None:
        try:
            import MetaTrader5 as mt5  # noqa: N816
        except ImportError:
            print("MetaTrader5 not importable (install the package and run "
                  "the terminal)", file=sys.stderr)
            return 2

    ok = mt5.initialize(args.mt5_path) if args.mt5_path else mt5.initialize()
    if not ok:
        err = getattr(mt5, "last_error", lambda: "unknown")()
        print(f"mt5.initialize failed: {err} — is the MT5 terminal running "
              f"and logged in?", file=sys.stderr)
        return 3

    try:
        start_balance = _resolve_start_balance(mt5, args.start_balance)
        for magic in magics:
            trades = fetch_deals(mt5, magic=magic, days=args.days)
            print(build_report(trades, magic=magic, days=args.days,
                               start_balance=start_balance))
            print()
    finally:
        if owns_mt5:
            mt5.shutdown()
        else:
            # injected fake: still honour shutdown so tests can assert it ran
            shutdown = getattr(mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
