"""Session window classification for the Asian-open ORB.

A session spans ``[open, open + session_len_min)`` keyed by the UTC date on which
that open occurs. The engine uses ``session_id`` to detect a new session (and
reset), and the zone to know whether a bar builds the range, trades, or is out
of session.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import OrbConfig


class Zone(enum.Enum):
    BEFORE = "BEFORE"                    # before today's session open
    IN_RANGE_WINDOW = "IN_RANGE_WINDOW"  # [open, open+range_minutes)
    IN_SESSION = "IN_SESSION"            # [open+range_minutes, open+len)
    AFTER = "AFTER"                      # >= open+len


@dataclass(frozen=True, slots=True)
class SessionInfo:
    zone: Zone
    session_id: str  # ISO date of the session open this bar belongs to


class SessionClock:
    """Maps a candle timestamp to its (zone, session_id)."""

    def __init__(self, config: OrbConfig) -> None:
        self._open = config.session_open_utc
        self._range_min = config.range_minutes
        self._len_min = config.session_len_min

    def classify(self, ts: datetime) -> SessionInfo:
        """Classify ``ts`` (tz-aware UTC) into a zone + owning session id.

        A bar before today's open belongs to the PREVIOUS calendar day's session
        if it still falls inside that session's window (handles sessions that
        cross midnight UTC); otherwise it is BEFORE today's open.
        """
        today_open = datetime.combine(ts.date(), self._open, tzinfo=ts.tzinfo)
        end = today_open + timedelta(minutes=self._len_min)

        if ts < today_open:
            # Could still belong to yesterday's session if it crosses midnight.
            prev_open = today_open - timedelta(days=1)
            prev_end = prev_open + timedelta(minutes=self._len_min)
            if prev_open <= ts < prev_end:
                return self._zone_within(ts, prev_open)
            return SessionInfo(Zone.BEFORE, today_open.date().isoformat())

        if ts >= end:
            return SessionInfo(Zone.AFTER, today_open.date().isoformat())

        return self._zone_within(ts, today_open)

    def _zone_within(self, ts: datetime, open_dt: datetime) -> SessionInfo:
        sid = open_dt.date().isoformat()
        range_end = open_dt + timedelta(minutes=self._range_min)
        if ts < range_end:
            return SessionInfo(Zone.IN_RANGE_WINDOW, sid)
        return SessionInfo(Zone.IN_SESSION, sid)
