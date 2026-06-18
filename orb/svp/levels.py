"""Immutable SVP profile snapshots + prior-session carryover.

Pure data types shared by the profile accumulator and the strategy. No logic,
no imports from :mod:`profile` (keeps the dependency one-directional and avoids
an import cycle: ``profile`` and ``strategy`` import from here).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Shape(enum.Enum):
    """Profile morphology (auction-theory day types).

    D  balanced / normal  -> Edge Rotation eligible (fade VA edges to POC)
    P  bullish distribution (POC upper third, thin tail below)
    b  bearish distribution (POC lower third, thin tail above)
    B  double distribution (two HVN clusters split by an LVN) -> LVN setup
    I  thin trend / unfair (no dominant node) -> suppress mean-reversion
    NONE  not enough structure to classify
    """

    D = "D"
    P = "P"
    b = "b"
    B = "B"
    I = "I"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class ProfileLevels:
    """A frozen snapshot of a :class:`VolumeProfile` at one point in time.

    ``poc``/``vah``/``val`` are the CENTER prices of the corresponding profile
    rows. ``hvns``/``lvns`` are sorted center prices of the high/low-volume nodes.
    """

    poc: float
    vah: float
    val: float
    hvns: tuple[float, ...]
    lvns: tuple[float, ...]
    shape: Shape
    total_volume: float


@dataclass(frozen=True, slots=True)
class PriorProfile:
    """Yesterday's completed profile, carried into today as reference levels."""

    session_id: str
    poc: float
    vah: float
    val: float
    shape: Shape
