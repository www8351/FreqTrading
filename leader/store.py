"""Append-only JSONL event store with monotonic seq numbers.

One line = one event payload (dict) with a store-assigned ``"seq"`` field.
The line format is identical to the trade log / HTTP body / ZMQ frame per
docs/copytrade_schema.md. Seq numbering resumes from the highest seq already
present in the file, so restarts never reuse or reset sequence numbers.
"""

from __future__ import annotations

import json
import logging
import threading

log = logging.getLogger("leader.store")


class LeaderStore:
    """Thread-safe JSONL sink; ``ThreadingHTTPServer`` handlers share one."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._seq = self._load_last_seq()

    def _load_last_seq(self) -> int:
        last = 0
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seq = json.loads(line).get("seq", 0)
                        if isinstance(seq, int) and seq > last:
                            last = seq
                    except (ValueError, AttributeError):
                        log.warning("store_corrupt_line_skipped path=%s",
                                    self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.error("store_read_failed err=%s path=%s", exc, self.path)
        return last

    def append(self, payload: dict) -> int:
        """Assign the next seq, persist one JSON line, return the seq."""
        with self._lock:
            self._seq += 1
            record = dict(payload)
            record["seq"] = self._seq
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            return self._seq

    def latest(self, n: int = 50) -> list[dict]:
        """Return the last ``n`` stored events, oldest-first."""
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            except FileNotFoundError:
                return []
            except OSError as exc:
                log.error("store_read_failed err=%s path=%s", exc, self.path)
                return []
        events: list[dict] = []
        for line in lines[-max(int(n), 0):]:
            try:
                events.append(json.loads(line))
            except ValueError:
                log.warning("store_corrupt_line_skipped path=%s", self.path)
        return events

    def count(self) -> int:
        """Highest seq assigned so far (== number of events ever stored)."""
        with self._lock:
            return self._seq
