"""One-shot broker smoke test on the DEMO account.

Opens a 0.01-lot XAUUSD short with SL at 1R above and TP at 3R below (RRR 1:3),
prints the live position as proof, then closes it.

Run: python scripts/demo_order_smoke.py
"""

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")

from orb.broker import Mt5Broker
from orb.models import Direction, Signal, SignalKind, State

RISK = 4.0  # SL distance in USD; TP = 3 x RISK below entry (short)


def main() -> int:
    broker = Mt5Broker(symbol="XAUUSD.ecn", default_qty=0.01)
    info = broker.connect()
    print(f"connected: {info}")
    assert info["demo"], "refusing: not a demo account"

    m = broker._mt5
    tick = m.symbol_info_tick(broker.symbol)
    entry = tick.bid
    sl = round(entry + RISK, 2)
    tp = round(entry - 3 * RISK, 2)
    print(f"plan: SHORT 0.01 {broker.symbol} @~{entry}  SL={sl} (+{RISK})  "
          f"TP={tp} (-{3 * RISK})  RRR=1:3")

    now = datetime.now(timezone.utc)
    entry_sig = Signal(ts=now, kind=SignalKind.ENTRY, direction=Direction.SHORT,
                       price=entry, state_from=State.RANGE_DEFINED,
                       state_to=State.BREAKOUT, reason="smoke_test",
                       stop=sl, tp=tp, qty=0.01)
    res = broker.execute(entry_sig)
    print(f"ORDER SENT: {res}")

    time.sleep(2)
    pos = [p for p in (m.positions_get(symbol=broker.symbol) or ())
           if p.magic == broker.magic]
    if not pos:
        print("FAIL: no position found after order")
        return 1
    p = pos[0]
    print(f"POSITION LIVE: ticket={p.ticket} vol={p.volume} "
          f"open={p.price_open} sl={p.sl} tp={p.tp} pnl={p.profit}")

    exit_sig = Signal(ts=now, kind=SignalKind.EXIT, direction=Direction.SHORT,
                      price=entry, state_from=State.BREAKOUT, state_to=State.IDLE,
                      reason="smoke_test_close", qty=0.01)
    res = broker.execute(exit_sig)
    print(f"CLOSED: {res}")
    broker.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
