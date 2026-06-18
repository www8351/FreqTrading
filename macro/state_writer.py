"""Atomic writer for ``macro_state.json`` + a neutral-state builder.

Atomic = write a temp file then ``os.replace`` it over the target, so a consumer
(``orb.macroguard.MacroGuard``) can never read a half-written file. Stdlib only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import DEFAULT_SYMBOLS, SCHEMA_VERSION


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def neutral_state(symbols=DEFAULT_SYMBOLS, ttl_sec: int = 300,
                  generated_at: str | None = None) -> dict:
    """A valid, no-veto MacroState: neutral regime, no blackout, flat per-asset.

    Lets consumers exercise the full read/parse path with a state that never
    blocks or scales a trade — the M0 default the sidecar emits until real
    collectors come online.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now_iso(),
        "ttl_sec": ttl_sec,
        "global": {
            "risk_regime": "neutral",
            "risk_score": 0.0,
            "confidence": 0.0,
            "blackout": {"active": False, "until": None, "reason": None},
        },
        "events": [],
        "assets": {
            sym: {"bias": "neutral", "score": 0.0,
                  "horizon": "intraday", "drivers": []}
            for sym in symbols
        },
    }


def write_state(state: dict, path: str) -> None:
    """Atomically write ``state`` as JSON to ``path`` (temp + os.replace + fsync)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
