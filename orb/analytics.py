"""Trade analytics: stats, daily/hour/duration breakdowns, plain-text report.

Pure stdlib, sync, no I/O. Consumes closed-trade records — either mapped from
``scripts/sim_realistic.py`` ``Sim.closed`` dicts via :func:`from_sim` or built
directly as :class:`TradeRecord`.

Conventions:
    * win = pnl > 0, loss = pnl < 0 (zero-pnl trades count in ``n`` only)
    * gross_loss is reported as a positive magnitude
    * profit_factor: ``None`` when there are no losing trades (inf-safe —
      a float('inf') PF is meaningless for comparison, so it is reported as
      ``None``); ``0.0`` when there are losses but no wins
    * drawdown is measured on the equity curve ``start_balance + cum pnl``
      ordered by close_ts; max_dd_pct is peak-relative at the trough
    * timestamps are normalised to UTC (naive datetimes are assumed UTC)

Fail-safe: every function accepts ``trades == []`` and unusable sim dicts
without raising.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone

__all__ = ["TradeRecord", "from_sim", "compute_stats", "daily_table",
           "by_hour", "by_duration", "format_report"]


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One closed trade (fully flat position)."""
    open_ts: datetime
    close_ts: datetime
    pnl: float
    direction: str = ""
    symbol: str = ""
    volume: float = 0.0
    reason: str = ""


# --------------------------------------------------------------------------- #
def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def from_sim(closed: list[dict]) -> list[TradeRecord]:
    """Map ``Sim.closed`` dicts (scripts/sim_realistic.py) to TradeRecords.

    Sim dicts carry: ticket, dir, entry, open_ts, signal_ts, close_ts, pnl,
    fills=[(reason, px, vol, pnl$), ...] plus arbitrary tag keys. ``volume``
    is the sum of fill volumes (= original position size), ``reason`` the
    final fill's close reason. Records missing open_ts/close_ts/pnl are
    dropped (fail-safe), missing optional keys default.
    """
    out: list[TradeRecord] = []
    for t in closed:
        open_ts = t.get("open_ts")
        close_ts = t.get("close_ts")
        pnl = t.get("pnl")
        if not isinstance(open_ts, datetime) or not isinstance(close_ts, datetime) \
                or pnl is None:
            continue
        fills = t.get("fills") or []
        volume = 0.0
        reason = ""
        for f in fills:
            try:
                volume += float(f[2])
                reason = str(f[0])
            except (TypeError, ValueError, IndexError):
                continue
        out.append(TradeRecord(
            open_ts=open_ts, close_ts=close_ts, pnl=float(pnl),
            direction=str(t.get("dir", "")), symbol=str(t.get("symbol", "")),
            volume=volume, reason=reason,
        ))
    return out


# --------------------------------------------------------------------------- #
def _daily_nets(trades: list[TradeRecord]) -> dict[date, tuple[int, float]]:
    """Per UTC calendar date of close_ts -> (count, net). Sorted by date."""
    days: dict[date, tuple[int, float]] = {}
    for t in trades:
        d = _utc(t.close_ts).date()
        n, net = days.get(d, (0, 0.0))
        days[d] = (n + 1, net + t.pnl)
    return dict(sorted(days.items()))


def compute_stats(trades: list[TradeRecord], *, start_balance: float) -> dict:
    """Aggregate performance stats. See module docstring for conventions.

    Undefined ratios (no wins / no losses / zero drawdown / no positive days)
    are ``None`` — never inf, never a raise.
    """
    n = len(trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    net = sum(t.pnl for t in trades)
    gross_win = sum(wins)
    gross_loss = -sum(losses)                      # positive magnitude

    if losses:
        profit_factor: float | None = gross_win / gross_loss
    else:
        profit_factor = None                       # inf-safe: no losses
    avg_win = gross_win / len(wins) if wins else None
    avg_loss = gross_loss / len(losses) if losses else None
    payoff_ratio = (avg_win / avg_loss
                    if avg_win is not None and avg_loss else None)

    # equity curve ordered by close_ts; peak-to-trough drawdown
    eq = peak = start_balance
    max_dd_abs = max_dd_pct = 0.0
    for t in sorted(trades, key=lambda t: t.close_ts):
        eq += t.pnl
        peak = max(peak, eq)
        dd = peak - eq
        if dd > max_dd_abs:
            max_dd_abs = dd
            max_dd_pct = (dd / peak * 100.0) if peak > 0 else 0.0
    recovery_factor = net / max_dd_abs if max_dd_abs > 0 else None

    days = _daily_nets(trades)
    nets = [v for _, v in days.values()]
    pos = [v for v in nets if v > 0]
    trading_days = len(days)
    return {
        "n": n,
        "net": net,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "trade_win_pct": 100.0 * len(wins) / n if n else 0.0,
        "day_win_pct": 100.0 * len(pos) / trading_days if trading_days else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "expectancy": net / n if n else 0.0,
        "max_dd_abs": max_dd_abs,
        "max_dd_pct": max_dd_pct,
        "recovery_factor": recovery_factor,
        "largest_day_share": max(pos) / sum(pos) if pos else None,
        "daily_stddev": statistics.pstdev(nets) if nets else 0.0,
        "trading_days": trading_days,
        "avg_daily_net": net / trading_days if trading_days else 0.0,
    }


def daily_table(trades: list[TradeRecord]) -> list[dict]:
    """Per UTC calendar date of close_ts: {date (ISO str), n, net, cum}."""
    rows: list[dict] = []
    cum = 0.0
    for d, (n, net) in _daily_nets(trades).items():
        cum += net
        rows.append({"date": d.isoformat(), "n": n, "net": net, "cum": cum})
    return rows


def by_hour(trades: list[TradeRecord]) -> list[dict]:
    """Buckets by open_ts UTC hour: {hour, n, net, win_pct, profit_factor}.

    Only hours with trades, sorted by hour. profit_factor per bucket follows
    the same None-when-no-losses convention as :func:`compute_stats`.
    """
    buckets: dict[int, list[TradeRecord]] = {}
    for t in trades:
        buckets.setdefault(_utc(t.open_ts).hour, []).append(t)
    rows = []
    for hour in sorted(buckets):
        ts = buckets[hour]
        gw = sum(t.pnl for t in ts if t.pnl > 0)
        gl = -sum(t.pnl for t in ts if t.pnl < 0)
        rows.append({
            "hour": hour,
            "n": len(ts),
            "net": sum(t.pnl for t in ts),
            "win_pct": 100.0 * sum(1 for t in ts if t.pnl > 0) / len(ts),
            "profit_factor": gw / gl if gl > 0 else None,
        })
    return rows


_DURATION_BUCKETS: tuple[tuple[str, float], ...] = (
    ("<15m", 15 * 60.0),
    ("15-60m", 60 * 60.0),
    ("1-4h", 4 * 3600.0),
    ("4-24h", 24 * 3600.0),
    (">24h", float("inf")),
)


def by_duration(trades: list[TradeRecord]) -> list[dict]:
    """Buckets on close_ts - open_ts: {bucket, n, net, win_pct}.

    Canonical order <15m, 15-60m, 1-4h, 4-24h, >24h; empty buckets omitted.
    Negative durations (clock anomalies) fall into "<15m" rather than raising.
    """
    grouped: dict[str, list[TradeRecord]] = {}
    for t in trades:
        secs = (t.close_ts - t.open_ts).total_seconds()
        for name, upper in _DURATION_BUCKETS:
            if secs < upper:
                grouped.setdefault(name, []).append(t)
                break
    rows = []
    for name, _ in _DURATION_BUCKETS:
        ts = grouped.get(name)
        if not ts:
            continue
        rows.append({
            "bucket": name,
            "n": len(ts),
            "net": sum(t.pnl for t in ts),
            "win_pct": 100.0 * sum(1 for t in ts if t.pnl > 0) / len(ts),
        })
    return rows


# --------------------------------------------------------------------------- #
def _fmt(v: object) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def format_report(trades: list[TradeRecord], *, start_balance: float,
                  title: str = "") -> str:
    """Readable plain-text report: aligned stats block + the three tables."""
    lines: list[str] = []
    if title:
        lines += [title, "=" * len(title)]
    if not trades:
        lines.append("no trades")
        return "\n".join(lines)

    s = compute_stats(trades, start_balance=start_balance)
    order = ["n", "net", "gross_win", "gross_loss", "profit_factor",
             "trade_win_pct", "day_win_pct", "avg_win", "avg_loss",
             "payoff_ratio", "expectancy", "max_dd_abs", "max_dd_pct",
             "recovery_factor", "largest_day_share", "daily_stddev",
             "trading_days", "avg_daily_net"]
    width = max(len(k) for k in order)
    lines += [f"{k:<{width}} : {_fmt(s[k])}" for k in order]

    lines += ["", "daily",
              f"{'date':<12} {'n':>4} {'net':>10} {'cum':>10}"]
    for r in daily_table(trades):
        lines.append(f"{r['date']:<12} {r['n']:>4} {r['net']:>10.2f} "
                     f"{r['cum']:>10.2f}")

    lines += ["", "by hour (UTC, open_ts)",
              f"{'hour':<5} {'n':>4} {'net':>10} {'win%':>7} {'pf':>6}"]
    for r in by_hour(trades):
        lines.append(f"{r['hour']:<5} {r['n']:>4} {r['net']:>10.2f} "
                     f"{r['win_pct']:>7.1f} {_fmt(r['profit_factor']):>6}")

    lines += ["", "by duration",
              f"{'bucket':<8} {'n':>4} {'net':>10} {'win%':>7}"]
    for r in by_duration(trades):
        lines.append(f"{r['bucket']:<8} {r['n']:>4} {r['net']:>10.2f} "
                     f"{r['win_pct']:>7.1f}")
    return "\n".join(lines)
