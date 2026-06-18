import math

import pytest

from orb import ROC, IndicatorError, VolumeSMA, WilderATR


def test_atr_not_ready_before_period():
    atr = WilderATR(3)
    atr.update(10, 9, 9.5)
    atr.update(11, 10, 10.5)
    assert not atr.ready
    assert atr.value is None
    atr.update(12, 11, 11.5)
    assert atr.ready  # ready at the period-th bar


def test_atr_known_answer():
    # TR sequence with a known Wilder smoothing result.
    atr = WilderATR(3)
    bars = [(10, 8, 9), (11, 9, 10), (12, 10, 11), (20, 11, 19)]
    for h, l, c in bars:
        atr.update(h, l, c)
    # TRs: bar0 = 10-8 = 2; bar1 = max(2,|11-9|,|9-9|) = 2; bar2 = max(2,|12-10|,|10-10|) = 2
    # seed ATR = mean(2,2,2) = 2 (prev_close after bar2 = 11)
    # bar3 TR = max(20-11, |20-11|, |11-11|) = 9; ATR = (2*2 + 9)/3 = 13/3
    assert math.isclose(atr.value, 13 / 3, rel_tol=1e-9)


def test_roc_ready_and_value():
    roc = ROC(2)
    roc.update(100)
    roc.update(101)
    assert not roc.ready
    roc.update(110)  # 3 closes -> ready (period+1)
    assert roc.ready
    assert math.isclose(roc.value, 10.0, rel_tol=1e-9)  # (110/100 - 1)*100


def test_roc_zero_divisor_raises():
    roc = ROC(1)
    roc.update(0.0)
    roc.update(5.0)
    with pytest.raises(IndicatorError):
        _ = roc.value


def test_volume_sma_rolling():
    vs = VolumeSMA(3)
    for v in (10, 20, 30):
        vs.update(v)
    assert vs.ready
    assert math.isclose(vs.value, 20.0)
    vs.update(60)  # window now (20,30,60)
    assert math.isclose(vs.value, (20 + 30 + 60) / 3)


def test_non_finite_input_raises():
    atr = WilderATR(2)
    with pytest.raises(IndicatorError):
        atr.update(float("nan"), 1, 1)
