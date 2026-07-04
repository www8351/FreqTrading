"""Retcode policy table + Mt5Broker._send retry integration (Task 4, Part 2).

Uses the shared scripted fake from tests/_fakemt5.py. The ``retry=None``
default must leave ``_send`` byte-identical to the pre-Part-2 body (single
send, same exception text) — pinned by tests at the bottom.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from orb.broker import BrokerError, Mt5Broker
from orb.broker.retcodes import (
    POLICY,
    RETCODE_NAMES,
    PolicyAction,
    RetryPolicy,
    classify,
    delays,
)
from orb.models import Direction, Signal, SignalKind, State

from tests._fakemt5 import FakeMt5

TS = datetime(2026, 6, 10, 2, 7, tzinfo=timezone.utc)


def sig(kind=SignalKind.ENTRY, direction=Direction.SHORT, price=4182.02,
        stop=4187.88, tp=4164.42, qty=0.01, reason="breakout_short"):
    return Signal(ts=TS, kind=kind, direction=direction, price=price,
                  state_from=State.RANGE_DEFINED, state_to=State.BREAKOUT,
                  reason=reason, stop=stop, tp=tp, qty=qty)


def make_broker(fake=None, **kw):
    fake = fake or FakeMt5()
    b = Mt5Broker(symbol="XAUUSD.ecn", mt5=fake, **kw)
    b.connect()
    return b, fake


def no_sleep_policy(**kw):
    kw.setdefault("sleep_fn", lambda s: None)
    return RetryPolicy(**kw)


def deal_request(fake, volume=0.01, price=4182.00):
    return {
        "action": fake.TRADE_ACTION_DEAL,
        "symbol": "XAUUSD.ecn",
        "volume": volume,
        "type": fake.ORDER_TYPE_SELL,
        "price": price,
        "sl": 4187.86,
        "tp": 4164.40,
        "deviation": 20,
        "magic": 20260610,
        "comment": "orb:test",
        "type_time": fake.ORDER_TIME_GTC,
        "type_filling": fake.ORDER_FILLING_FOK,
    }


# ---------------------------------------------------------------------- #
# policy table
# ---------------------------------------------------------------------- #

EXPECTED_POLICY = {
    10009: PolicyAction.SUCCESS,
    10008: PolicyAction.SUCCESS,
    10010: PolicyAction.SUCCESS_PARTIAL,
    10004: PolicyAction.RETRY_FRESH_PRICE,
    10015: PolicyAction.RETRY_FRESH_PRICE,
    10020: PolicyAction.RETRY_FRESH_PRICE,
    10006: PolicyAction.RETRY_BACKOFF,
    10011: PolicyAction.RETRY_BACKOFF,
    10021: PolicyAction.RETRY_BACKOFF,
    10024: PolicyAction.RETRY_BACKOFF,
    10028: PolicyAction.RETRY_BACKOFF,
    10012: PolicyAction.AMBIGUOUS,
    10031: PolicyAction.AMBIGUOUS,
    10019: PolicyAction.DEFER_LADDER,
    10030: PolicyAction.ROTATE_FILLING,
    10013: PolicyAction.ABORT,
    10014: PolicyAction.ABORT,
    10017: PolicyAction.ABORT,
    10018: PolicyAction.ABORT,
    10022: PolicyAction.ABORT,
    10029: PolicyAction.ABORT,
    10033: PolicyAction.ABORT,
    10034: PolicyAction.ABORT,
    10016: PolicyAction.ABORT_ALERT,
    10026: PolicyAction.ABORT_ALERT,
    10027: PolicyAction.ABORT_ALERT,
}


def test_policy_table_covers_all_documented_retcodes():
    assert POLICY == EXPECTED_POLICY
    for code, action in EXPECTED_POLICY.items():
        assert classify(code) is action


def test_every_policy_code_has_a_name():
    for code in POLICY:
        assert code in RETCODE_NAMES


def test_classify_none_is_ambiguous_unknown_is_abort():
    assert classify(None) is PolicyAction.AMBIGUOUS
    assert classify(99999) is PolicyAction.ABORT
    assert classify(0) is PolicyAction.ABORT


def test_delays_exponential_then_capped():
    pol = RetryPolicy(max_retries=6, base_delay=0.5, mult=2.0, max_delay=4.0)
    assert list(delays(pol)) == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


def test_delays_defaults():
    assert list(delays(RetryPolicy())) == [0.5, 1.0, 2.0]


# ---------------------------------------------------------------------- #
# retry integration
# ---------------------------------------------------------------------- #


class RequoteMt5(FakeMt5):
    """Moves the tick after serving a 10004 requote (fresh-price scenario)."""

    def order_send(self, request):
        res = super().order_send(request)
        if res is not None and res.retcode == 10004:
            self.bid, self.ask = 4180.00, 4180.30
        return res


def test_requote_retries_with_fresh_price_then_succeeds():
    fake = RequoteMt5()
    fake.retcode_script = [10004]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b.execute(sig())
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 2
    # refresh_price closure re-read the tick and re-anchored price/SL/TP
    final = fake.sent[-1]
    assert final["price"] == 4180.00                      # new bid (short)
    assert abs(final["sl"] - 4185.86) < 1e-6              # bid + 5.86 risk dist
    assert abs(final["tp"] - 4162.40) < 1e-6              # bid - 17.60 tp dist


def test_requote_without_refresh_price_plain_retries():
    fake = FakeMt5()
    fake.retcode_script = [10004]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b._send(deal_request(fake))
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 2


def test_backoff_delays_exponential_and_capped():
    sleeps = []
    fake = FakeMt5()
    fake.retcode_script = [10006, 10006, 10006, 10006]
    pol = RetryPolicy(max_retries=4, base_delay=0.5, mult=2.0, max_delay=2.0,
                      sleep_fn=sleeps.append)
    b, _ = make_broker(fake=fake, retry=pol)
    res = b._send(deal_request(fake))
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 5
    assert sleeps == [0.5, 1.0, 2.0, 2.0]


def test_reject_retries_max_then_raises():
    sleeps = []
    fake = FakeMt5()
    fake.retcode_script = [10006, 10006, 10006, 10006]
    b, _ = make_broker(fake=fake,
                       retry=no_sleep_policy(sleep_fn=sleeps.append))
    with pytest.raises(BrokerError, match="retcode=10006"):
        b._send(deal_request(fake))
    assert len(fake.sent) == 4          # 1 initial + max_retries(3)
    assert sleeps == [0.5, 1.0, 2.0]
    assert b.retcode_counts[10006] == 4


class CountingMt5(FakeMt5):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.positions_calls = 0

    def positions_get(self, symbol=None):
        self.positions_calls += 1
        return super().positions_get(symbol)


def test_ambiguous_none_result_requeries_positions_then_retries():
    fake = CountingMt5()
    fake.retcode_script = [None]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b._send(deal_request(fake), entry_guard=True)
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 2
    # snapshot before first send + re-query after the ambiguous failure
    assert fake.positions_calls >= 2


class DoubleFillMt5(FakeMt5):
    """order_send 'fails' (None) but the deal actually landed server-side."""

    def order_send(self, request):
        res = super().order_send(request)
        if res is None:
            self.positions.append(SimpleNamespace(
                ticket=901, magic=request["magic"], symbol=request["symbol"],
                volume=request["volume"], price_open=request["price"],
                type=self.POSITION_TYPE_SELL, sl=0.0, tp=0.0))
        return res


def test_double_fill_recovered_position_not_resent():
    fake = DoubleFillMt5()
    fake.retcode_script = [None]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b.execute(sig())
    assert len(fake.sent) == 1          # NEVER resent — double-fill guard
    assert res["retcode"] == -1         # synthesized "recovered"
    assert res["order"] == 901
    assert res["deal"] == 0
    assert res["price"] == 4182.00
    assert res["volume"] == 0.01


def test_ambiguous_ignores_preexisting_positions():
    fake = DoubleFillMt5()
    fake.retcode_script = [None]
    fake.positions.append(SimpleNamespace(
        ticket=700, magic=20260610, symbol="XAUUSD.ecn", volume=0.02,
        price_open=4179.00, type=FakeMt5.POSITION_TYPE_SELL, sl=0.0, tp=0.0))
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b.execute(sig())
    assert res["retcode"] == -1
    assert res["order"] == 901          # the NEW ticket, not the old 700


def test_market_closed_aborts_without_retry():
    fake = FakeMt5()
    fake.retcode_script = [10018]
    sleeps = []
    b, _ = make_broker(fake=fake,
                       retry=no_sleep_policy(sleep_fn=sleeps.append))
    with pytest.raises(BrokerError, match="retcode=10018"):
        b._send(deal_request(fake))
    assert len(fake.sent) == 1
    assert sleeps == []


def test_autotrading_disabled_aborts_with_alert(caplog):
    fake = FakeMt5()
    fake.retcode_script = [10027]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    with caplog.at_level("ERROR", logger="orb.broker.mt5"):
        with pytest.raises(BrokerError, match="retcode=10027"):
            b._send(deal_request(fake))
    assert len(fake.sent) == 1
    assert any("order_abort_alert" in r.message for r in caplog.records)


def test_10019_defers_to_volume_ladder():
    fake = FakeMt5()
    fake.retcode_script = [10019]       # 0.04 rejected -> ladder tries 0.02
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b.execute(sig(qty=0.04))
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 2
    assert fake.sent[0]["volume"] == 0.04
    assert fake.sent[1]["volume"] == 0.02


class FillingRecorderMt5(FakeMt5):
    """Snapshots type_filling per send (requests are mutated in place)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fillings = []

    def order_send(self, request):
        self.fillings.append(request.get("type_filling"))
        return super().order_send(request)


def test_rotate_filling_switches_once_then_aborts():
    fake = FillingRecorderMt5()
    fake.retcode_script = [10030]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b._send(deal_request(fake))   # FOK -> IOC, second send succeeds
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert fake.fillings == [fake.ORDER_FILLING_FOK, fake.ORDER_FILLING_IOC]

    fake2 = FillingRecorderMt5()
    fake2.retcode_script = [10030, 10030]
    b2, _ = make_broker(fake=fake2, retry=no_sleep_policy())
    with pytest.raises(BrokerError, match="retcode=10030"):
        b2._send(deal_request(fake2))
    assert len(fake2.sent) == 2         # one rotation, then abort


def test_partial_fill_counts_as_success():
    fake = FakeMt5()
    fake.retcode_script = [10010]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b._send(deal_request(fake))
    assert res["retcode"] == 10010
    assert len(fake.sent) == 1


def test_retcode_counts_accumulate():
    fake = FakeMt5()
    fake.retcode_script = [10004, 10006]
    b, _ = make_broker(fake=fake, retry=no_sleep_policy())
    res = b._send(deal_request(fake))
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert b.retcode_counts[10004] == 1
    assert b.retcode_counts[10006] == 1
    assert b.retcode_counts[10009] == 1


# ---------------------------------------------------------------------- #
# retry=None default: byte-identical to the pre-Part-2 _send
# ---------------------------------------------------------------------- #


def test_retry_none_default_single_send_same_rejection_text():
    fake = FakeMt5()
    fake.retcode_script = [10006]
    b, _ = make_broker(fake=fake)       # no retry kwarg at all
    req = deal_request(fake)
    with pytest.raises(BrokerError) as ei:
        b._send(req)
    assert len(fake.sent) == 1
    assert str(ei.value) == (
        f"order rejected retcode=10006 comment='ret=10006' request={req}"
    )


def test_retry_none_default_none_result_same_text():
    fake = FakeMt5()
    fake.retcode_script = [None]
    b, _ = make_broker(fake=fake)
    with pytest.raises(BrokerError) as ei:
        b._send(deal_request(fake))
    assert len(fake.sent) == 1
    assert str(ei.value) == "order_send returned None: (0, 'ok')"


def test_retry_none_ignores_guard_kwargs():
    fake = FakeMt5()
    b, _ = make_broker(fake=fake)
    res = b._send(deal_request(fake), refresh_price=lambda: None,
                  entry_guard=True)
    assert res["retcode"] == fake.TRADE_RETCODE_DONE
    assert len(fake.sent) == 1
