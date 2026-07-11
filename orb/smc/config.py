"""SmcConfig — all tunable parameters for the SMC (Smart Money Concepts) strategy.

Separate from OrbConfig/SvpConfig so the existing engines stay byte-for-byte
untouched. Frozen and validated at construction, mirroring
:class:`orb.svp.config.SvpConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import ConfigError


class SmcConfigError(ConfigError, ValueError):
    """Invalid SmcConfig. Fatal at construction time.

    Inherits ValueError so callers may catch either the repo hierarchy
    (:class:`orb.models.ConfigError`) or plain ``ValueError``.
    """


@dataclass(frozen=True, slots=True)
class SmcConfig:
    # timeframes (minutes)
    trigger_tf_min: int = 30
    htf_min: int = 240
    d1_min: int = 1440

    # structure (swing detection)
    swing_lookback: int = 2          # fractal half-window
    max_swings: int = 50             # deque bound

    # displacement / order blocks
    disp_body_frac: float = 0.5      # body/range minimum for a displacement bar
    disp_atr_mult: float = 1.2       # range vs ATR minimum
    ob_confirm_bars: int = 10        # bars allowed between OB and its BOS
    max_blocks: int = 20             # deque bound per side
    ob_expiry_bars: int = 180        # drop untouched blocks after N trigger bars
    atr_period: int = 14

    # POC / profile confluence
    poc_tol: float = 2.0             # price distance counted as "at POC"
    ticks_per_row: int = 100
    tick_size: float = 0.01
    min_profile_bars: int = 60

    # confluence scoring
    min_confluences: int = 3         # 1..6 checks required to fire
    vol_mult: float = 1.5            # entry volume vs SMA
    vol_sma_period: int = 20

    # risk / entries
    risk_pct: float = 2.0            # % equity risked per trade, (0, 10]
    stop_buffer: float = 0.5         # beyond the OB extreme
    stop_max_dist: float = 15.0      # reject entries with wider stops
    max_trades_per_day: int = 2

    # exits (ladder): ((r_multiple, close_fraction), ...) ascending in r
    partial_levels: tuple = ((5.0, 0.40), (7.0, 0.30))
    final_tp_r: float = 10.0         # 0 = no final TP (runner trails out)

    # two-stage discrete SL (max 2 modifications per position lifetime,
    # both tighten-only; confirmed on the N+1 CLOSED trigger-TF candle — see
    # LadderExitManager). Stage 1 = breakeven + round-trip costs. Stage 2 =
    # final profit lock at candle N's low/high (floored), then frozen forever.
    stage1_at_r: float = 1.0         # BE+costs trigger (candle N close >= this R)
    stage2_at_r: float = 2.0         # final-lock trigger (candle N close >= this R)
    stage2_min_lock_r: float = 1.0   # stage2 SL floor, in R, never looser than this
    comm_per_lot: float = 7.0        # $ round-trip commission/lot (XAUUSD ~ $0.07 @ $100/move)

    # plumbing
    strict_monotonic: bool = True
    instrument_dp: int = 2

    def __post_init__(self) -> None:
        for nm in ("trigger_tf_min", "htf_min", "d1_min", "swing_lookback",
                   "max_swings", "ob_confirm_bars", "max_blocks",
                   "ob_expiry_bars", "atr_period", "ticks_per_row",
                   "min_profile_bars", "vol_sma_period", "max_trades_per_day"):
            if getattr(self, nm) < 1:
                raise SmcConfigError(f"{nm} must be >= 1")
        if self.tick_size <= 0:
            raise SmcConfigError("tick_size must be > 0")
        if self.poc_tol < 0:
            raise SmcConfigError("poc_tol must be >= 0")
        if not (0.0 < self.disp_body_frac < 1.0):
            raise SmcConfigError("disp_body_frac must be in (0, 1)")
        for nm in ("disp_atr_mult", "vol_mult"):
            if getattr(self, nm) <= 0:
                raise SmcConfigError(f"{nm} must be > 0")
        if not (0.0 < self.risk_pct <= 10.0):
            raise SmcConfigError("risk_pct must be in (0, 10]")
        if not (self.trigger_tf_min < self.htf_min < self.d1_min):
            raise SmcConfigError("timeframes must satisfy trigger_tf_min < htf_min < d1_min")
        if self.d1_min != 1440:
            raise SmcConfigError("d1_min must be 1440 (daily)")
        prev_r = 0.0
        frac_sum = 0.0
        for lvl in self.partial_levels:
            if len(lvl) != 2:
                raise SmcConfigError(f"partial level must be (r, frac): {lvl}")
            r, frac = lvl
            if r <= prev_r:
                raise SmcConfigError("partial_levels r must be > 0 and strictly ascending")
            if not (0.0 < frac < 1.0):
                raise SmcConfigError("partial_levels frac must be in (0, 1)")
            prev_r = r
            frac_sum += frac
        if frac_sum > 1.0:
            raise SmcConfigError("partial_levels fractions must sum to <= 1.0")
        if self.final_tp_r != 0.0 and self.final_tp_r <= prev_r:
            raise SmcConfigError("final_tp_r must be 0 (off) or > the last partial r")
        if self.final_tp_r < 0:
            raise SmcConfigError("final_tp_r must be >= 0")
        if self.stage1_at_r <= 0:
            raise SmcConfigError("stage1_at_r must be > 0")
        if self.stage2_at_r <= self.stage1_at_r:
            raise SmcConfigError("stage2_at_r must be > stage1_at_r")
        if self.stage2_min_lock_r <= 0:
            raise SmcConfigError("stage2_min_lock_r must be > 0")
        if self.comm_per_lot < 0:
            raise SmcConfigError("comm_per_lot must be >= 0")
        if self.stop_buffer <= 0:
            raise SmcConfigError("stop_buffer must be > 0")
        if self.stop_max_dist <= self.stop_buffer:
            raise SmcConfigError("stop_max_dist must be > stop_buffer")
        if not (1 <= self.min_confluences <= 6):
            raise SmcConfigError("min_confluences must be in 1..6")

    @property
    def row_size(self) -> float:
        return self.ticks_per_row * self.tick_size
