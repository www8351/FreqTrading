"""SvpConfig — all tunable parameters for the SVP Edge-Rotation strategy.

Separate from OrbConfig so the ORB engine stays byte-for-byte untouched. Frozen
and validated at construction, mirroring :class:`orb.models.OrbConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from ..models import ConfigError


@dataclass(frozen=True, slots=True)
class SvpConfig:
    # session (a profile spans one session; default a full UTC day)
    session_open_utc: time = time(0, 0)
    session_len_min: int = 1440

    # profile grid
    ticks_per_row: int = 10
    tick_size: float = 0.01
    value_area_pct: float = 0.70
    va_tiebreak: str = "up"
    hvn_frac: float = 0.70
    lvn_frac: float = 0.30
    i_max_peak: float = 1.5
    min_session_bars: int = 20
    max_rows_per_bar: int = 5000
    # TPO fallback: weight each bar as 1 unit when it has no tick volume (for
    # backtesting history that lacks volume). Live uses real volume -> keep off.
    tpo_fallback: bool = False

    # entry setups
    enable_edge_rotation: bool = True
    enable_lvn: bool = False
    enable_absorption_proxy: bool = False
    # structural-stop buffer beyond the shelf, in ticks. Default 50 ($0.50 for
    # gold): a tight buffer is swept by the tagging wick itself (backtest: an
    # $0.08 buffer stopped out almost every fade). Tune per instrument.
    stop_buffer_ticks: float = 50.0

    # absorption proxy (off by default; tick volume cannot give true delta)
    absorb_lookback: int = 20
    absorb_vol_mult: float = 1.8
    absorb_body_ticks: float = 5.0
    absorb_wick_mult: float = 2.0

    # risk / sizing (applied downstream in cli.on_signal)
    risk_pct: float = 3.0

    # babysitter passthrough (exit management)
    partial_frac: float = 0.7
    partial_at_r: float = 2.0

    # session trade policy
    max_trades_per_session: int = 4
    allow_long: bool = True
    allow_short: bool = True

    # data hygiene
    expected_bar_sec: int = 60
    max_gap_bars: int = 3
    strict_monotonic: bool = True

    # output
    instrument_dp: int = 2

    def __post_init__(self) -> None:
        if self.ticks_per_row < 1:
            raise ConfigError("ticks_per_row must be >= 1")
        if self.tick_size <= 0:
            raise ConfigError("tick_size must be > 0")
        if not (0.0 < self.value_area_pct <= 1.0):
            raise ConfigError("value_area_pct must be in (0, 1]")
        if self.va_tiebreak not in ("up", "down"):
            raise ConfigError("va_tiebreak must be 'up' or 'down'")
        for nm in ("hvn_frac", "lvn_frac"):
            v = getattr(self, nm)
            if not (0.0 < v <= 1.0):
                raise ConfigError(f"{nm} must be in (0, 1]")
        if self.i_max_peak <= 1.0:
            raise ConfigError("i_max_peak must be > 1")
        if self.min_session_bars < 1:
            raise ConfigError("min_session_bars must be >= 1")
        if self.max_rows_per_bar < 1:
            raise ConfigError("max_rows_per_bar must be >= 1")
        if self.stop_buffer_ticks < 0:
            raise ConfigError("stop_buffer_ticks must be >= 0")
        if self.absorb_lookback < 1:
            raise ConfigError("absorb_lookback must be >= 1")
        if self.risk_pct <= 0:
            raise ConfigError("risk_pct must be > 0")
        if not (0.0 < self.partial_frac <= 1.0):
            raise ConfigError("partial_frac must be in (0, 1]")
        if self.partial_at_r <= 0:
            raise ConfigError("partial_at_r must be > 0")
        if self.max_trades_per_session < 1:
            raise ConfigError("max_trades_per_session must be >= 1")
        if not (self.allow_long or self.allow_short):
            raise ConfigError("at least one of allow_long/allow_short must be True")
        if self.session_len_min < 2:
            raise ConfigError("session_len_min must be >= 2")
        if self.expected_bar_sec < 1:
            raise ConfigError("expected_bar_sec must be >= 1")
        if self.max_gap_bars < 0:
            raise ConfigError("max_gap_bars must be >= 0")

    @property
    def row_size(self) -> float:
        return self.ticks_per_row * self.tick_size

    @property
    def buffer(self) -> float:
        return self.stop_buffer_ticks * self.tick_size
