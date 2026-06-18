"""Incremental Session Volume Profile accumulator.

Builds a price->tick-volume histogram on a fixed row grid, one closed M1 bar at
a time. Each bar's volume is split EVENLY across the rows its [low, high] span
covers (TPO-style uniform distribution). From the histogram it derives:

    POC          row of maximum volume (the "fairest" price)
    Value Area   contiguous rows around the POC holding ``value_area_pct`` of
                 the total volume, expanded by the standard two-rows-up vs
                 two-rows-down comparison
    HVN / LVN    high/low-volume nodes (peaks / valleys of the histogram)
    Shape        D / P / b / B / I morphology

Contract mirrors :mod:`orb.indicators`: ``update(candle)`` feeds one bar,
``ready`` flips once the session has matured. Pure, stdlib only. Cost is
O(rows-per-bar) per update, bounded by ``max_rows_per_bar``.
"""

from __future__ import annotations

import logging
import math

from ..models import Candle
from .levels import ProfileLevels, Shape

log = logging.getLogger("orb.svp.profile")


class VolumeProfile:
    __slots__ = (
        "row_size", "value_area_pct", "hvn_frac", "lvn_frac", "min_bars",
        "max_rows_per_bar", "va_tiebreak", "i_max_peak", "tpo_fallback",
        "_anchor", "_hist", "_min_row", "_max_row", "_total", "_bars",
    )

    def __init__(
        self,
        row_size: float,
        *,
        value_area_pct: float = 0.70,
        hvn_frac: float = 0.70,
        lvn_frac: float = 0.30,
        min_bars: int = 20,
        max_rows_per_bar: int = 5000,
        va_tiebreak: str = "up",
        i_max_peak: float = 1.5,
        tpo_fallback: bool = False,
    ) -> None:
        if row_size <= 0:
            raise ValueError("row_size must be > 0")
        if not (0.0 < value_area_pct <= 1.0):
            raise ValueError("value_area_pct must be in (0, 1]")
        if va_tiebreak not in ("up", "down"):
            raise ValueError("va_tiebreak must be 'up' or 'down'")
        self.row_size = float(row_size)
        self.value_area_pct = float(value_area_pct)
        self.hvn_frac = float(hvn_frac)
        self.lvn_frac = float(lvn_frac)
        self.min_bars = int(min_bars)
        self.max_rows_per_bar = int(max_rows_per_bar)
        self.va_tiebreak = va_tiebreak
        self.i_max_peak = float(i_max_peak)
        self.tpo_fallback = bool(tpo_fallback)
        self._anchor: float | None = None
        self._hist: dict[int, float] = {}
        self._min_row: int | None = None
        self._max_row: int | None = None
        self._total = 0.0
        self._bars = 0

    # ------------------------------------------------------------------ #
    # Accumulation
    # ------------------------------------------------------------------ #
    def update(self, c: Candle) -> None:
        """Distribute one closed bar's weight evenly across the rows it spans.

        Weight is the bar's tick volume. When ``tpo_fallback`` is set and a bar
        has no volume, it contributes 1 unit (a TPO / time-at-price profile) —
        used for backtesting history that lacks tick volume; live uses real
        volume with the fallback off.
        """
        if self._anchor is None:
            self._anchor = c.low
        self._bars += 1
        weight = c.volume if c.volume > 0 else (1.0 if self.tpo_fallback else 0.0)
        if weight <= 0:
            return  # nothing to distribute (still counts as an elapsed bar)

        lo = self._row_of(c.low)
        hi = self._row_of(c.high)
        if hi < lo:  # defensive; validate() guarantees high>=low
            lo, hi = hi, lo
        n = hi - lo + 1
        if n > self.max_rows_per_bar:
            log.warning("svp_rows_blowup ts=%s rows=%d (skipped)", c.ts, n)
            return

        share = weight / n
        h = self._hist
        for r in range(lo, hi + 1):
            h[r] = h.get(r, 0.0) + share
        self._total += weight
        self._min_row = lo if self._min_row is None else min(self._min_row, lo)
        self._max_row = hi if self._max_row is None else max(self._max_row, hi)

    def reset(self) -> None:
        """Clear all state for a fresh session (new anchor on the next bar)."""
        self._anchor = None
        self._hist = {}
        self._min_row = None
        self._max_row = None
        self._total = 0.0
        self._bars = 0

    # ------------------------------------------------------------------ #
    # Readiness + levels
    # ------------------------------------------------------------------ #
    @property
    def ready(self) -> bool:
        return self._bars >= self.min_bars and self._total > 0.0

    @property
    def bars(self) -> int:
        return self._bars

    @property
    def total_volume(self) -> float:
        return self._total

    @property
    def poc(self) -> float | None:
        if not self.ready:
            return None
        row = self._poc_row()
        return None if row is None else self._row_price(row)

    @property
    def vah(self) -> float | None:
        va = self._value_area()
        if va is None or not self.ready:
            return None
        return self._row_price(va[1])

    @property
    def val(self) -> float | None:
        va = self._value_area()
        if va is None or not self.ready:
            return None
        return self._row_price(va[0])

    def hvns(self) -> list[float]:
        if not self.ready:
            return []
        hvn, _ = self._nodes()
        return [self._row_price(r) for r in hvn]

    def lvns(self) -> list[float]:
        if not self.ready:
            return []
        _, lvn = self._nodes()
        return [self._row_price(r) for r in lvn]

    def shape(self) -> Shape:
        if not self.ready:
            return Shape.NONE
        return self._classify_shape()

    def levels(self) -> ProfileLevels | None:
        """Immutable snapshot, or ``None`` until ``ready``."""
        if not self.ready:
            return None
        poc = self._poc_row()
        va = self._value_area()
        if poc is None or va is None:
            return None
        lo, hi = va
        hvn, lvn = self._nodes()
        return ProfileLevels(
            poc=self._row_price(poc),
            vah=self._row_price(hi),
            val=self._row_price(lo),
            hvns=tuple(self._row_price(r) for r in hvn),
            lvns=tuple(self._row_price(r) for r in lvn),
            shape=self._classify_shape(),
            total_volume=self._total,
        )

    # ------------------------------------------------------------------ #
    # Geometry
    # ------------------------------------------------------------------ #
    def _row_of(self, price: float) -> int:
        return math.floor((price - self._anchor) / self.row_size)

    def _row_price(self, row: int) -> float:
        # center of the row band
        return self._anchor + (row + 0.5) * self.row_size

    # ------------------------------------------------------------------ #
    # POC / Value Area / Nodes
    # ------------------------------------------------------------------ #
    def _poc_row(self) -> int | None:
        h = self._hist
        if not h:
            return None
        best_r: int | None = None
        best_v = -1.0
        # ascending scan + strict '>' => ties resolve to the lower (cheaper) row
        for r in sorted(h):
            v = h[r]
            if v > best_v:
                best_v = v
                best_r = r
        return best_r

    def _value_area(self):
        """Return (val_row, vah_row) inclusive, or ``None``.

        Seeds at the POC row, then repeatedly compares the two rows above against
        the two rows below and absorbs the heavier pair until ``value_area_pct``
        of total volume is enclosed. Ties favour ``va_tiebreak`` (default up).
        """
        h = self._hist
        poc = self._poc_row()
        if poc is None or self._total <= 0.0:
            return None
        mn, mx = self._min_row, self._max_row
        target = self.value_area_pct * self._total
        va = h.get(poc, 0.0)
        lo = hi = poc
        up, dn = poc + 1, poc - 1

        while va < target:
            up_ok = up <= mx
            dn_ok = dn >= mn
            if not up_ok and not dn_ok:
                break
            up_sum = (h.get(up, 0.0) + h.get(up + 1, 0.0)) if up_ok else None
            dn_sum = (h.get(dn, 0.0) + h.get(dn - 1, 0.0)) if dn_ok else None

            if dn_sum is None:
                go_up = True
            elif up_sum is None:
                go_up = False
            elif up_sum > dn_sum:
                go_up = True
            elif dn_sum > up_sum:
                go_up = False
            else:
                go_up = self.va_tiebreak == "up"

            if go_up:
                va += h.get(up, 0.0)
                hi = up
                if up + 1 <= mx:
                    va += h.get(up + 1, 0.0)
                    hi = up + 1
                    up += 2
                else:
                    up += 1
            else:
                va += h.get(dn, 0.0)
                lo = dn
                if dn - 1 >= mn:
                    va += h.get(dn - 1, 0.0)
                    lo = dn - 1
                    dn -= 2
                else:
                    dn -= 1
        return lo, hi

    def _row_vols(self) -> list[float]:
        mn, mx = self._min_row, self._max_row
        h = self._hist
        return [h.get(r, 0.0) for r in range(mn, mx + 1)]

    def _nodes(self):
        """Return (hvn_rows, lvn_rows) — sorted absolute row indices."""
        mn, mx = self._min_row, self._max_row
        if mn is None or mx is None:
            return [], []
        vols = self._row_vols()
        max_v = max(vols)
        occupied = [v for v in vols if v > 0.0]
        mean_v = sum(occupied) / len(occupied) if occupied else 0.0
        if max_v <= 0.0:
            return [], []

        hvn_thresh = self.hvn_frac * max_v
        peaks: list[int] = []
        n = len(vols)
        for i, v in enumerate(vols):
            if v < hvn_thresh:
                continue
            left = vols[i - 1] if i > 0 else -1.0
            right = vols[i + 1] if i < n - 1 else -1.0
            if v >= left and v >= right:
                peaks.append(mn + i)
        hvn_rows = self._suppress_adjacent(peaks)

        lvn_rows: list[int] = []
        if len(hvn_rows) >= 2:
            lvn_thresh = self.lvn_frac * mean_v
            for a, b in zip(hvn_rows, hvn_rows[1:]):
                seg = range(a - mn + 1, b - mn)  # rows strictly between two HVNs
                if not seg:
                    continue
                jmin = min(seg, key=lambda j: vols[j])
                if vols[jmin] <= lvn_thresh:
                    lvn_rows.append(mn + jmin)
        return hvn_rows, lvn_rows

    def _suppress_adjacent(self, rows: list[int]) -> list[int]:
        """Collapse runs of consecutive peak rows to the single heaviest row."""
        if not rows:
            return []
        h = self._hist
        out: list[int] = []
        run = [rows[0]]
        for r in rows[1:]:
            if r == run[-1] + 1:
                run.append(r)
            else:
                out.append(max(run, key=lambda x: (h.get(x, 0.0), -x)))
                run = [r]
        out.append(max(run, key=lambda x: (h.get(x, 0.0), -x)))
        return out

    def _classify_shape(self) -> Shape:
        poc = self._poc_row()
        va = self._value_area()
        mn, mx = self._min_row, self._max_row
        if poc is None or va is None or mn is None or mx == mn:
            return Shape.NONE

        vols = self._row_vols()
        max_v = max(vols)
        occupied = [v for v in vols if v > 0.0]
        mean_v = sum(occupied) / len(occupied) if occupied else 0.0
        peakiness = (max_v / mean_v) if mean_v > 0 else math.inf

        # I: no dominant node — volume spread ~uniformly (trend / unfair day)
        if peakiness < self.i_max_peak:
            return Shape.I

        hvn, lvn = self._nodes()
        # B: two distinct HVN clusters separated by an LVN
        if len(hvn) >= 2 and len(lvn) >= 1:
            return Shape.B

        span = mx - mn
        pos = (poc - mn) / span  # 0 = bottom of range, 1 = top
        if pos >= 0.66:
            return Shape.P
        if pos <= 0.34:
            return Shape.b
        return Shape.D
