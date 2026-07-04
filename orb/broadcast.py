"""Non-blocking copy-trade broadcaster (stdlib only).

Ships trade-event payloads to a leader node over HTTP POST from a daemon
worker thread. The trading loop only ever touches :meth:`Broadcaster.publish`,
which is a ``put_nowait`` — it can NEVER block or raise into the caller. On
queue overflow the OLDEST event is dropped (counted in ``dropped``). Any send
failure (exception or non-2xx) appends the payload to a JSONL disk spool;
once connectivity recovers the spool drains oldest-first so the leader
receives events in original publish order.

Wire contract (shared with the leader node and the Part 1 EA):
``X-Timestamp: <unix-seconds>`` and
``X-Signature: hex(hmac_sha256(secret, "<ts>." + raw_body))``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
import urllib.request
from typing import Callable

log = logging.getLogger("orb.broadcast")


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """Hex HMAC-SHA256 over ``<timestamp>.<body>`` — the copy-trade wire pin."""
    return hmac.new(secret, timestamp.encode() + b"." + body,
                    hashlib.sha256).hexdigest()


class Broadcaster:
    """Fire-and-forget HTTP publisher with disk spool fallback.

    ``opener`` / ``now_fn`` / ``sleep_fn`` are injectable for tests (no real
    sockets needed). Defaults: ``urllib.request.urlopen`` / ``time.time`` /
    ``time.sleep``.
    """

    def __init__(self, url: str, secret: bytes, *,
                 spool_path: str = "broadcast_spool.jsonl",
                 timeout: float = 3.0,
                 max_queue: int = 1000,
                 opener: Callable | None = None,
                 now_fn: Callable[[], float] | None = None,
                 sleep_fn: Callable[[float], None] | None = None) -> None:
        self.url = url
        self.spool_path = spool_path
        self.timeout = timeout
        self.dropped = 0
        self.sent = 0
        self.spooled = 0
        self._secret = secret
        self._opener = opener or urllib.request.urlopen
        self._now_fn = now_fn or time.time
        self._sleep_fn = sleep_fn or time.sleep  # reserved for backoff pacing
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name="broadcast-worker", daemon=True)
        self._thread.start()

    # -- producer side (trading loop) ------------------------------------

    def publish(self, payload: dict) -> None:
        """Enqueue a payload; NEVER blocks. Full queue drops the oldest."""
        try:
            self._q.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()  # drop oldest
        except queue.Empty:
            pass
        self.dropped += 1
        try:
            self._q.put_nowait(payload)
        except queue.Full:  # raced full again — drop the new payload too
            self.dropped += 1
        log.warning("broadcast_queue_full dropped=%d", self.dropped)

    def close(self, drain_sec: float = 2.0) -> None:
        """Signal stop and join the worker, bounded — never hangs."""
        self._stop.set()
        self._thread.join(timeout=drain_sec)
        log.info("broadcast_closed sent=%d spooled=%d dropped=%d",
                 self.sent, self.spooled, self.dropped)

    # -- worker side ------------------------------------------------------

    def _run(self) -> None:
        while True:
            try:
                payload = self._q.get(timeout=0.05)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            try:
                self._handle(payload)
            except Exception as exc:  # worker must never die
                log.error("broadcast_worker_error err=%s", exc)

    def _handle(self, payload: dict) -> None:
        if self._spool_backlog():
            # backlog exists: route through the spool so the leader still
            # receives events in original publish order
            self._spool(payload)
            self._drain_spool()
        elif self._send(payload):
            self._drain_spool()
        else:
            self._spool(payload)

    def _send(self, payload: dict) -> bool:
        body = json.dumps(payload).encode("utf-8")
        ts = str(int(self._now_fn()))
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Timestamp": ts,
                     "X-Signature": sign(self._secret, ts, body)})
        try:
            resp = self._opener(req, timeout=self.timeout)
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            if not 200 <= int(status) < 300:
                log.warning("broadcast_http_error status=%s url=%s",
                            status, self.url)
                return False
        except Exception as exc:
            log.warning("broadcast_send_failed err=%s url=%s", exc, self.url)
            return False
        self.sent += 1
        return True

    def _spool_backlog(self) -> bool:
        try:
            return (os.path.exists(self.spool_path)
                    and os.path.getsize(self.spool_path) > 0)
        except OSError:
            return False

    def _spool(self, payload: dict) -> None:
        try:
            with open(self.spool_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
            self.spooled += 1
        except OSError as exc:  # spool failure must not kill the worker
            log.error("broadcast_spool_failed err=%s path=%s",
                      exc, self.spool_path)

    def _drain_spool(self) -> None:
        """Replay spooled payloads oldest-first; re-spool the remainder on
        the first failure so order is preserved for the next attempt."""
        try:
            if not os.path.exists(self.spool_path):
                return
            with open(self.spool_path, "r", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            os.remove(self.spool_path)
        except OSError as exc:
            log.error("broadcast_spool_read_failed err=%s path=%s",
                      exc, self.spool_path)
            return
        for i, line in enumerate(lines):
            try:
                payload = json.loads(line)
            except ValueError:
                log.error("broadcast_spool_corrupt_line_dropped path=%s",
                          self.spool_path)
                continue
            if not self._send(payload):
                try:
                    with open(self.spool_path, "a", encoding="utf-8") as fh:
                        for ln in lines[i:]:
                            fh.write(ln + "\n")
                except OSError as exc:
                    log.error("broadcast_respool_failed err=%s lost=%d",
                              exc, len(lines) - i)
                return
        log.info("broadcast_spool_drained n=%d", len(lines))
