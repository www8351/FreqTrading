"""Execution guards: live spread gate, killzone session gate, fill assessment.

Pure-stdlib helpers for the production execution layer. All of them are inert
until the cli wires them in behind default-off flags:

* :class:`SpreadGate` blocks entries while the live bid/ask spread exceeds a cap.
* :func:`parse_killzones` / :class:`SessionGate` restrict entries to UTC
  minutes-of-day windows — same ``(start_min, end_min)`` tuple convention as
  ``SvpConfig.killzones``, extended to allow wrap-past-midnight windows
  (``"22:00-02:00"``).
* :func:`assess_fill` measures the actual fill against the ORIGINAL signal
  levels (requested entry / stop / tp), exposing slippage and the achieved
  R:R degradation that server-side SL/TP re-anchoring would otherwise hide.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger("orb.execguard")

_WINDOW_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$")


class SpreadGate:
    """Block entries while the live spread is wider than ``max_spread``
    (absolute price units, e.g. 0.4 = 40 cents on gold). :meth:`allows`
    returns ``(allowed, spread)`` so the caller can log the measured spread
    either way; a spread exactly at the cap is allowed."""

    def __init__(self, max_spread: float) -> None:
        if max_spread <= 0:
            raise ValueError("max_spread must be > 0")
        self.max_spread = max_spread

    def allows(self, bid: float, ask: float) -> tuple[bool, float]:
        # round strips float noise so e.g. 4000.4 - 4000.0 compares as 0.4
        spread = round(ask - bid, 10)
        allowed = spread <= self.max_spread
        if not allowed:
            log.info("SPREAD_BLOCK spread=%.5f max=%.5f bid=%s ask=%s",
                     spread, self.max_spread, bid, ask)
        return allowed, spread


def parse_killzones(spec: str) -> tuple[tuple[int, int], ...]:
    """Parse ``"12:00-16:00,07:30-10:00"`` into UTC minutes-of-day pairs
    ``((720, 960), (450, 600))``. A window may wrap past midnight:
    ``"22:00-02:00"`` -> ``(1320, 120)``. Blank spec/tokens are skipped
    (``""`` -> no windows); anything malformed raises ``ValueError``."""
    windows: list[tuple[int, int]] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = _WINDOW_RE.match(token)
        if m is None:
            raise ValueError(f"bad killzone window {token!r} (want HH:MM-HH:MM)")
        h1, m1, h2, m2 = (int(g) for g in m.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if start == end:
            raise ValueError(f"zero-length killzone window {token!r}")
        windows.append((start, end))
    return tuple(windows)


class SessionGate:
    """Allow entries only inside the given UTC minutes-of-day windows.

    Empty ``windows`` = allow everything. Start is inclusive, end exclusive
    (matching the SVP killzone semantics); ``start > end`` means the window
    wraps past midnight. Aware timestamps are converted to UTC; naive ones
    are trusted to already be UTC (repo convention)."""

    def __init__(self, windows: tuple[tuple[int, int], ...]) -> None:
        self.windows = tuple(windows)

    def allows(self, ts: datetime) -> bool:
        if not self.windows:
            return True
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        mod = ts.hour * 60 + ts.minute
        for start, end in self.windows:
            if start < end:
                if start <= mod < end:
                    return True
            elif mod >= start or mod < end:      # wraps past midnight
                return True
        return False


@dataclass(frozen=True)
class FillAssessment:
    """Post-fill verdict computed against the ORIGINAL signal levels.

    ``rr_planned``/``rr_achieved``/``risk_inflation_r`` are ``None`` whenever
    their denominators don't exist (no tp, no stop, or zero stop distance) —
    never a ZeroDivisionError. ``risk_inflation_r`` is the slippage expressed
    in R units of the planned stop distance."""

    slippage: float
    risk_inflation_r: float | None
    rr_planned: float | None
    rr_achieved: float | None
    breach: bool
    degraded: bool


def assess_fill(requested: float, filled: float, stop: float, tp: float | None,
                *, max_slippage: float | None = None,
                rr_floor: float | None = None) -> FillAssessment:
    """Compare the actual fill against the original signal levels.

    ``rr_planned = |tp - requested| / |requested - stop|`` and
    ``rr_achieved = |tp - filled| / |filled - stop|`` — both vs the ORIGINAL
    stop/tp, so an adverse fill shows up as achieved R:R degradation even when
    the broker layer re-anchors SL/TP around the fill. ``tp`` of ``None``/
    ``0.0`` (MT5 "no tp") and ``stop`` of ``0.0``/zero distance leave the R:R
    fields ``None``. ``breach`` fires only when ``max_slippage`` is set and
    exceeded; ``degraded`` only when ``rr_floor`` is set and the achieved R:R
    is known and below it."""
    slippage = round(abs(filled - requested), 10)
    no_stop = stop is None or stop == 0.0
    plan_dist = 0.0 if no_stop else abs(requested - stop)
    fill_dist = 0.0 if no_stop else abs(filled - stop)
    no_tp = tp is None or tp == 0.0

    rr_planned = None
    rr_achieved = None
    risk_inflation_r = None
    if plan_dist > 0:
        risk_inflation_r = slippage / plan_dist
        if not no_tp:
            rr_planned = abs(tp - requested) / plan_dist
            if fill_dist > 0:
                rr_achieved = abs(tp - filled) / fill_dist

    breach = max_slippage is not None and slippage > max_slippage
    degraded = (rr_floor is not None and rr_achieved is not None
                and rr_achieved < rr_floor)
    return FillAssessment(slippage=slippage, risk_inflation_r=risk_inflation_r,
                          rr_planned=rr_planned, rr_achieved=rr_achieved,
                          breach=breach, degraded=degraded)
