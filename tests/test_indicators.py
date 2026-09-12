"""Unit tests for core indicator math — reference values are HAND-CALCULATED,
not generated from the implementation."""
import pytest

from core.indicators import (atr, classify_slope, ema, ema_series,
                             linear_regression, rsi, rsi_state, sma,
                             swing_pivots)


def test_sma():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0  # (3+4+5)/3


class TestEMA:
    def test_reference_series(self):
        # period 3, seed = SMA(1,2,3)=2, k=2/(3+1)=0.5
        # i3: 2 + 0.5*(4-2) = 3 ; i4: 3 + 0.5*(5-3) = 4
        series = ema_series([1, 2, 3, 4, 5], 3)
        assert series[:2] == [None, None]
        assert abs(series[2] - 2.0) < 1e-12
        assert abs(series[3] - 3.0) < 1e-12
        assert abs(series[4] - 4.0) < 1e-12

    def test_ema_last(self):
        assert abs(ema([1, 2, 3, 4, 5], 3) - 4.0) < 1e-12

    def test_insufficient_data(self):
        with pytest.raises(ValueError):
            ema_series([1, 2], 3)

    def test_rising_closes_give_rising_ema(self):
        closes = [float(i) for i in range(1, 301)]
        for period in (20, 50, 200):
            assert classify_slope(ema_series(closes, period), lookback=10) \
                == "rising"


class TestRSI:
    def test_hand_calculated_wilder(self):
        # closes [10,11,10,12,13], period 3.
        # changes: +1,-1,+2,+1 -> first avg gain 1.0, avg loss 1/3 (RSI=75)
        # Wilder smoothing of the 4th change:
        # avg_gain = (1.0*2 + 1)/3 = 1.0 ; avg_loss = (1/3*2 + 0)/3 = 2/9
        # RSI = 100 * 1 / (1 + 2/9) = 900/11 = 81.8181...
        assert abs(rsi([10, 11, 10, 12, 13], period=3) - 900 / 11) < 1e-9

    def test_all_rising_is_100(self):
        assert rsi([float(i) for i in range(1, 30)], period=14) == 100.0

    def test_all_falling_is_0(self):
        assert rsi([float(30 - i) for i in range(30)], period=14) == 0.0

    def test_constant_is_50(self):
        assert rsi([5.0] * 30, period=14) == 50.0

    def test_state_thresholds(self):
        assert rsi_state(71) == "overbought"
        assert rsi_state(70) == "neutral"
        assert rsi_state(30) == "neutral"
        assert rsi_state(29.9) == "oversold"
        assert rsi_state(50) == "neutral"


class TestATR:
    def test_hand_calculated_wilder(self):
        # bars (h,l,c): (10,8,9),(12,9,10),(11,10,10.5),(12,10.5,11); period 2
        # TRs: 3, 1, 1.5 -> init (3+1)/2 = 2 -> (2*1+1.5)/2 = 1.75
        highs = [10, 12, 11, 12]
        lows = [8, 9, 10, 10.5]
        closes = [9, 10, 10.5, 11]
        assert abs(atr(highs, lows, closes, period=2) - 1.75) < 1e-12


class TestSlope:
    def test_rising_flat_falling(self):
        assert classify_slope([float(i) for i in range(20)]) == "rising"
        assert classify_slope([5.0] * 20) == "flat"
        assert classify_slope([float(20 - i) for i in range(20)]) == "falling"

    def test_deadband(self):
        # 0.05% drift over 10 periods < 0.1% threshold -> flat
        vals = [100.0 * (1 + 0.00005 * i) for i in range(20)]
        assert classify_slope(vals, lookback=10, rel_threshold=0.001) == "flat"


class TestRegression:
    def test_perfect_linear(self):
        slope, r2 = linear_regression([1, 2, 3, 4, 5])
        assert abs(slope - 1.0) < 1e-12
        assert abs(r2 - 1.0) < 1e-12

    def test_hand_case(self):
        # values [5,1,4,2,3]: Sxy = -3, Sxx = 10, Syy = 10
        # slope = -0.3 ; r2 = Sxy^2 / (Sxx*Syy) = 9/100 = 0.09
        slope, r2 = linear_regression([5, 1, 4, 2, 3])
        assert abs(slope + 0.3) < 1e-12
        assert abs(r2 - 0.09) < 1e-12

    def test_flat_series_no_trend(self):
        slope, r2 = linear_regression([7.0] * 5)
        assert slope == 0.0 and r2 == 0.0


class TestSwingPivots:
    def test_hand_built_pivots(self):
        bars = [
            {"high": 10.0, "low": 9.0, "timestamp": "t0"},
            {"high": 11.0, "low": 9.5, "timestamp": "t1"},
            {"high": 12.0, "low": 10.0, "timestamp": "t2"},   # pivot high
            {"high": 11.5, "low": 10.0, "timestamp": "t3"},
            {"high": 11.0, "low": 8.0, "timestamp": "t4"},    # pivot low
            {"high": 10.0, "low": 8.5, "timestamp": "t5"},
            {"high": 10.5, "low": 9.0, "timestamp": "t6"},
        ]
        pivots = swing_pivots(bars, k=2)
        assert [(p["index"], p["kind"], p["price"]) for p in pivots] == \
            [(2, "high", 12.0), (4, "low", 8.0)]
