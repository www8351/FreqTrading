"""``python -m leader`` — copy-trade leader node sidecar CLI.

    $env:COPYTRADE_SECRET='...'    # REQUIRED (never a CLI argument)
    python -m leader --port 8787 --store leader_events.jsonl
    python -m leader --port 8787 --store leader_events.jsonl --zmq-pub tcp://*:5556
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .server import DEFAULT_MAX_SKEW, serve
from .store import LeaderStore


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="leader", description="copy-trade leader node sidecar")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (use 0.0.0.0 to accept remote followers)")
    p.add_argument("--store", default="leader_events.jsonl",
                   help="append-only JSONL event store path")
    p.add_argument("--zmq-pub", dest="zmq_pub", default=None,
                   help="optional ZeroMQ PUB bind, e.g. tcp://*:5556 "
                        "(requires pyzmq; imported only when set)")
    p.add_argument("--max-skew", dest="max_skew", type=float,
                   default=DEFAULT_MAX_SKEW,
                   help="max |now - X-Timestamp| seconds before 408")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(),
                                      logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    secret = os.environ.get("COPYTRADE_SECRET", "")
    if not secret:
        print("error: COPYTRADE_SECRET environment variable is required and "
              "must be non-empty. Set it in the environment before starting "
              "the leader node — it is never accepted as a CLI argument.",
              file=sys.stderr)
        return 2

    store = LeaderStore(args.store)
    publisher = None
    if args.zmq_pub:
        from .pub import ZmqPublisher  # lazy: pyzmq only needed with --zmq-pub
        publisher = ZmqPublisher(args.zmq_pub)

    server = serve(args.port, store, secret.encode("utf-8"),
                   publisher=publisher, max_skew=args.max_skew, host=args.host)
    print(f"# leader node: http://{args.host}:{server.server_address[1]}/events "
          f"store={args.store} events={store.count()} "
          f"zmq_pub={args.zmq_pub or 'off'}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if publisher is not None:
            publisher.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
