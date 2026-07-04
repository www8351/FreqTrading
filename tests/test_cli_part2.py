"""Part 2 CLI wiring (orb/cli.py live mode): gates, sizing/guards, pipeline.

Harness: ``main(["live", ...])`` end-to-end with
* a module-level async ``feed()`` factory (``--source tests.test_cli_part2:feed``)
  serving scripted Candles that deterministically trigger ORB entries
  (same shape as tests/fixtures/asian_session_long.csv), and
* ``orb.broker.Mt5Broker`` monkeypatched to a subclass that injects
  :class:`CliFakeMt5` (a position-filling extension of tests/_fakemt5.FakeMt5),
  so the REAL broker adapter runs against a fake MT5 terminal.

Every new flag defaults off; the spy-broker test pins the no-flags call
sequence so the Part 2 gates provably change nothing when disabled.
"""

import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import orb.broadcast as broadcast_mod
import orb.broker as broker_pkg
import orb.symbols as symbols_mod
from orb.broker import Mt5Broker
from orb.broker.retcodes import RetryPolicy
from orb.cli import build_parser, main
from orb.models import Candle
from orb.svp import compute_lot

from ._fakemt5 import Deal, FakeMt5, SymbolInfo

ORB_MAGIC = 20260610

# ORB engine flags: 3-bar range, entry fires at 00:03 (price 2005.0,
# stop 2000.666...), range_reentry exit at 00:05 (verified via replay).
BASE_FLAGS = ["--range-min", "3", "--atr-period", "3", "--roc-period", "2",
              "--roc-min", "0", "--qty", "0.01", "--quiet"]


def _c(minute, o, h, lo, cl, vol=100.0):
    return Candle(ts=datetime(2026, 6, 10, 0, minute, tzinfo=timezone.utc),
                  open=o, high=h, low=lo, close=cl, volume=vol)


#: One entry (00:03) + one range_reentry exit (00:05).
ENTRY_CANDLES = [
    _c(0, 2000, 2000.5, 1999.5, 2000),
    _c(1, 2000, 2001, 1999, 2000),
    _c(2, 2000, 2000.5, 1999.5, 2000),
    _c(3, 2002, 2006, 2001.5, 2005, 120),
    _c(4, 2004, 2005, 2003, 2004, 110),
    _c(5, 2004, 2004.5, 1998, 1998, 150),
]

#: With --rearm: entry 00:03, losing exit 00:05, second entry 00:09
#: (verified via replay: entries=2 exits=1).
TWO_CYCLE_CANDLES = ENTRY_CANDLES + [
    _c(6, 1998, 1998.5, 1997.5, 1998),
    _c(7, 1998, 1999, 1997, 1998),
    _c(8, 1998, 1998.5, 1997.5, 1998),
    _c(9, 2000, 2004, 1999.5, 2003, 120),
    _c(10, 2002, 2003, 2001, 2002, 110),
]

_FEED: list = []  # candles served by feed(); set per test by _run_live


def feed():
    """--source factory: async iterator over the module-level _FEED list."""
    async def _gen():
        for c in list(_FEED):
            yield c
    return _gen()


class CliFakeMt5(FakeMt5):
    """FakeMt5 that FILLS orders: entry deals open positions, position-
    targeted deals close them, SLTP requests update the stored SL/TP —
    so the babysit/close/event paths behave like a real terminal."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bid, self.ask = 2004.80, 2005.00  # near the scripted signals
        self.point = 0.01
        self.specs = dict(trade_tick_value=1.0, trade_tick_size=0.01,
                          volume_min=0.01, volume_step=0.01, volume_max=50.0)
        self._next_ticket = 501

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=1, point=self.point, **self.specs)

    def order_send(self, request):
        res = super().order_send(request)
        if res is None or res.retcode != self.TRADE_RETCODE_DONE:
            return res
        if request.get("action") == self.TRADE_ACTION_DEAL:
            pos_ticket = request.get("position")
            if pos_ticket is None:  # entry -> open a position
                self.positions.append(SimpleNamespace(
                    ticket=self._next_ticket, magic=request.get("magic"),
                    volume=request["volume"],
                    type=(self.POSITION_TYPE_BUY
                          if request["type"] == self.ORDER_TYPE_BUY
                          else self.POSITION_TYPE_SELL),
                    sl=request.get("sl", 0.0), tp=request.get("tp", 0.0),
                    price_open=res.price))
                self._next_ticket += 1
            else:  # (partial) close
                for p in list(self.positions):
                    if p.ticket == pos_ticket:
                        p.volume = round(p.volume - request["volume"], 8)
                        if p.volume <= 1e-9:
                            self.positions.remove(p)
        elif request.get("action") == self.TRADE_ACTION_SLTP:
            for p in self.positions:
                if p.ticket == request.get("position"):
                    p.sl = request.get("sl", p.sl)
                    p.tp = request.get("tp", p.tp)
        return res


def _test_broker(fake):
    """Mt5Broker subclass injecting ``fake``; records ctor kwargs/instances."""
    class _TestBroker(Mt5Broker):
        created: list = []
        instances: list = []

        def __init__(self, **kw):
            kw.setdefault("mt5", fake)
            type(self).created.append(dict(kw))
            super().__init__(**kw)
            type(self).instances.append(self)
    return _TestBroker


_SPIED = ("connect", "execute", "update_stop", "close_all", "cancel_pending",
          "cancel_expired", "has_position", "has_pending", "close_ticket",
          "modify_sl", "balance", "symbol_specs", "current_spread",
          "deal_profit", "my_positions", "shutdown")


def _spy_broker(fake, calls):
    """TestBroker whose public methods append their name to ``calls``."""
    base = _test_broker(fake)
    ns = {}
    for name in _SPIED:
        def _make(n):
            def _m(self, *a, **kw):
                calls.append(n)
                return getattr(super(spy_cls, self), n)(*a, **kw)
            return _m
        ns[name] = _make(name)
    spy_cls = type("_SpyBroker", (base,), ns)
    return spy_cls


def _run_live(monkeypatch, capsys, fake, candles, extra=(),
              symbol="XAUUSD.ecn", broker_cls=None):
    monkeypatch.setattr(sys.modules[__name__], "_FEED", list(candles))
    cls = broker_cls or _test_broker(fake)
    monkeypatch.setattr(broker_pkg, "Mt5Broker", cls)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)  # --resolve-symbol
    rc = main(["live", "--source", f"{__name__}:feed", "--broker", "mt5",
               "--symbol", symbol, *BASE_FLAGS, *list(extra)])
    return rc, capsys.readouterr(), cls


def _entry_deals(fake):
    return [r for r in fake.sent
            if r.get("action") == fake.TRADE_ACTION_DEAL and "position" not in r]


def _close_deals(fake):
    return [r for r in fake.sent
            if r.get("action") == fake.TRADE_ACTION_DEAL and "position" in r]


@pytest.fixture(autouse=True)
def _fresh_symbol_cache():
    symbols_mod.clear_cache()
    yield
    symbols_mod.clear_cache()


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
def test_new_flags_default_off():
    args = build_parser().parse_args(["live"])
    assert args.max_spread is None
    assert args.killzones is None
    assert args.resolve_symbol is False
    assert args.retry_policy == "off"
    assert args.max_retries == 3
    assert args.max_slippage is None
    assert args.slippage_policy == "keep"
    assert args.rr_floor is None
    assert args.risk_pct is None
    assert args.max_consec_losses == 0
    assert args.trade_log is None
    assert args.broadcast is None
    assert args.broadcast_spool == "broadcast_spool.jsonl"


# --------------------------------------------------------------------------- #
# T9 gates
# --------------------------------------------------------------------------- #
def test_spread_skip_blocks_entry(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.bid, fake.ask = 2004.50, 2005.00              # spread 0.50
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--max-spread", "0.2"])
    assert rc == 0
    assert "# SPREAD_SKIP" in cap.err
    assert "spread=0.5" in cap.err and "max=0.2" in cap.err
    assert _entry_deals(fake) == []                    # no broker.execute


def test_spread_gate_allows_within_cap(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.bid, fake.ask = 2004.50, 2005.00              # spread 0.50
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--max-spread", "0.5"])  # at the cap = allowed
    assert rc == 0
    assert "# SPREAD_SKIP" not in cap.err
    assert len(_entry_deals(fake)) == 1


def test_killzone_skip_outside_window(monkeypatch, capsys):
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--killzones", "12:00-16:00"])
    assert rc == 0
    assert "# KILLZONE_SKIP" in cap.err                # entry ts is 00:03 UTC
    assert _entry_deals(fake) == []


def test_killzone_allows_inside_window(monkeypatch, capsys):
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--killzones", "00:00-06:00"])
    assert rc == 0
    assert "# KILLZONE_SKIP" not in cap.err
    assert len(_entry_deals(fake)) == 1


def test_resolve_symbol_overrides_at_startup(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.symbols = [SymbolInfo("EURUSD.ecn", True, 4),
                    SymbolInfo("XAUUSDm", False, 0),
                    SymbolInfo("XAUUSD.ecn", True, 4)]
    rc, cap, cls = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                             symbol="XAUUSD", extra=["--resolve-symbol"])
    assert rc == 0
    assert "# symbol_resolved XAUUSD -> XAUUSD.ecn" in cap.err
    assert cls.created[0]["symbol"] == "XAUUSD.ecn"    # resolved pre-construction
    assert len(_entry_deals(fake)) == 1


def test_retry_policy_on_passes_retrypolicy(monkeypatch, capsys):
    fake = CliFakeMt5()
    rc, cap, cls = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                             extra=["--retry-policy", "on",
                                    "--max-retries", "5"])
    assert rc == 0
    retry = cls.created[0].get("retry")
    assert isinstance(retry, RetryPolicy) and retry.max_retries == 5


def test_max_slippage_sets_deviation_from_point(monkeypatch, capsys):
    fake = CliFakeMt5()                                # point = 0.01
    rc, cap, cls = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES[:5],
                             extra=["--max-slippage", "0.15"])
    assert rc == 0
    assert cls.instances[0].deviation == 15            # 0.15 / 0.01


def test_slippage_breach_keep_logs_alert_keeps_position(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.fill_price = 2005.5   # 0.5 adverse vs signal price 2005.0
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES[:5],
                           extra=["--max-slippage", "0.2", "--tp-rrr", "2"])
    assert rc == 0
    assert "# FILL slippage=0.5" in cap.err
    assert "rr_planned=2.00" in cap.err and "rr_achieved=1.69" in cap.err
    assert "# ALERT" in cap.err and "policy=keep" in cap.err
    assert len(fake.positions) == 1                    # kept open
    assert _close_deals(fake) == []


def test_slippage_breach_close_policy_closes(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.fill_price = 2005.5
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES[:5],
                           extra=["--max-slippage", "0.2", "--tp-rrr", "2",
                                  "--slippage-policy", "close"])
    assert rc == 0
    assert "# ALERT" in cap.err and "policy=close" in cap.err
    closes = _close_deals(fake)
    assert len(closes) == 1
    assert closes[0]["comment"] == "orb:slippage_abort"
    assert fake.positions == []


# --------------------------------------------------------------------------- #
# T10 sizing / guards
# --------------------------------------------------------------------------- #
def test_risk_pct_sizes_orb_entry_via_compute_lot(monkeypatch, capsys):
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--risk-pct", "2.0", "--json"])
    assert rc == 0
    entry = json.loads(next(l for l in cap.out.splitlines() if '"ENTRY"' in l))
    expected = compute_lot(522.62, 2.0, entry["price"], entry["stop"],
                           100.0, 0.01, 0.01, 50.0)   # balance/specs from fake
    assert expected > 0
    assert "# ORB_SIZE lot=" in cap.err
    deals = _entry_deals(fake)
    assert len(deals) == 1 and deals[0]["volume"] == expected


def test_risk_pct_lot_zero_skips_entry(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.specs["volume_min"] = 1.0                     # min lot over-risks
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--risk-pct", "2.0"])
    assert rc == 0
    assert "# ORB_SKIP lot=0" in cap.err
    assert _entry_deals(fake) == []


def test_default_fixed_qty_without_risk_flag(monkeypatch, capsys):
    fake = CliFakeMt5()
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES)
    assert rc == 0
    assert "# ORB_SIZE" not in cap.err
    deals = _entry_deals(fake)
    assert len(deals) == 1 and deals[0]["volume"] == 0.01


def test_consec_guard_blocks_after_losses(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.deals = [Deal(ticket=222, order=111, position_id=501, profit=-5.0)]
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, TWO_CYCLE_CANDLES,
                           extra=["--rearm", "--max-consec-losses", "1"])
    assert rc == 0
    assert "# CONSEC_SKIP" in cap.err
    assert len(_entry_deals(fake)) == 1                # second entry blocked
    assert len(_close_deals(fake)) == 1                # the losing close


def test_consec_guard_off_by_default(monkeypatch, capsys):
    fake = CliFakeMt5()
    fake.deals = [Deal(ticket=222, order=111, position_id=501, profit=-5.0)]
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, TWO_CYCLE_CANDLES,
                           extra=["--rearm"])
    assert rc == 0
    assert "# CONSEC_SKIP" not in cap.err
    assert len(_entry_deals(fake)) == 2                # both entries execute


# --------------------------------------------------------------------------- #
# T11 pipeline
# --------------------------------------------------------------------------- #
def test_trade_log_written_on_entry_and_exit(monkeypatch, capsys, tmp_path):
    fake = CliFakeMt5()
    fake.deals = [Deal(ticket=222, order=111, position_id=501, profit=-5.0)]
    path = tmp_path / "events.jsonl"
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           extra=["--trade-log", str(path)])
    assert rc == 0
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    actions = [l["action"] for l in lines]
    assert "open" in actions and "close" in actions
    open_ev = lines[actions.index("open")]
    assert open_ev["schema_version"] == 1
    assert open_ev["symbol"] == "XAUUSD.ecn"
    assert open_ev["base_symbol"] == "XAUUSD"
    assert open_ev["source"]["strategy"] == "orb"
    assert open_ev["source"]["magic"] == ORB_MAGIC
    close_ev = lines[actions.index("close")]
    assert close_ev["pnl"] == -5.0
    assert close_ev["reason"] == "range_reentry"


def test_broadcast_missing_secret_env_is_fatal(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("COPYTRADE_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)                        # dodge any repo .env
    fake = CliFakeMt5()
    rc, cap, cls = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                             extra=["--broadcast", "http://127.0.0.1:1/events"])
    assert rc == 2
    assert "COPYTRADE_SECRET" in cap.err
    assert cls.created == []                           # before broker construction
    assert fake.sent == []


class _StubBroadcaster:
    def __init__(self, url, secret, *, spool_path="broadcast_spool.jsonl", **kw):
        self.url, self.secret, self.spool_path = url, secret, spool_path
        self.published: list = []
        self.closed = False

    def publish(self, payload):
        self.published.append(payload)

    def close(self, drain_sec=2.0):
        self.closed = True


def test_events_flow_through_hub_to_both_sinks(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("COPYTRADE_SECRET", "hunter2")
    made: list = []

    def _factory(url, secret, **kw):
        b = _StubBroadcaster(url, secret, **kw)
        made.append(b)
        return b

    monkeypatch.setattr(broadcast_mod, "Broadcaster", _factory)
    fake = CliFakeMt5()
    fake.deals = [Deal(ticket=222, order=111, position_id=501, profit=-5.0)]
    path = tmp_path / "events.jsonl"
    spool = tmp_path / "spool.jsonl"
    rc, cap, _ = _run_live(
        monkeypatch, capsys, fake, ENTRY_CANDLES,
        extra=["--trade-log", str(path),
               "--broadcast", "http://leader.local/events",
               "--broadcast-spool", str(spool)])
    assert rc == 0
    assert len(made) == 1
    b = made[0]
    assert b.url == "http://leader.local/events"
    assert b.secret == b"hunter2"                      # env secret, encoded
    assert b.spool_path == str(spool)
    assert b.closed is True                            # closed in the finally
    log_lines = [json.loads(l) for l in path.read_text().splitlines()
                 if l.strip()]
    assert [p["event_id"] for p in b.published] \
        == [l["event_id"] for l in log_lines]          # same events, same order
    assert {p["action"] for p in b.published} >= {"open", "close"}


# --------------------------------------------------------------------------- #
# No flags = byte-identical broker interaction
# --------------------------------------------------------------------------- #
#: Pinned pre-Part-2 sequence for ENTRY_CANDLES against a position-filling
#: terminal: connect; 00:03 ENTRY execute + BREAKOUT-bar stop sync; 00:04
#: stop sync; 00:05 EXIT execute (close); shutdown.
BASELINE_CALLS = [
    "connect",
    "execute", "has_position", "my_positions", "update_stop", "my_positions",
    "has_position", "my_positions", "update_stop", "my_positions",
    "execute", "my_positions",
    "shutdown",
]


def test_no_new_flags_on_signal_path_unchanged(monkeypatch, capsys):
    fake = CliFakeMt5()
    calls: list = []
    spy = _spy_broker(fake, calls)
    rc, cap, _ = _run_live(monkeypatch, capsys, fake, ENTRY_CANDLES,
                           broker_cls=spy)
    assert rc == 0
    assert calls == BASELINE_CALLS                     # no gate/sizing calls
    assert "# FILL" not in cap.err
    assert "# retcodes" not in cap.err
    kw = spy.created[0]
    assert kw.get("on_event") is None                  # no hub without flags
    assert kw.get("retry") is None                     # single-send _send
