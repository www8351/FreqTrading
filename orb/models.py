"""Data models, enums, exceptions, and candle validation for the ORB engine.

Pure module: no I/O, no asyncio, no third-party deps. Everything downstream
(engine, indicators, session) depends on the symbols defined here.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from datetime import datetime, time


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class OrbError(Exception):
    """Base class for all ORB engine errors."""


class ConfigError(OrbError):
    """Invalid OrbConfig. Fatal at construction time."""


class CandleError(OrbError):
    """Malformed candle (NaN/non-finite, OHLC insanity, naive timestamp)."""


class OutOfOrderError(OrbError):
    """Candle timestamp <= previous under strict_monotonic. Fatal per bar."""


class IndicatorError(OrbError):
    """Indicator fed corrupt input (NaN) or hit an impossible divisor."""


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class State(enum.Enum):
    IDLE = "IDLE"                    # pre-session / between sessions; no range
    RANGE_DEFINED = "RANGE_DEFINED"  # OR building/locked + armed; awaiting breakout
    BREAKOUT = "BREAKOUT"            # simulated position open, trailing active
    EXIT = "EXIT"                    # transient; resolved same call -> IDLE/RANGE_DEFINED


class Direction(enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(enum.Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAIL_STOP = "TRAIL_STOP"
    BROKER_CLOSED = "BROKER_CLOSED"  # server-side SL/TP filled; engine syncs
    RANGE_REENTRY = "RANGE_REENTRY"
    SESSION_END = "SESSION_END"
    GAP_INVALIDATE = "GAP_INVALIDATE"


class SignalKind(enum.Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REJECT = "REJECT"  # breakout close but momentum gate failed (diagnostic)


# --------------------------------------------------------------------------- #
# Candle
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Candle:
    """A single closed 1m OHLCV bar. ``ts`` is the bar OPEN time, tz-aware UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def validate(c: Candle) -> None:
    """Raise :class:`CandleError` if the candle is structurally invalid.

    Called by the engine before any state logic so corrupt market data can never
    drive a trading decision. Validation lives here (not in ``__post_init__``) so
    the caller can attach row/index context to the failure.
    """
    if c.ts.tzinfo is None:
        raise CandleError(f"naive timestamp (tz-aware UTC required): {c.ts!r}")
    if not _finite(c.open, c.high, c.low, c.close):
        raise CandleError(f"non-finite OHLC at {c.ts}: {c}")
    if not (isinstance(c.volume, (int, float)) and math.isfinite(c.volume)):
        raise CandleError(f"non-finite volume at {c.ts}: {c.volume!r}")
    if c.volume < 0:
        raise CandleError(f"negative volume at {c.ts}: {c.volume}")
    if c.high < c.low:
        raise CandleError(f"high<low at {c.ts}: high={c.high} low={c.low}")
    if c.high < max(c.open, c.close):
        raise CandleError(f"high<max(open,close) at {c.ts}: {c}")
    if c.low > min(c.open, c.close):
        raise CandleError(f"low>min(open,close) at {c.ts}: {c}")


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class OrbConfig:
    """All tunable parameters. Frozen; validated in ``__post_init__``."""

    # session
    session_open_utc: time = time(0, 0)   # Asian open: Tokyo 09:00 JST == 00:00 UTC
    session_len_min: int = 480            # 8h window; engine forces exit at close

    # opening range
    range_minutes: int = 5                # lock OR over first N closed bars; arm on N+1

    # momentum gate (both must pass when enabled)
    use_roc: bool = True
    roc_period: int = 5
    roc_min: float = 0.05                 # pct; long needs >=+roc_min, short <=-roc_min
    use_rvol: bool = False                # off by default: XAU spot volume unreliable
    rvol_period: int = 20
    rvol_min: float = 1.5

    # exit: ATR trail
    atr_period: int = 14
    atr_mult: float = 1.5                 # k
    stop_max_dist: float | None = None    # hard cap on stop distance in price
                                          # units (gold: 4.0 == 40 pips)
    stop_min_dist: float | None = None    # hard floor: trail never chokes the
                                          # trade tighter than this (2.0 == 20p)

    # exit: range re-entry
    reentry_on: str = "close"             # "close" | "intrabar"

    # position sizing / fixed take-profit
    qty: float = 0.0                      # lot size attached to signals (0 = omit)
    tp_rrr: float | None = None           # TP at rrr x initial risk (None = no TP)
    tp_close_frac: float = 1.0            # fraction closed at TP; <1 leaves the
                                          # remainder running on the trail stop

    # direction
    allow_long: bool = True
    allow_short: bool = True

    # session trade policy
    one_trade_per_session: bool = True
    rearm_after_exit: bool = False        # consulted only when one_trade_per_session False
    rearm_range: str = "rebuild"          # "rebuild": fresh N-bar range after exit
                                          # "keep": reuse session opening range

    # data hygiene
    expected_bar_sec: int = 60
    max_gap_bars: int = 3
    on_gap: str = "hold"                  # "hold" | "reset"
    strict_monotonic: bool = True

    # output
    instrument_dp: int = 2

    def __post_init__(self) -> None:
        if self.range_minutes < 1:
            raise ConfigError("range_minutes must be >= 1")
        for name in ("roc_period", "rvol_period", "atr_period"):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be >= 1")
        if self.atr_mult <= 0:
            raise ConfigError("atr_mult must be > 0")
        if self.stop_max_dist is not None and self.stop_max_dist <= 0:
            raise ConfigError("stop_max_dist must be > 0")
        if self.stop_min_dist is not None and self.stop_min_dist <= 0:
            raise ConfigError("stop_min_dist must be > 0")
        if (self.stop_min_dist is not None and self.stop_max_dist is not None
                and self.stop_min_dist > self.stop_max_dist):
            raise ConfigError("stop_min_dist must be <= stop_max_dist")
        if self.qty < 0:
            raise ConfigError("qty must be >= 0")
        if self.tp_rrr is not None and self.tp_rrr <= 0:
            raise ConfigError("tp_rrr must be > 0")
        if not (0.0 < self.tp_close_frac <= 1.0):
            raise ConfigError("tp_close_frac must be in (0, 1]")
        if self.rearm_range not in ("rebuild", "keep"):
            raise ConfigError("rearm_range must be 'rebuild' or 'keep'")
        if self.reentry_on not in ("close", "intrabar"):
            raise ConfigError("reentry_on must be 'close' or 'intrabar'")
        if self.on_gap not in ("hold", "reset"):
            raise ConfigError("on_gap must be 'hold' or 'reset'")
        if not (self.allow_long or self.allow_short):
            raise ConfigError("at least one of allow_long/allow_short must be True")
        if self.session_len_min <= self.range_minutes:
            raise ConfigError("session_len_min must exceed range_minutes")
        if self.expected_bar_sec < 1:
            raise ConfigError("expected_bar_sec must be >= 1")
        if self.max_gap_bars < 0:
            raise ConfigError("max_gap_bars must be >= 0")

    @property
    def warmup_bars(self) -> int:
        """Bars needed before the engine may arm (max of all enabled indicators)."""
        need = [self.range_minutes]
        if self.use_roc:
            need.append(self.roc_period + 1)
        if self.use_rvol:
            need.append(self.rvol_period)
        need.append(self.atr_period + 1)  # ATR is always used (trailing stop)
        return max(need)


# --------------------------------------------------------------------------- #
# Output records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Signal:
    ts: datetime
    kind: SignalKind
    direction: Direction | None
    price: float
    state_from: State
    state_to: State
    reason: str
    range_high: float | None = None
    range_low: float | None = None
    roc: float | None = None
    rvol: float | None = None
    atr: float | None = None
    stop: float | None = None
    tp: float | None = None
    qty: float | None = None
    bars_held: int | None = None


@dataclass(frozen=True, slots=True)
class StateTransition:
    ts: datetime
    state_from: State
    state_to: State
    event: str
    detail: str = ""


@dataclass(slots=True)
class PositionState:
    direction: Direction
    entry_ts: datetime
    entry_price: float
    stop: float
    atr_at_entry: float
    tp: float | None = None
    bars_held: int = 0
    mfe: float = 0.0   # max favorable excursion (diagnostic)
    mae: float = 0.0   # max adverse excursion (diagnostic)
