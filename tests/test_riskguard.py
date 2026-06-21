from datetime import date

import pytest

from orb.riskguard import DailyLossBreaker

D1 = date(2026, 6, 11)
D2 = date(2026, 6, 12)


def test_halts_at_max_daily_loss():
    b = DailyLossBreaker(110.0)
    assert b.update(D1, 500.0) is False
    assert b.update(D1, 420.0) is False   # -80, still trading
    assert b.update(D1, 390.0) is True    # -110 -> halt
    assert b.halted


def test_stays_halted_even_if_balance_recovers_same_day():
    b = DailyLossBreaker(110.0)
    b.update(D1, 500.0)
    b.update(D1, 385.0)                   # halt
    assert b.update(D1, 450.0) is True    # no un-halt same day


def test_resets_next_utc_day():
    b = DailyLossBreaker(110.0)
    b.update(D1, 500.0)
    b.update(D1, 380.0)
    assert b.halted
    assert b.update(D2, 380.0) is False   # new day, new baseline 380
    assert b.update(D2, 275.0) is False   # -105
    assert b.update(D2, 270.0) is True    # -110 again


def test_invalid_threshold():
    with pytest.raises(ValueError):
        DailyLossBreaker(0)


from orb.riskguard import SpikeCancel


def test_spike_cancel_triggers_at_ratio():
    s = SpikeCancel(ratio=2.5, lookback=20, min_bars=5)
    for _ in range(20):
        assert s.update(101.0, 100.0) is False   # steady 1.0 ranges
    assert s.update(102.4, 100.0) is False        # 2.4x avg -> no
    # avg now (19*1.0 + 2.4)/20 = 1.07 -> threshold 2.675
    assert s.update(102.7, 100.0) is True         # 2.7 >= 2.675 -> spike


def test_spike_cancel_quiet_during_warmup():
    s = SpikeCancel(ratio=2.5, min_bars=5)
    for _ in range(4):
        assert s.update(101.0, 100.0) is False
    assert s.update(110.0, 100.0) is False        # only 4 bars history


def test_spike_ratio_validation():
    import pytest
    with pytest.raises(ValueError):
        SpikeCancel(ratio=1.0)


from orb.riskguard import ConsecutiveLossGuard


def test_consec_loss_blocks_after_n_losses():
    g = ConsecutiveLossGuard(2)
    g.on_period(D1)
    g.record(-5.0)
    assert not g.blocked          # 1 loss
    g.record(-3.0)
    assert g.blocked              # 2 in a row -> stop


def test_consec_loss_win_resets_streak():
    g = ConsecutiveLossGuard(2)
    g.on_period(D1)
    g.record(-5.0)
    g.record(+10.0)               # win clears the streak
    g.record(-2.0)
    assert not g.blocked          # only 1 consecutive loss again


def test_consec_loss_resets_on_new_session():
    g = ConsecutiveLossGuard(2)
    g.on_period(D1)
    g.record(-5.0)
    g.record(-5.0)
    assert g.blocked
    g.on_period(D2)               # new session -> fresh streak
    assert not g.blocked


def test_consec_loss_breakeven_does_not_count():
    g = ConsecutiveLossGuard(2)
    g.on_period(D1)
    g.record(-5.0)
    g.record(0.0)                 # break-even: streak unchanged, not reset
    assert not g.blocked
    g.record(-5.0)
    assert g.blocked              # the two losses straddling a B/E still stop


def test_consec_loss_disabled_when_zero():
    g = ConsecutiveLossGuard(0)
    g.on_period(D1)
    for _ in range(10):
        g.record(-5.0)
    assert not g.blocked          # 0 = off
