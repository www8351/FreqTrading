"""macro/ — the "second brain" sidecar.

A standalone local process that fetches macro/fundamental data (economic
calendar, FRED actuals, GDELT, sentiment, market proxies), scores it, and writes
a single ``macro_state.json`` that every per-symbol ``orb live`` process reads via
``orb.macroguard.MacroGuard``.

This package is a SEPARATE process from the trading engine and MAY use
third-party deps (collectors/scorers in later milestones). The engine and its
``orb.macroguard`` consumer stay stdlib-only and never import this package.

M0 ships only the contract: the state writer + a CLI that emits a neutral
(no-veto) state. Collectors/scorer/blackout/daemon arrive in M1+.
"""

SCHEMA_VERSION = 1
DEFAULT_SYMBOLS = ("XAUUSD", "US100", "US500", "XAGUSD")
