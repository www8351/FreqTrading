"""``python -m macro`` — macro layer sidecar CLI.

Commands:
- ``emit``     write a neutral (no-veto) macro_state.json (M0; offline).
- ``calendar`` fetch + print the ForexFactory calendar (M1 debug; network).
- ``run``      run the daemon: keep macro_state.json fresh with calendar blackouts.

    python -m macro run --out macro_state.json
    orb live ... --macro-mode shadow --macro-state-path macro_state.json
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import DEFAULT_SYMBOLS
from .blackout import DEFAULT_POST_MIN, DEFAULT_PRE_MIN, upcoming_events
from .collectors import forexfactory, gdelt
from .state_writer import neutral_state, write_state


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="macro", description="macro layer sidecar")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command", required=True)

    ep = sub.add_parser("emit", help="write a neutral (no-veto) macro_state.json")
    ep.add_argument("--out", default="macro_state.json")
    ep.add_argument("--ttl", type=int, default=300)
    ep.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))

    cp = sub.add_parser("calendar", help="fetch + print the economic calendar")
    cp.add_argument("--url", default=forexfactory.DEFAULT_URL)

    rp = sub.add_parser("run", help="daemon: refresh macro_state.json with blackouts")
    rp.add_argument("--out", default="macro_state.json")
    rp.add_argument("--url", default=forexfactory.DEFAULT_URL)
    rp.add_argument("--fetch-interval", type=float, default=900.0)
    rp.add_argument("--write-interval", type=float, default=60.0)
    rp.add_argument("--ttl", type=int, default=300)
    rp.add_argument("--pre-min", type=int, default=DEFAULT_PRE_MIN)
    rp.add_argument("--post-min", type=int, default=DEFAULT_POST_MIN)
    rp.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    rp.add_argument("--geo", action="store_true",
                    help="enable GDELT geopolitics + VIX confirm (war-spike); needs "
                         "network, and FRED_API_KEY for the VIX confirmation")
    rp.add_argument("--gdelt-query", dest="gdelt_query", default=gdelt.DEFAULT_QUERY)
    rp.add_argument("--news", action="store_true",
                    help="enable RSS headline sentiment (stdlib lexicon; needs network)")
    rp.add_argument("--semis", action="store_true",
                    help="enable AI/semis thematic bias (Stooq momentum; needs network)")

    args = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.command == "emit":
        write_state(neutral_state(symbols=tuple(args.symbols), ttl_sec=args.ttl),
                    args.out)
        print(f"# wrote neutral macro_state -> {args.out} (ttl={args.ttl}s)",
              file=sys.stderr)
        return 0

    if args.command == "calendar":
        events = forexfactory.fetch(args.url)
        now = _now()
        print(f"# fetched {len(events)} events from {args.url}", file=sys.stderr)
        for ev in upcoming_events(events, now, horizon_h=72):
            print(f"{ev['ts']} | {ev['impact']:6} | {ev['kind']:5} | "
                  f"{ev['currency']}")
        return 0

    if args.command == "run":
        from .daemon import (_default_geo_provider, _default_news_provider,
                             _default_thematic_provider, run)
        geo_provider = (_default_geo_provider(query=args.gdelt_query)
                        if args.geo else None)
        news_provider = (_default_news_provider(symbols=tuple(args.symbols))
                         if args.news else None)
        thematic_provider = (_default_thematic_provider(symbols=tuple(args.symbols))
                             if args.semis else None)
        print(f"# macro daemon: out={args.out} fetch={args.fetch_interval}s "
              f"write={args.write_interval}s ttl={args.ttl}s geo={args.geo} "
              f"news={args.news} semis={args.semis}", file=sys.stderr)
        run(out=args.out, url=args.url, fetch_interval=args.fetch_interval,
            write_interval=args.write_interval, ttl_sec=args.ttl,
            symbols=tuple(args.symbols), pre_min=args.pre_min, post_min=args.post_min,
            geo_provider=geo_provider, news_provider=news_provider,
            thematic_provider=thematic_provider)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
