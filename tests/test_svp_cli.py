"""SVP cli wiring: config assembly, distinct magic, dynamic-qty market entry."""

from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace

from orb.broker import Mt5Broker
from orb.cli import build_svp_config
from orb.models import Direction, Signal, SignalKind, State
from orb.svp import SVP_MAGIC, compute_lot

TS = datetime(2026, 6, 10, 2, 7, tzinfo=timezone.utc)


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self):
        self.sent = []
        self.positions = []

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(login=1, server="Demo", trade_mode=0,
                               balance=500.0, currency="USD")

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info(self, symbol):
        return SimpleNamespace(filling_mode=1, trade_tick_value=1.0,
                               trade_tick_size=0.01, volume_min=0.01,
                               volume_step=0.01, volume_max=50.0)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=2000.00, ask=2000.10)

    def positions_get(self, symbol=None):
        return list(self.positions)

    def orders_get(self, symbol=None):
        return []

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=1, deal=2,
                               price=request.get("price", 0.0),
                               volume=request.get("volume", 0.0), comment="done")


def _svp_args(**ov):
    base = dict(session_len=None, svp_ticks_per_row=None, svp_tick_size=None,
                svp_va_pct=None, svp_hvn_frac=None, svp_lvn_frac=None,
                svp_risk_pct=None, svp_min_bars=None, svp_buffer_ticks=None,
                svp_enable_lvn=False, svp_enable_absorption=False,
                long_only=False, short_only=False, session_open=None)
    base.update(ov)
    return Namespace(**base)


def test_svp_magic_is_distinct_from_orb():
    assert SVP_MAGIC == 20260620
    assert SVP_MAGIC != 20260610


def test_build_svp_config_maps_flags():
    cfg = build_svp_config(_svp_args(
        session_len=720, svp_ticks_per_row=25, svp_va_pct=0.68, svp_risk_pct=3.0,
        svp_min_bars=15, svp_buffer_ticks=5.0, svp_enable_lvn=True))
    assert cfg.session_len_min == 720 and cfg.ticks_per_row == 25
    assert cfg.value_area_pct == 0.68 and cfg.risk_pct == 3.0
    assert cfg.min_session_bars == 15 and cfg.stop_buffer_ticks == 5.0
    assert cfg.enable_lvn is True and cfg.enable_absorption_proxy is False


def test_build_svp_config_defaults():
    cfg = build_svp_config(_svp_args())
    assert cfg.ticks_per_row == 10 and cfg.risk_pct == 5.0
    assert cfg.enable_edge_rotation is True and cfg.enable_lvn is False


def test_broker_symbol_specs():
    b = Mt5Broker(symbol="XAUUSD.ecn", magic=SVP_MAGIC, mt5=FakeMt5(),
                  entry_mode="market", server_tp=False)
    b.connect()
    specs = b.symbol_specs()
    assert specs["value_per_move"] == 100.0      # tick_value 1.0 / tick_size 0.01
    assert specs["volume_min"] == 0.01 and specs["volume_max"] == 50.0
    assert b.magic == SVP_MAGIC


def test_svp_market_entry_carries_dynamic_qty_and_magic():
    fake = FakeMt5()
    b = Mt5Broker(symbol="XAUUSD.ecn", magic=SVP_MAGIC, mt5=fake,
                  entry_mode="market", server_tp=False)
    b.connect()
    specs = b.symbol_specs()
    lot = compute_lot(500, 5, 2000.0, 2002.0, specs["value_per_move"],
                      specs["volume_min"], specs["volume_step"], specs["volume_max"])
    assert lot == 0.12  # 25 / (2 * 100) -> 0.125 -> snap 0.12
    sig = Signal(ts=TS, kind=SignalKind.ENTRY, direction=Direction.SHORT,
                 price=2000.0, state_from=State.RANGE_DEFINED,
                 state_to=State.BREAKOUT, reason="edge_rot_vah_fade",
                 stop=2002.0, tp=None, qty=lot)
    res = b.execute(sig)
    assert res is not None
    req = fake.sent[0]
    assert req["magic"] == SVP_MAGIC
    assert req["volume"] == lot
    assert req["type"] == FakeMt5.ORDER_TYPE_SELL  # sell at bid for a short
    assert req["tp"] == 0.0                        # server_tp False (babysitter)
    # SL re-anchored to fill (bid 2000.00) + signal stop distance (2.0)
    assert abs(req["sl"] - 2002.00) < 1e-6
