"""Read-only live-state check before a restart: account, our open positions /
pending orders (gold magic), and whether gold quotes are live. No order calls.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone

UNIVERSE = {
    "XAUUSD.ecn": 20260610,
    "US100.ecn": 20260611,
    "US500.ecn": 20260612,
    "XAGUSD.ecn": 20260613,
}


def main() -> int:
    try:
        import MetaTrader5 as mt5  # noqa: N816
    except ImportError:
        print("MetaTrader5 not importable", file=sys.stderr)
        return 2
    if not mt5.initialize():
        print(f"initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 3
    try:
        acct = mt5.account_info()
        print(f"account login={acct.login} server={acct.server} "
              f"balance={acct.balance} equity={acct.equity} "
              f"demo={acct.trade_mode == 0}")
        now = datetime.now(timezone.utc).timestamp()
        for sym, magic in UNIVERSE.items():
            mt5.symbol_select(sym, True)
            tick = mt5.symbol_info_tick(sym)
            age = (now - int(tick.time)) if tick else None
            live = (abs(age) < 120) if age is not None else False
            bid = getattr(tick, "bid", None) if tick else None
            pos = [p for p in (mt5.positions_get(symbol=sym) or ())
                   if p.magic == magic]
            pend = [o for o in (mt5.orders_get(symbol=sym) or ())
                    if o.magic == magic]
            print(f"{sym:<12} magic={magic} bid={bid} tick_age={age:+.0f}s "
                  f"market_live={live} positions={len(pos)} pendings={len(pend)}")
            for p in pos:
                print(f"   POS ticket={p.ticket} type={p.type} vol={p.volume} "
                      f"open={p.price_open} sl={p.sl} profit={p.profit}")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
