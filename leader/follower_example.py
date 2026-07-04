"""Minimal follower example: poll ``GET /events/latest`` and print new events.

Stdlib-only reference for building a follower node. Tracks the highest seq
seen and prints each newly observed event as one JSON line. A real follower
would map these events onto its own broker (mirroring opens/closes with its
own sizing) instead of printing.

    python -m leader.follower_example --url http://127.0.0.1:8787 --interval 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="leader.follower_example",
        description="poll a leader node and print new events")
    p.add_argument("--url", default="http://127.0.0.1:8787",
                   help="leader node base URL")
    p.add_argument("--interval", type=float, default=2.0,
                   help="poll interval seconds")
    p.add_argument("--n", type=int, default=50,
                   help="events per poll (catch-up window)")
    args = p.parse_args(argv)

    last_seq = 0
    print(f"# follower polling {args.url}/events/latest?n={args.n} "
          f"every {args.interval}s", file=sys.stderr)
    while True:
        try:
            with urllib.request.urlopen(
                    f"{args.url}/events/latest?n={args.n}",
                    timeout=5) as resp:
                events = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # leader down: keep polling
            print(f"# poll_failed err={exc}", file=sys.stderr)
            events = []
        for ev in events:
            seq = ev.get("seq", 0)
            if isinstance(seq, int) and seq > last_seq:
                last_seq = seq
                print(json.dumps(ev), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
