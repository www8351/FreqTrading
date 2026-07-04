"""Shared fake MetaTrader5 module for Part 2 broker tests (stdlib only).

Extended clone of the ``FakeMt5`` in tests/test_broker_mt5.py (that file stays
UNTOUCHED — this helper serves the new Part 2 test files). Extensions:

* scripted ``order_send`` retcodes: ``retcode_script`` list is consumed one
  entry per call (``None`` entry -> ``order_send`` returns ``None``), then the
  default ``TRADE_RETCODE_DONE`` applies;
* mutable tick: tests set ``bid`` / ``ask`` / ``tick_time`` mid-scenario;
* optional ``fill_price`` overrides the result fill price (slippage tests);
* ``symbols_get()`` returns configurable :data:`SymbolInfo` namedtuples
  (``name``/``visible``/``trade_mode``; ``SYMBOL_TRADE_MODE_FULL = 4``);
* ``history_deals_get`` returns configurable :data:`Deal` tuples (with a
  ``profit`` attr) and counts calls in ``history_calls``;
* ``positions`` / ``pending`` are plain lists tests mutate mid-scenario
  (ambiguous-failure recovery scenarios re-query them between sends).
"""

from collections import namedtuple
from types import SimpleNamespace

#: Minimal symbol info shape for orb.symbols resolution tests.
SymbolInfo = namedtuple("SymbolInfo", ["name", "visible", "trade_mode"])

#: Minimal history deal shape for Mt5Broker.deal_profit tests.
Deal = namedtuple("Deal", ["ticket", "order", "position_id", "profit"])


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    SYMBOL_TRADE_MODE_FULL = 4

    def __init__(self, trade_mode=0):
        self.trade_mode = trade_mode
        self.sent = []            # every order_send request, in order
        self.positions = []       # mutable mid-scenario
        self.pending = []         # mutable mid-scenario
        self.bid = 4182.00        # mutable tick
        self.ask = 4182.30
        self.tick_time = None     # set to int to expose tick.time
        self.fill_price = None    # override result.price when set
        self.retcode_script = []  # consumed per order_send call, then DONE
        self.symbols = []         # SymbolInfo list for symbols_get()
        self.symbols_calls = 0    # symbols_get call counter (cache tests)
        self.deals = []           # Deal list for history_deals_get()
        self.history_calls = 0    # history_deals_get call counter
        self.next_order = 111
        self.next_deal = 222

    # ------------------------------------------------------------------ #
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
        tick = SimpleNamespace(bid=self.bid, ask=self.ask)
        if self.tick_time is not None:
            tick.time = self.tick_time
        return tick

    def symbols_get(self, group=None):
        self.symbols_calls += 1
        return list(self.symbols)

    def positions_get(self, symbol=None):
        return list(self.positions)

    def orders_get(self, symbol=None):
        return list(self.pending)

    def history_deals_get(self, *args, **kwargs):
        self.history_calls += 1
        ticket = kwargs.get("ticket")
        if ticket is not None:
            return [d for d in self.deals if d.ticket == ticket]
        return list(self.deals)

    def order_send(self, request):
        self.sent.append(request)
        retcode = self.TRADE_RETCODE_DONE
        if self.retcode_script:
            retcode = self.retcode_script.pop(0)
        if retcode is None:
            return None
        price = request.get("price", 0.0)
        if self.fill_price is not None:
            price = self.fill_price
        ok = retcode == self.TRADE_RETCODE_DONE
        return SimpleNamespace(retcode=retcode, order=self.next_order,
                               deal=self.next_deal, price=price,
                               volume=request.get("volume", 0.0),
                               comment="done" if ok else f"ret={retcode}")
