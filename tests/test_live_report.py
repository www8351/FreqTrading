"""Tests for scripts/live_report.py against a fake mt5 module (no terminal).

The FakeMt5 stub mirrors tests/test_broker_mt5.py: injectable module with the
deal/entry/type constants and a canned history_deals_get. Cases cover:
  * two positions collapse from 4 deals (a 3-deal LONG + a 2-deal SHORT),
  * pnl summed per position (profit + commission + swap + fee),
  * direction from the entry deal type,
  * a FOREIGN magic excluded entirely,
  * build_report / main run without a real terminal.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts.live_report import KNOWN, build_report, fetch_deals, main


# --------------------------------------------------------------------------- #
def deal(position_id, magic, entry, dtype, time, volume=0.01, price=100.0,
         profit=0.0, commission=0.0, swap=0.0, symbol="XAUUSD.ecn"):
    return SimpleNamespace(
        position_id=position_id, magic=magic, entry=entry, type=dtype,
        time=time, volume=volume, price=price, profit=profit,
        commission=commission, swap=swap, symbol=symbol,
    )


BASE = int(datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc).timestamp())


class FakeMt5:
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1

    def __init__(self, deals, balance=1234.0):
        self._deals = deals
        self._balance = balance
        self.init_called = False
        self.shutdown_called = False

    def initialize(self, *a, **kw):
        self.init_called = True
        return True

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(login=1, server="Demo", trade_mode=0,
                               balance=self._balance, currency="USD")

    def history_deals_get(self, from_dt, to_dt):
        return list(self._deals)


# LONG position 100: entry-in + partial-out + final-out (3 deals).
# SHORT position 200: single in + single out (2 deals).
# FOREIGN magic position 999: must be excluded.
MAGIC = 20260621


def canned_deals():
    return [
        # LONG (magic MAGIC): in, then two out (partial + final)
        deal(100, MAGIC, FakeMt5.DEAL_ENTRY_IN, FakeMt5.DEAL_TYPE_BUY,
             BASE, volume=0.02, profit=0.0, commission=-0.20, swap=0.0),
        deal(100, MAGIC, FakeMt5.DEAL_ENTRY_OUT, FakeMt5.DEAL_TYPE_SELL,
             BASE + 600, volume=0.01, profit=5.0, commission=-0.10, swap=0.0),
        deal(100, MAGIC, FakeMt5.DEAL_ENTRY_OUT, FakeMt5.DEAL_TYPE_SELL,
             BASE + 1200, volume=0.01, profit=7.0, commission=-0.10, swap=-0.50),
        # SHORT (magic MAGIC): in + out
        deal(200, MAGIC, FakeMt5.DEAL_ENTRY_IN, FakeMt5.DEAL_TYPE_SELL,
             BASE + 100, volume=0.03, profit=0.0, commission=-0.30, swap=0.0),
        deal(200, MAGIC, FakeMt5.DEAL_ENTRY_OUT, FakeMt5.DEAL_TYPE_BUY,
             BASE + 900, volume=0.03, profit=-4.0, commission=-0.30, swap=0.0),
        # FOREIGN magic: entirely excluded
        deal(999, 11111111, FakeMt5.DEAL_ENTRY_IN, FakeMt5.DEAL_TYPE_BUY,
             BASE + 50, volume=0.05, profit=0.0, commission=-0.50),
        deal(999, 11111111, FakeMt5.DEAL_ENTRY_OUT, FakeMt5.DEAL_TYPE_SELL,
             BASE + 800, volume=0.05, profit=100.0, commission=-0.50),
    ]


# --------------------------------------------------------------------------- #
def test_fetch_deals_returns_two_records_foreign_excluded():
    fake = FakeMt5(canned_deals())
    recs = fetch_deals(fake, magic=MAGIC, days=30)
    assert len(recs) == 2  # foreign magic excluded, partials collapsed
    symbols = {r.symbol for r in recs}
    assert symbols == {"XAUUSD.ecn"}


def test_fetch_deals_pnl_summed_per_position():
    fake = FakeMt5(canned_deals())
    recs = {(_pos_key(r)): r for r in fetch_deals(fake, magic=MAGIC, days=30)}
    long = _by_direction(recs.values(), "LONG")
    short = _by_direction(recs.values(), "SHORT")
    # LONG: (0-0.20) + (5-0.10) + (7-0.10-0.50) = 11.10
    assert long.pnl == pytest.approx(11.10)
    # SHORT: (0-0.30) + (-4-0.30) = -4.60
    assert short.pnl == pytest.approx(-4.60)


def test_fetch_deals_direction_from_entry():
    recs = fetch_deals(FakeMt5(canned_deals()), magic=MAGIC, days=30)
    assert _by_direction(recs, "LONG").direction == "LONG"
    assert _by_direction(recs, "SHORT").direction == "SHORT"


def test_fetch_deals_collapses_partials_into_one_record():
    recs = fetch_deals(FakeMt5(canned_deals()), magic=MAGIC, days=30)
    long = _by_direction(recs, "LONG")
    # entry volume of the LONG = 0.02 (the in-deal volume, not summed outs)
    assert long.volume == pytest.approx(0.02)
    # open_ts is the entry-in time; close_ts is the last out-deal time
    assert long.open_ts == datetime.fromtimestamp(BASE, tz=timezone.utc)
    assert long.close_ts == datetime.fromtimestamp(BASE + 1200, tz=timezone.utc)


def test_fetch_deals_skips_position_with_no_in_deal():
    only_out = [
        deal(300, MAGIC, FakeMt5.DEAL_ENTRY_OUT, FakeMt5.DEAL_TYPE_SELL,
             BASE, profit=1.0),
    ]
    assert fetch_deals(FakeMt5(only_out), magic=MAGIC, days=30) == []


def test_fetch_deals_utc_tzaware():
    recs = fetch_deals(FakeMt5(canned_deals()), magic=MAGIC, days=30)
    for r in recs:
        assert r.open_ts.tzinfo is not None
        assert r.close_ts.tzinfo is not None


def test_build_report_non_empty_with_key_labels():
    recs = fetch_deals(FakeMt5(canned_deals()), magic=MAGIC, days=30)
    out = build_report(recs, magic=MAGIC, days=30, start_balance=1000.0)
    assert isinstance(out, str) and out
    assert "profit_factor" in out
    assert "net" in out
    assert str(MAGIC) in out
    assert KNOWN[MAGIC] in out  # "SMC" preset label present in header


def test_build_report_empty_trades():
    out = build_report([], magic=MAGIC, days=7, start_balance=1000.0)
    assert isinstance(out, str) and out
    assert "no trades" in out


def test_main_runs_and_prints(capsys):
    fake = FakeMt5(canned_deals())
    rc = main(argv=["--magic", str(MAGIC), "--days", "7"], mt5=fake)
    assert rc == 0
    captured = capsys.readouterr()
    assert str(MAGIC) in captured.out
    assert fake.init_called and fake.shutdown_called


def test_main_known_flag_iterates_presets(capsys):
    fake = FakeMt5(canned_deals())
    rc = main(argv=["--known", "--days", "7"], mt5=fake)
    assert rc == 0
    out = capsys.readouterr().out
    for magic in KNOWN:
        assert str(magic) in out


def test_main_initialize_failure_returns_nonzero(capsys):
    class DeadMt5(FakeMt5):
        def initialize(self, *a, **kw):
            return False

    rc = main(argv=["--magic", str(MAGIC)], mt5=DeadMt5(canned_deals()))
    assert rc != 0
    err = capsys.readouterr().err
    assert err  # a clear stderr message, not a crash


# --------------------------------------------------------------------------- #
def _pos_key(rec):
    return (rec.direction, rec.open_ts)


def _by_direction(recs, direction):
    for r in recs:
        if r.direction == direction:
            return r
    raise AssertionError(f"no {direction} record")
