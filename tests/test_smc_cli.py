"""SMC cli wiring: config assembly, distinct magic, ladder exits, always-inject qty."""

from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace

from orb.broker import Mt5Broker
from orb.cli import build_smc_config
from orb.models import Candle, Direction, Signal, SignalKind, State
from orb.smc import SMC_MAGIC, SmcConfig, SmcEngine
from orb.smc.exits import LadderExitManager
from orb.svp import compute_lot

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


def _smc_args(**ov):
    base = dict(session_len=None, smc_min_confluences=None, smc_risk_pct=None,
                smc_disp_atr_mult=None, smc_poc_tol=None, smc_stop_max_dist=None,
                smc_max_trades_per_day=None, smc_trail_mode=None, smc_final_tp_r=None,
                long_only=False, short_only=False, session_open=None)
    base.update(ov)
    return Namespace(**base)


def test_smc_magic_is_distinct():
    assert SMC_MAGIC == 20260621
    assert SMC_MAGIC != 20260610      # orb
    assert SMC_MAGIC != 20260620      # svp


def test_build_smc_config_maps_flags():
    cfg = build_smc_config(_smc_args(
        smc_min_confluences=4, smc_risk_pct=1.5, smc_disp_atr_mult=1.5,
        smc_poc_tol=3.0, smc_stop_max_dist=12.0, smc_max_trades_per_day=1,
        smc_trail_mode="atr", smc_final_tp_r=8.0))
    assert cfg.min_confluences == 4 and cfg.risk_pct == 1.5
    assert cfg.disp_atr_mult == 1.5 and cfg.poc_tol == 3.0
    assert cfg.stop_max_dist == 12.0 and cfg.max_trades_per_day == 1
    assert cfg.trail_mode == "atr" and cfg.final_tp_r == 8.0


def test_build_smc_config_defaults():
    cfg = build_smc_config(_smc_args())
    d = SmcConfig()
    assert cfg.min_confluences == d.min_confluences
    assert cfg.risk_pct == d.risk_pct
    assert cfg.trail_mode == d.trail_mode
    assert cfg.final_tp_r == d.final_tp_r


def test_smc_broker_magic_server_tp_and_entry_mode():
    # cli builds the broker for smc with SMC_MAGIC / server_tp False / market
    b = Mt5Broker(symbol="XAUUSD.ecn", magic=SMC_MAGIC, mt5=FakeMt5(),
                  entry_mode="market", server_tp=False)
    b.connect()
    assert b.magic == SMC_MAGIC
    assert b.server_tp is False
    assert b.entry_mode == "market"


def test_smc_market_entry_always_injects_dynamic_qty():
    fake = FakeMt5()
    b = Mt5Broker(symbol="XAUUSD.ecn", magic=SMC_MAGIC, mt5=fake,
                  entry_mode="market", server_tp=False)
    b.connect()
    specs = b.symbol_specs()
    bal = b.balance()
    lot = compute_lot(bal, 2.0, 2000.0, 2002.0, specs["value_per_move"],
                      specs["volume_min"], specs["volume_step"], specs["volume_max"])
    assert lot > 0
    sig = Signal(ts=TS, kind=SignalKind.ENTRY, direction=Direction.SHORT,
                 price=2000.0, state_from=State.RANGE_DEFINED,
                 state_to=State.BREAKOUT, reason="smc_a_plus",
                 stop=2002.0, tp=None, qty=lot)
    res = b.execute(sig)
    assert res is not None
    req = fake.sent[0]
    assert req["magic"] == SMC_MAGIC
    assert req["volume"] == lot          # ALWAYS injected (never default_qty)
    assert req["type"] == FakeMt5.ORDER_TYPE_SELL
    assert req["tp"] == 0.0              # server_tp False (ladder owns exits)


def test_ladder_exit_manager_built_from_smc_config():
    cfg = SmcConfig()
    ladder = LadderExitManager(
        partial_levels=cfg.partial_levels, final_tp_r=cfg.final_tp_r,
        be_at_r=cfg.be_at_r, trail_start_r=cfg.trail_start_r,
        trail_mode=cfg.trail_mode, trail_atr_mult=cfg.trail_atr_mult,
        trail_buffer=cfg.trail_buffer, swing_lookback=cfg.swing_lookback,
        atr_period=cfg.atr_period, trail_tf_min=cfg.trigger_tf_min)
    # observe() is the smc-specific method absent on Babysitter
    assert hasattr(ladder, "observe")
    ladder.observe(Candle(ts=TS, open=2000.0, high=2001.0, low=1999.0,
                          close=2000.5, volume=10.0))
    # no positions -> no actions, clean pass
    assert ladder.on_bar([], 2000.5) == []


def test_smc_engine_replay_runs():
    eng = SmcEngine(SmcConfig())
    sigs = eng.replay([
        Candle(ts=datetime(2026, 6, 10, 0, i % 60, tzinfo=timezone.utc),
               open=2000.0, high=2001.0, low=1999.0, close=2000.5, volume=10.0)
        for i in range(5)
    ])
    assert isinstance(sigs, list)
