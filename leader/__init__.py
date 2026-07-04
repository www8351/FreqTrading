"""leader/ — the copy-trade leader node sidecar.

A standalone local process that ingests trade events from ``orb.broadcast``
(and the Part 1 MQL5 EA via WebRequest) over authenticated HTTP POST, persists
them to an append-only JSONL store with monotonic ``seq`` numbers, and
optionally re-publishes each event on a ZeroMQ PUB socket for followers.

This package is a SEPARATE process from the trading engine and MAY use
third-party deps (pyzmq is imported lazily, only when ``--zmq-pub`` is
requested). The engine-side ``orb.broadcast`` producer stays stdlib-only and
never imports this package; the wire contract they share is
``X-Signature = hex(hmac_sha256(secret, "<X-Timestamp>." + raw_body))`` with
the secret supplied via env ``COPYTRADE_SECRET`` on both ends (never a CLI
argument). See docs/copytrade_schema.md.

Run: ``python -m leader --port 8787 --store leader_events.jsonl``
"""
