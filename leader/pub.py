"""Optional ZeroMQ PUB fan-out for followers.

``pyzmq`` is imported LAZILY inside ``ZmqPublisher.__init__`` so the leader
node runs on pure stdlib unless ``--zmq-pub`` is actually requested.
Frame format: multipart ``[topic(=symbol), json_payload]`` where the payload
is the exact stored line (seq included) per docs/copytrade_schema.md.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("leader.pub")


class ZmqPublisher:
    """PUB socket bound to ``bind`` (e.g. ``tcp://*:5556``)."""

    def __init__(self, bind: str) -> None:
        import zmq  # lazy: only --zmq-pub users need pyzmq installed
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.bind(bind)
        self.bind = bind
        log.info("zmq_pub_bound bind=%s", bind)

    def publish(self, topic: str, payload: dict) -> None:
        self._sock.send_multipart([str(topic).encode("utf-8"),
                                   json.dumps(payload).encode("utf-8")])

    def close(self) -> None:
        try:
            self._sock.close(linger=0)
        except Exception as exc:
            log.error("zmq_close_failed err=%s", exc)
