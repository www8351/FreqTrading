"""Manual macro sensitivity table — event kind -> per-asset directional coefficient.

Each coefficient is multiplied by the *normalized surprise* (signed, [-1..+1],
``+`` = actual above forecast) to get that event's contribution to an asset's bias.
The signs encode classic macro priors:

- **CPI / PPI hot** (surprise +) -> USD up, real yields up -> gold & silver DOWN,
  equities DOWN (tighter-for-longer). Cool -> the opposite.
- **NFP strong** (surprise +) -> risk-on for equities (UP) but USD/yields up ->
  gold DOWN. Weak -> the opposite.
- **FOMC / rate** higher-than-expected (hawkish, surprise +) -> DOWN across gold +
  equities; dovish (surprise -) -> UP across.
- **GDP strong** -> equities UP, gold slightly DOWN.

These are PRIORS, not fitted values. D-013 / PLAN §8 Q4: start manual, calibrate
against backtest in M6. Tune here; the scorer reads this table only.
"""

from __future__ import annotations

# kind -> {bare asset key: coefficient in [-1..+1]}
SENSITIVITY: dict[str, dict[str, float]] = {
    "CPI":  {"XAUUSD": -1.0, "XAGUSD": -1.0, "US100": -0.8, "US500": -0.8},
    "PPI":  {"XAUUSD": -0.7, "XAGUSD": -0.7, "US100": -0.6, "US500": -0.6},
    "NFP":  {"XAUUSD": -0.6, "XAGUSD": -0.5, "US100": +0.7, "US500": +0.7},
    "FOMC": {"XAUUSD": -1.0, "XAGUSD": -0.9, "US100": -0.9, "US500": -0.9},
    "GDP":  {"XAUUSD": -0.3, "XAGUSD": -0.3, "US100": +0.6, "US500": +0.6},
    "JOBS": {"XAUUSD": +0.4, "XAGUSD": +0.3, "US100": -0.5, "US500": -0.5},  # unemployment rate up = weak
}
