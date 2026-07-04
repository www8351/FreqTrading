"""Leader-node HTTP server (stdlib ``http.server``, threading).

Endpoints:
- ``POST /events``          HMAC-authenticated ingest (see wire contract below)
- ``GET  /events/latest?n=`` last n stored events, oldest-first (default 50)
- ``GET  /health``           ``{"ok": true, "events": <count>}``

Wire contract (shared with ``orb.broadcast`` and the Part 1 EA):
``X-Signature = hex(hmac_sha256(secret, "<X-Timestamp>." + raw_body))``,
verified with ``hmac.compare_digest``; ``|now - X-Timestamp| <= max_skew``
(default 300s). Rejections: 401 bad signature, 408 stale timestamp,
400 malformed timestamp/JSON. On accept the payload is stored (seq assigned),
optionally fanned out to a publisher (failure logged, never fails ingest),
and the response is ``{"ok": true, "seq": N}``.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from orb.broadcast import sign

from .store import LeaderStore

log = logging.getLogger("leader.server")

DEFAULT_MAX_SKEW = 300.0


def make_handler(store: LeaderStore, secret: bytes, publisher=None,
                 max_skew: float = DEFAULT_MAX_SKEW,
                 now_fn: Callable[[], float] | None = None):
    """Build a request-handler class bound to store/secret/publisher.

    ``now_fn`` is injectable for deterministic skew tests (default
    ``time.time``). ``publisher`` needs only ``publish(topic, payload)``.
    """
    now = now_fn or time.time

    class Handler(BaseHTTPRequestHandler):

        # -- plumbing --------------------------------------------------

        def log_message(self, fmt, *args):  # quiet: route to stdlib logging
            log.debug("http client=%s line=%s", self.address_string(),
                      fmt % args)

        def _json(self, status: int, obj) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reject(self, status: int, error: str) -> None:
            log.warning("ingest_rejected status=%d error=%s client=%s",
                        status, error, self.address_string())
            self._json(status, {"ok": False, "error": error})

        # -- ingest ------------------------------------------------------

        def do_POST(self):  # noqa: N802 (http.server API)
            if urlparse(self.path).path != "/events":
                self._reject(404, "not_found")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._reject(400, "bad_content_length")
                return
            raw = self.rfile.read(length)
            ts = self.headers.get("X-Timestamp", "")
            sig = self.headers.get("X-Signature", "")
            if not hmac.compare_digest(sign(secret, ts, raw), sig):
                self._reject(401, "bad_signature")
                return
            try:
                ts_val = float(ts)
            except ValueError:
                self._reject(400, "bad_timestamp")
                return
            if abs(now() - ts_val) > max_skew:
                self._reject(408, "stale_timestamp")
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._reject(400, "malformed_json")
                return
            if not isinstance(payload, dict):
                self._reject(400, "payload_not_object")
                return
            seq = store.append(payload)
            if publisher is not None:
                stored = dict(payload)
                stored["seq"] = seq  # ZMQ frame == store line (schema doc)
                try:
                    publisher.publish(stored.get("symbol", ""), stored)
                except Exception as exc:  # fan-out must never fail ingest
                    log.error("publish_failed err=%s seq=%d", exc, seq)
            log.info("event_stored seq=%d symbol=%s action=%s", seq,
                     payload.get("symbol"), payload.get("action"))
            self._json(200, {"ok": True, "seq": seq})

        # -- reads --------------------------------------------------------

        def do_GET(self):  # noqa: N802 (http.server API)
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(200, {"ok": True, "events": store.count()})
                return
            if parsed.path == "/events/latest":
                try:
                    n = int(parse_qs(parsed.query).get("n", ["50"])[0])
                except ValueError:
                    self._reject(400, "bad_n")
                    return
                self._json(200, store.latest(n))
                return
            self._reject(404, "not_found")

    return Handler


def serve(port: int, store: LeaderStore, secret: bytes, *, publisher=None,
          max_skew: float = DEFAULT_MAX_SKEW,
          now_fn: Callable[[], float] | None = None,
          host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Bind and return the server (port 0 = ephemeral); caller runs
    ``serve_forever()`` and owns shutdown/close."""
    handler = make_handler(store, secret, publisher=publisher,
                           max_skew=max_skew, now_fn=now_fn)
    return ThreadingHTTPServer((host, port), handler)
