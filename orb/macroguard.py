"""MacroGuard — the live bot's read-only consumer of the macro "second brain".

A separate sidecar (the ``macro/`` package) writes a single ``macro_state.json``;
each per-symbol ``orb live`` process reads it through a :class:`MacroGuard` and
turns it into entry vetoes / qty scaling / risk-off decisions, **without the pure
engine ever importing this module**. Same orchestration-layer pattern as the
``trueopen`` / ``quarter`` / ``DailyLossBreaker`` filters in ``cli.py``.

This module is **stdlib-only and pure** (no network, no third-party deps): it only
reads + parses a local JSON file, so it unit-tests by injecting a state file and a
clock. The heavy fetching/scoring lives in the ``macro/`` sidecar, which may use
third-party deps and never gets imported here.

``macro_state.json`` schema (v1)::

    {
      "schema_version": 1,
      "generated_at": "2026-06-16T10:40:00Z",   # ISO-8601 UTC
      "ttl_sec": 300,                            # state older than this == stale
      "global": {
        "risk_regime": "risk_on | neutral | risk_off",
        "risk_score": -0.42,                     # [-1..+1], +=risk-on
        "confidence": 0.78,                      # [0..1]
        "blackout": {"active": false, "until": null, "reason": null}
      },
      "events": [ ... ],                          # forward calendar (sidecar use)
      "assets": {
        "XAUUSD": {"bias":"bullish","score":0.55,"horizon":"intraday","drivers":[...]},
        ...
      }
    }

Fail-safe principle: a missing/stale/corrupt brain **never** opens a position and
never (under the default ``allow`` policy) blocks one — worst case is today's
behavior. See DECISIONS D-013.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Direction, Signal, SignalKind

log = logging.getLogger("orb.macroguard")

SCHEMA_VERSION = 1
_EPS = 1e-9


# --------------------------------------------------------------------------- #
# Parsed state model (defensive; tolerant of missing keys)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Blackout:
    active: bool = False
    until: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AssetView:
    bias: str = "neutral"
    score: float = 0.0          # [-1..+1], + = bullish
    horizon: str = "intraday"
    drivers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MacroState:
    schema_version: int
    generated_at: datetime      # tz-aware UTC
    ttl_sec: int
    risk_regime: str
    risk_score: float
    confidence: float
    blackout: Blackout
    assets: dict[str, AssetView] = field(default_factory=dict)

    @staticmethod
    def _parse_ts(raw: str) -> datetime:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError(f"generated_at not tz-aware: {raw!r}")
        return dt.astimezone(timezone.utc)

    @classmethod
    def from_dict(cls, d: dict) -> "MacroState":
        """Parse a raw JSON dict. Raises ValueError/KeyError/TypeError on
        anything structurally wrong; the caller treats that as a corrupt file."""
        g = d.get("global", {})
        b = g.get("blackout", {}) or {}
        assets: dict[str, AssetView] = {}
        for key, av in (d.get("assets", {}) or {}).items():
            assets[str(key).upper()] = AssetView(
                bias=str(av.get("bias", "neutral")),
                score=float(av.get("score", 0.0)),
                horizon=str(av.get("horizon", "intraday")),
                drivers=tuple(av.get("drivers", []) or ()),
            )
        return cls(
            schema_version=int(d.get("schema_version", 0)),
            generated_at=cls._parse_ts(str(d["generated_at"])),
            ttl_sec=int(d.get("ttl_sec", 0)),
            risk_regime=str(g.get("risk_regime", "neutral")),
            risk_score=float(g.get("risk_score", 0.0)),
            confidence=float(g.get("confidence", 0.0)),
            blackout=Blackout(
                active=bool(b.get("active", False)),
                until=b.get("until"),
                reason=b.get("reason"),
            ),
            assets=assets,
        )

    def age_sec(self, now: datetime) -> float:
        return (now - self.generated_at).total_seconds()

    def is_stale(self, now: datetime) -> bool:
        return self.ttl_sec <= 0 or self.age_sec(now) > self.ttl_sec


# --------------------------------------------------------------------------- #
# Decision returned to the live loop
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Decision:
    action: str            # "ALLOW" | "VETO"
    qty: float | None      # possibly-scaled lot size (pass-through in M0)
    reason: str


# --------------------------------------------------------------------------- #
# Pure decision logic (shared by the live MacroGuard and the M6 backtest harness)
# --------------------------------------------------------------------------- #
def bare_key(symbol: str) -> str:
    """Broker symbol (``XAUUSD.ecn``) -> bare MacroState asset key (``XAUUSD``)."""
    return symbol.split(".")[0].upper()


def _stale_decision(qty, default_when_stale: str, tag: str) -> Decision:
    if default_when_stale == "block":
        return Decision("VETO", qty, f"{tag}_block")
    return Decision("ALLOW", qty, f"{tag}_allow")


def decide_entry(state: "MacroState | None", asset_key: str, direction, qty, now,
                 conf_min: float = 0.6, default_when_stale: str = "allow") -> Decision:
    """Pure entry decision over a (possibly None) MacroState. Drives the live guard
    and the backtest harness (which reconstructs state per bar)."""
    if state is None:
        return _stale_decision(qty, default_when_stale, "macro_absent")
    if state.is_stale(now):
        return _stale_decision(qty, default_when_stale, "macro_stale")
    if state.blackout.active:
        return Decision("VETO", qty, f"blackout:{state.blackout.reason or 'unknown'}")
    asset = state.assets.get(asset_key)
    if asset is not None and abs(asset.score) > _EPS and state.confidence >= conf_min:
        bullish = asset.score > 0
        opposes = ((direction is Direction.LONG and not bullish)
                   or (direction is Direction.SHORT and bullish))
        if opposes:
            return Decision("VETO", qty,
                            f"macro_bias_conflict score={asset.score:+.2f} "
                            f"conf={state.confidence:.2f}")
    bias = asset.bias if asset is not None else "none"
    return Decision("ALLOW", qty, f"macro_ok regime={state.risk_regime} bias={bias}")


def decide_risk_off(state: "MacroState | None", now) -> tuple[bool, str]:
    """Pure risk-off decision: True only on an active hard blackout (scheduled
    window or confirmed war_spike). Missing/stale -> False."""
    if state is None or state.is_stale(now):
        return False, ""
    if state.blackout.active:
        return True, f"blackout:{state.blackout.reason or 'unknown'}"
    return False, ""


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
class MacroGuard:
    """Reads ``state_path`` and decides whether a per-symbol bot may enter.

    Pure consumer: the only side effect is reading a file (cached on mtime).
    Constructed once per ``orb live`` process with that process's ``symbol``.
    """

    def __init__(self, symbol: str, state_path: str,
                 default_when_stale: str = "allow", conf_min: float = 0.6,
                 now_fn=None) -> None:
        if default_when_stale not in ("allow", "block"):
            raise ValueError("default_when_stale must be 'allow' or 'block'")
        # Bot symbols carry a broker suffix (XAUUSD.ecn); state keys are bare.
        self.asset_key = bare_key(symbol)
        self.state_path = state_path
        self.default_when_stale = default_when_stale
        self.conf_min = conf_min
        self._now_fn = now_fn
        self._cached_mtime_ns: int | None = None
        self._cached_state: MacroState | None = None

    def _now(self) -> datetime:
        return self._now_fn() if self._now_fn is not None else datetime.now(timezone.utc)

    # --- state access ----------------------------------------------------- #
    def read(self) -> MacroState | None:
        """Return the current parsed state, or None if the file is missing.

        Missing file  -> None (sidecar down: degrade to today's behavior).
        Corrupt parse -> last-good state if we have one, else None (never crash).
        Unchanged file (same mtime) -> cached parse, no re-read.
        """
        try:
            st = os.stat(self.state_path)
        except OSError:
            return None  # missing: treat as absent, not as last-good
        if (self._cached_mtime_ns == st.st_mtime_ns
                and self._cached_state is not None):
            return self._cached_state
        try:
            with open(self.state_path, encoding="utf-8") as f:
                raw = json.load(f)
            state = MacroState.from_dict(raw)
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("macro_state parse failed (%s): keeping last good state", e)
            return self._cached_state
        self._cached_mtime_ns = st.st_mtime_ns
        self._cached_state = state
        return state

    # --- entry gate (hook A: cli.py::on_signal) --------------------------- #
    def evaluate_entry(self, sig: Signal) -> Decision:
        """Decide an ENTRY signal. Only call for SignalKind.ENTRY."""
        return decide_entry(self.read(), self.asset_key, sig.direction, sig.qty,
                            self._now(), self.conf_min, self.default_when_stale)

    # --- risk-off (hook B: cli.py::on_bar, guard mode only) --------------- #
    def risk_off_now(self) -> tuple[bool, str]:
        """True only on an active hard blackout (scheduled FOMC/CPI/NFP window or a
        confirmed ``war_spike``). A soft ``risk_off`` regime never closes an open
        position — it only tilts bias / vetoes new entries. Missing/stale -> False."""
        return decide_risk_off(self.read(), self._now())
