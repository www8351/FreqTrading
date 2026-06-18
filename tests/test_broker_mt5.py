"""Mt5Broker tests against a fake mt5 module (no terminal needed)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orb.broker import BrokerError, Mt5Broker
from orb.models import Direction, Signal, SignalKind, State

TS = datetime(2026, 6, 10, 2, 7, tzinfo=timezone.utc)


def sig(kind, direction=Direction.SHORT, price=4182.02, stop=4187.88,
        tp=4164.42, qty=0.01, reason="breakout_short"):
    return Signal(ts=TS, kind=kind, direction=direction, price=price,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason=reason, stop=stop, tp=tp, qty=qty)


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, trade_mode=0):
        self.trade_mode = trade_mode
        self.sent = []
        self.positions = []
        self.pending = []

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(login=2001894982, server="JustMarkets-Demo",
                               trade_mode=self.trade_mode, balance=522.62,
                               currency="USD")

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=1)  # FOK only

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=4182.00, ask=4182.30)

    def positions_get(self, symbol=None):
        return list(self.positions)

    def orders_get(self, symbol=None):
        return list(self.pending)

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=111,
                               deal=222, price=request.get("price", 0.0),
                               volume=request.get("volume", 0.0), comment="done")


def make_broker(**kw):
    fake = FakeMt5(trade_mode=kw.pop("trade_mode", 0))
    b = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake, **kw)
    return b, fake


def test_live_account_blocked_without_flag():
    b, _ = make_broker(trade_mode=2)  # real account
    with pytest.raises(BrokerError, match="NOT demo"):
        b.connect()


def test_live_account_allowed_with_flag():
    b, _ = make_broker(trade_mode=2, allow_live=True)
    assert b.connect()["demo"] is False


def test_entry_short_sends_sell_with_sl_tp_qty():
    b, fake = make_broker()
    b.connect()
    res = b.execute(sig(SignalKind.ENTRY))
    assert res["retcode"] == FakeMt5.TRADE_RETCODE_DONE
    req = fake.sent[0]
    assert req["type"] == FakeMt5.ORDER_TYPE_SELL
    assert req["volume"] == 0.01
    assert abs(req["sl"] - 4187.86) < 1e-6  # re-anchored: bid + signal sl-dist
    assert abs(req["tp"] - 4164.40) < 1e-6  # re-anchored: bid - signal tp-dist
    assert req["price"] == 4182.00  # sell at bid
    assert req["type_filling"] == FakeMt5.ORDER_FILLING_FOK  # symbol supports FOK only


def test_exit_closes_position_by_magic():
    b, fake = make_broker()
    b.connect()
    fake.positions = [SimpleNamespace(ticket=7, magic=b.magic, volume=0.01,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    res = b.execute(sig(SignalKind.EXIT, reason="take_profit"))
    assert res is not None
    req = fake.sent[0]
    assert req["type"] == FakeMt5.ORDER_TYPE_BUY  # buy to close short
    assert req["position"] == 7


def test_exit_with_no_position_is_noop():
    b, fake = make_broker()
    b.connect()
    assert b.execute(sig(SignalKind.EXIT, reason="trail_stop")) is None
    assert fake.sent == []


def test_reject_signal_ignored():
    b, fake = make_broker()
    b.connect()
    assert b.execute(sig(SignalKind.REJECT, reason="momentum_fail:roc")) is None
    assert fake.sent == []


def test_partial_tp_closes_fraction_no_server_tp():
    b, fake = make_broker(server_tp=False)
    b.connect()
    # entry: TP must NOT go to the server in partial mode
    b.execute(sig(SignalKind.ENTRY))
    assert fake.sent[0]["tp"] == 0.0
    assert abs(fake.sent[0]["sl"] - 4187.86) < 1e-6  # SL still server-side
    # partial exit: closes 0.04 of 0.05 (0.035 snapped to 0.01 step)
    fake.positions = [SimpleNamespace(ticket=9, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    res = b.execute(sig(SignalKind.EXIT, qty=0.035, reason="take_profit_partial"))
    assert res is not None
    assert fake.sent[1]["volume"] == 0.04
    assert fake.sent[1]["position"] == 9


def test_sl_tp_reanchored_to_fill_price():
    """SL/TP distances follow the actual request price, not the signal price."""
    b, fake = make_broker()
    b.connect()
    # signal: short @4182.02, stop +5.86, tp -17.60; tick bid = 4182.00
    b.execute(sig(SignalKind.ENTRY))
    req = fake.sent[0]
    assert abs(req["sl"] - (4182.00 + 5.86)) < 1e-6
    assert abs(req["tp"] - (4182.00 - 17.60)) < 1e-6


def test_update_stop_modifies_server_sl():
    b, fake = make_broker()
    b.connect()
    fake.positions = [SimpleNamespace(ticket=11, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL,
                                      sl=4124.41, tp=0.0)]
    res = b.update_stop(4120.00)
    assert res is not None
    req = fake.sent[0]
    assert req["action"] == FakeMt5.TRADE_ACTION_SLTP
    assert req["position"] == 11
    assert req["sl"] == 4120.00


def test_update_stop_noop_without_position():
    b, fake = make_broker()
    b.connect()
    assert b.update_stop(4120.00) is None
    assert fake.sent == []


def test_no_money_falls_back_down_volume_ladder():
    b, fake = make_broker()
    b.connect()

    real_send = fake.order_send
    def flaky(request):
        if request["volume"] > 0.02:
            fake.sent.append(request)
            return SimpleNamespace(retcode=10019, order=0, deal=0,
                                   price=0.0, volume=0.0, comment="No money")
        return real_send(request)
    fake.order_send = flaky

    res = b.execute(sig(SignalKind.ENTRY, qty=0.05))
    assert res is not None and res["volume"] == 0.02
    assert [r["volume"] for r in fake.sent] == [0.05, 0.02]


def test_limit_entry_places_two_pendings_at_liquidity_levels():
    """entry_mode=limit: SELL LIMIT where the naive stop was (price+d), add-on
    SELL LIMIT deeper at 80% toward that order's SL. Shared SL, own TPs."""
    b, fake = make_broker(entry_mode="limit")
    b.connect()
    # short signal: price 4182.02, stop 4184.02 (d=2.0), tp 4178.02 (rrr=2)
    res = b.execute(sig(SignalKind.ENTRY, stop=4184.02, tp=4178.02, qty=0.05))
    assert res is not None
    assert len(fake.sent) == 2
    l1, l2 = fake.sent
    assert l1["action"] == FakeMt5.TRADE_ACTION_PENDING
    assert l1["type"] == FakeMt5.ORDER_TYPE_SELL_LIMIT
    assert abs(l1["price"] - 4184.02) < 1e-6        # entry at old-stop level
    assert abs(l1["sl"] - 4186.02) < 1e-6           # d above L1
    assert l1["tp"] == 0.0                          # no server TP: babysitter
    assert l2["type"] == FakeMt5.ORDER_TYPE_SELL_LIMIT
    assert abs(l2["price"] - (4184.02 + 0.8 * 2.0)) < 1e-6  # add-on near SL
    assert abs(l2["sl"] - 4186.02) < 1e-6           # shared SL
    assert l2["tp"] == 0.0
    assert l1["volume"] == 0.05 and l2["volume"] == 0.05


def test_limit_mode_engine_exit_is_fully_ignored():
    """Limit mode: engine virtual exits touch NOTHING — pendings keep working,
    positions are babysitter-managed (partial at 2R + chasing stop)."""
    b, fake = make_broker(entry_mode="limit")
    b.connect()
    fake.pending = [SimpleNamespace(ticket=21, magic=b.magic)]
    fake.positions = [SimpleNamespace(ticket=22, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    assert b.execute(sig(SignalKind.EXIT, reason="trail_stop")) is None
    assert fake.sent == []  # no cancels, no closes


def test_cancel_pending_removes_only_our_orders():
    b, fake = make_broker(entry_mode="limit")
    b.connect()
    fake.pending = [SimpleNamespace(ticket=31, magic=b.magic),
                    SimpleNamespace(ticket=32, magic=999)]
    b.cancel_pending()
    removes = [r for r in fake.sent if r["action"] == FakeMt5.TRADE_ACTION_REMOVE]
    assert [r["order"] for r in removes] == [31]


def test_spike_cancel_spares_fresh_orders():
    b, fake = make_broker(entry_mode="limit")
    b.connect()
    now = 1_000_000
    fake.symbol_info_tick = lambda s: SimpleNamespace(bid=4182.0, ask=4182.3,
                                                      time=now)
    fake.pending = [SimpleNamespace(ticket=41, magic=b.magic, time_setup=now - 300),
                    SimpleNamespace(ticket=42, magic=b.magic, time_setup=now - 10)]
    b.cancel_pending(min_age_sec=120)
    removes = [r["order"] for r in fake.sent
               if r["action"] == FakeMt5.TRADE_ACTION_REMOVE]
    assert removes == [41]  # old cancelled, fresh (10s) spared


def test_limit_ttl_cancels_only_stale_orders():
    b, fake = make_broker(entry_mode="limit")
    b.connect()
    now = 2_000_000
    fake.symbol_info_tick = lambda s: SimpleNamespace(bid=4182.0, ask=4182.3,
                                                      time=now)
    fake.pending = [
        SimpleNamespace(ticket=51, magic=b.magic, time_setup=now - 1900),  # 31min
        SimpleNamespace(ticket=52, magic=b.magic, time_setup=now - 600),   # 10min
        SimpleNamespace(ticket=53, magic=999, time_setup=now - 9000),      # not ours
    ]
    n = b.cancel_expired(1800)
    assert n == 1
    removes = [r["order"] for r in fake.sent
               if r["action"] == FakeMt5.TRADE_ACTION_REMOVE]
    assert removes == [51]
