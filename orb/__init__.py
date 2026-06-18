"""ORB scalping + momentum-validation state engine for XAU/USD 1m candles.

Public surface:
    OrbEngine        synchronous state machine (on_candle / replay / reset)
    OrbConfig        all tunable parameters
    Candle           input OHLCV bar
    Signal           ENTRY / EXIT / REJECT output
    StateTransition  emitted on every state edge
    State            engine state enum
    CandleStream     async live wrapper (orb.stream)
"""

from .engine import OrbEngine
from .indicators import ROC, VolumeSMA, WilderATR
from .models import (
    Candle,
    CandleError,
    ConfigError,
    Direction,
    ExitReason,
    IndicatorError,
    OrbConfig,
    OrbError,
    OutOfOrderError,
    PositionState,
    Signal,
    SignalKind,
    State,
    StateTransition,
    validate,
)
from .session import SessionClock, Zone
from .stream import CandleStream

__all__ = [
    "OrbEngine", "OrbConfig", "Candle", "Signal", "StateTransition", "State",
    "Direction", "ExitReason", "SignalKind", "PositionState", "validate",
    "OrbError", "ConfigError", "CandleError", "OutOfOrderError", "IndicatorError",
    "SessionClock", "Zone", "CandleStream", "WilderATR", "ROC", "VolumeSMA",
]

__version__ = "0.1.0"
