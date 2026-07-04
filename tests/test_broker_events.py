"""Mt5Broker trade-event emission tests (Part 2 Task 3).

Contract under test:
* ``on_event`` hook (default ``None``) fires one schema-v1 TradeEvent per
  broker action: open / open_pending / partial_close / close / modify_sl /
  cancel_pending;
* no callback => zero emissions AND the order_send request stream is
  byte-identical to a hookless broker run (no extra terminal calls either);
* a sink that raises can NEVER break the order;
* ``deal_profit`` / ``current_spread`` helpers are best-effort reads.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from orb.broker import Mt5Broker
from orb.models import Direction, Signal, SignalKind, State
from orb.tradeevents import TradeEvent

from tests._fakemt5 import Deal, FakeMt5

TS = datetime(2026, 6, 10, 2, 7, tzinfo=timezone.utc)


def sig(kind, direction=Direction.SHORT, price=4182.02, stop=4187.88,
        tp=4164.42, qty=0.01, reason="breakout_short"):
    return Signal(ts=TS, kind=kind, direction=direction, price=price,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason=reason, stop=stop, tp=tp, qty=qty)


def make_broker(**kw):
    events = []
    fake = FakeMt5(trade_mode=kw.pop("trade_mode", 0))
    b = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake, on_event=events.append, **kw)
    b.connect()
    return b, fake, events


# ---------------------------------------------------------------- open ---- #
def test_open_emits_event_with_fill_price_and_ticket():
    b, fake, events = make_broker()
    fake.fill_price = 4182.05  # short sells at bid 4182.00 -> 0.05 slippage
    res = b.execute(sig(SignalKind.ENTRY))
    assert res is not None
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, TradeEvent)
    assert ev.action == "open"
    assert ev.price_requested == 4182.00      # pre-send tick price (bid)
    assert ev.price_filled == 4182.05         # actual fill from the result
    assert abs(ev.slippage - 0.05) < 1e-9
    assert ev.ticket == 111 and ev.order == 111 and ev.deal == 222
    assert ev.direction == "short"
    assert ev.volume == 0.01
    assert ev.reason == "breakout_short"
    assert ev.retcode == FakeMt5.TRADE_RETCODE_DONE
    assert ev.account == 2001894982
    assert ev.strategy == "orb" and ev.magic == b.magic
    assert ev.symbol == "XAUUSD.ecn" and ev.base_symbol == "XAUUSD"


def test_open_ladder_emits_single_event_with_final_volume():
    """10019 ladder: only the successful send emits, at the reduced volume."""
    b, fake, events = make_broker()
    fake.retcode_script = [10019]  # first send (0.05) fails, retry 0.02 fills
    res = b.execute(sig(SignalKind.ENTRY, qty=0.05))
    assert res is not None and res["volume"] == 0.02
    assert len(events) == 1
    assert events[0].action == "open"
    assert events[0].volume == 0.02


# ------------------------------------------------------------- pending ---- #
def test_limit_mode_emits_two_open_pending_events():
    b, fake, events = make_broker(entry_mode="limit")
    res = b.execute(sig(SignalKind.ENTRY, stop=4184.02, tp=4178.02, qty=0.05))
    assert res is not None
    assert len(fake.sent) == 2
    assert [ev.action for ev in events] == ["open_pending", "open_pending"]
    entry, addon = events
    assert entry.reason.startswith("entry:")
    assert addon.reason.startswith("addon:")
    assert abs(entry.price_requested - 4184.02) < 1e-6
    assert abs(addon.price_requested - (4184.02 + 0.8 * 2.0)) < 1e-6
    assert entry.direction == "short" and addon.direction == "short"
    assert entry.volume == 0.05 and addon.volume == 0.05


# --------------------------------------------------------------- close ---- #
def test_partial_close_emits_partial_close_with_pnl():
    b, fake, events = make_broker(server_tp=False)
    fake.positions = [SimpleNamespace(ticket=9, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    fake.deals = [Deal(ticket=222, order=111, position_id=9, profit=12.5)]
    res = b.execute(sig(SignalKind.EXIT, qty=0.035, reason="take_profit_partial"))
    assert res is not None
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "partial_close"
    assert ev.volume == 0.04           # 0.035 snapped to 0.01 step
    assert ev.ticket == 9              # the position, not the closing order
    assert ev.pnl == 12.5
    assert ev.reason == "take_profit_partial"


def test_full_close_emits_close():
    b, fake, events = make_broker()
    fake.positions = [SimpleNamespace(ticket=9, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    fake.deals = [Deal(ticket=222, order=111, position_id=9, profit=-7.0)]
    res = b.execute(sig(SignalKind.EXIT, reason="stop_loss"))
    assert res is not None
    assert len(events) == 1
    assert events[0].action == "close"
    assert events[0].volume == 0.05
    assert events[0].pnl == -7.0


def test_close_ticket_babysitter_route_emits():
    """close_ticket (babysitter partial at 2R) routes through _close_position
    and therefore emits without any extra wiring."""
    b, fake, events = make_broker(entry_mode="limit")
    fake.positions = [SimpleNamespace(ticket=9, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL)]
    fake.deals = [Deal(ticket=222, order=111, position_id=9, profit=3.3)]
    res = b.close_ticket(9, 0.03)
    assert res is not None
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "partial_close"   # 0.03 < 0.05
    assert ev.ticket == 9
    assert ev.pnl == 3.3


def test_deal_profit_missing_deal_returns_none():
    b, fake, _ = make_broker()
    assert b.deal_profit(999) is None      # no matching history deal
    assert b.deal_profit(None) is None     # no deal id at all

    def boom(*a, **kw):
        raise RuntimeError("terminal gone")
    fake.history_deals_get = boom
    assert b.deal_profit(222) is None      # best-effort: swallow errors


# ----------------------------------------------------------- modify_sl ---- #
def test_modify_sl_emits():
    b, fake, events = make_broker()
    fake.positions = [SimpleNamespace(ticket=11, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL,
                                      sl=4124.41, tp=0.0)]
    res = b.modify_sl(11, 4120.00)
    assert res is not None
    assert len(events) == 1
    ev = events[0]
    assert ev.action == "modify_sl"
    assert ev.ticket == 11
    assert ev.sl == 4120.00


def test_update_stop_emits_modify_sl():
    b, fake, events = make_broker()
    fake.positions = [SimpleNamespace(ticket=11, magic=b.magic, volume=0.05,
                                      type=FakeMt5.POSITION_TYPE_SELL,
                                      sl=4124.41, tp=0.0)]
    res = b.update_stop(4120.00)
    assert res is not None
    assert [ev.action for ev in events] == ["modify_sl"]
    assert events[0].ticket == 11 and events[0].sl == 4120.00


# ------------------------------------------------------------- cancels ---- #
def test_cancel_pending_emits_cancel_events_for_ours_only():
    b, fake, events = make_broker(entry_mode="limit")
    fake.pending = [SimpleNamespace(ticket=31, magic=b.magic),
                    SimpleNamespace(ticket=32, magic=999)]
    b.cancel_pending()
    assert [ev.action for ev in events] == ["cancel_pending"]
    assert events[0].ticket == 31


def test_cancel_expired_emits_cancel_events():
    b, fake, events = make_broker(entry_mode="limit")
    now = 2_000_000
    fake.tick_time = now
    fake.pending = [SimpleNamespace(ticket=51, magic=b.magic,
                                    time_setup=now - 1900)]
    assert b.cancel_expired(1800) == 1
    assert [ev.action for ev in events] == ["cancel_pending"]
    assert events[0].ticket == 51


# --------------------------------------------------- default-off safety --- #
def test_no_callback_means_no_emission_and_identical_requests():
    """Default construction (no on_event) must be byte-identical: same request
    stream, zero extra terminal calls (no history_deals_get for pnl)."""
    def run_scenario(broker, fake):
        broker.connect()
        broker.execute(sig(SignalKind.ENTRY))
        fake.positions = [SimpleNamespace(ticket=9, magic=broker.magic,
                                          volume=0.05,
                                          type=FakeMt5.POSITION_TYPE_SELL)]
        broker.execute(sig(SignalKind.EXIT, qty=0.035,
                           reason="take_profit_partial"))
        broker.update_stop(4120.00)

    fake_a = FakeMt5()
    hookless = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake_a)  # pre-Part-2 shape
    run_scenario(hookless, fake_a)

    fake_b = FakeMt5()
    explicit_none = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake_b, on_event=None,
                              strategy="orb")
    run_scenario(explicit_none, fake_b)

    assert fake_a.sent == fake_b.sent
    assert fake_a.history_calls == 0 and fake_b.history_calls == 0


def test_emit_exception_never_breaks_order():
    fake = FakeMt5()

    def bad_sink(ev):
        raise RuntimeError("sink exploded")

    b = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake, on_event=bad_sink)
    b.connect()
    res = b.execute(sig(SignalKind.ENTRY))
    assert res is not None                    # order succeeded regardless
    assert res["retcode"] == FakeMt5.TRADE_RETCODE_DONE
    assert len(fake.sent) == 1                # request went out exactly once


# ---------------------------------------------------------------- reads --- #
def test_current_spread_reads_tick():
    b, fake, _ = make_broker()
    spread = b.current_spread()
    assert spread["bid"] == 4182.00
    assert spread["ask"] == 4182.30
    assert abs(spread["spread"] - 0.30) < 1e-9
    fake.bid, fake.ask = 4182.10, 4182.25     # mutable tick mid-scenario
    spread = b.current_spread()
    assert spread["bid"] == 4182.10
    assert abs(spread["spread"] - 0.15) < 1e-9
