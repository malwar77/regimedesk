"""Position sizing and trend-following core vs hand-calculated values."""
import math

import pytest

from core.position_sizing import calculate_position_size
from core.trend_following import (donchian_breakout, ma_signal,
                                  realized_volatility, time_series_momentum,
                                  trend_strength,
                                  volatility_scaled_position)


class TestPositionSizing:
    def test_reference_value(self):
        # (10000 * 1/100) / (50 * 10) = 100 / 500 = 0.2
        assert calculate_position_size(10000, 1.0, 50, 10.0) \
            == pytest.approx(0.2)

    def test_second_reference(self):
        # (5000 * 0.5/100) / (25 * 5) = 25 / 125 = 0.2
        assert calculate_position_size(5000, 0.5, 25, 5.0) \
            == pytest.approx(0.2)

    def test_validation(self):
        with pytest.raises(ValueError):
            calculate_position_size(10000, 1, 0, 10)
        with pytest.raises(ValueError):
            calculate_position_size(10000, 1, 50, 0)
        with pytest.raises(ValueError):
            calculate_position_size(-1, 1, 50, 10)


class TestTSMOM:
    def test_all_positive(self):
        assert time_series_momentum([0.01] * 12) == 1.0

    def test_hand_mixed_case(self):
        # lookbacks 1/3/6/12 over this series:
        # lb1: -0.10 -> -1 ; lb3: 0.01+0.04-0.10 = -0.05 -> -1
        # lb6: 0.05+0.02+0.01+0.01+0.04-0.10 = 0.03 -> +1 ; lb12: +0.05 -> +1
        rets = [0.02, -0.01, 0.03, 0.01, -0.02, -0.01,
                0.05, 0.02, 0.01, 0.01, 0.04, -0.10]
        assert time_series_momentum(rets) == pytest.approx(0.0)

    def test_insufficient_raises(self):
        with pytest.raises(ValueError):
            time_series_momentum([])   # nothing at all
        with pytest.raises(ValueError):
            time_series_momentum([0.01] * 5, lookback_months=[12])  # < lookback


class TestMASignal:
    def test_above_all_windows(self):
        prices = [float(i) for i in range(1, 251)]
        r = ma_signal(prices, windows=(50, 100, 200))
        assert r["signal"] == 1.0
        assert all(v == 1 for v in r["windows"].values())

    def test_below_all_windows(self):
        prices = [float(251 - i) for i in range(1, 251)]
        r = ma_signal(prices, windows=(50, 100, 200))
        assert r["signal"] == -1.0

    def test_insufficient_raises(self):
        with pytest.raises(ValueError):
            ma_signal([1.0, 2.0], windows=(50,))


class TestDonchian:
    def test_breakout_up(self):
        prices = [float(i) for i in range(1, 31)]  # prior channel 10..29
        r = donchian_breakout(prices, window=20)
        assert r["direction"] == "up"
        assert r["distance"] == pytest.approx(1.0)  # 30 - 29

    def test_inside_channel(self):
        prices = [float(i) for i in range(1, 21)] + [15.0]
        assert donchian_breakout(prices, window=20)["direction"] == "inside"

    def test_no_lookahead_channel_excludes_current_bar(self):
        # current bar IS the channel high; the prior-channel rule means
        # a NEW high above the previous 20 bars, not its own inclusion
        prices = [float(i) for i in range(1, 21)] + [20.5]
        assert donchian_breakout(prices, window=20)["direction"] == "up"


class TestTrendStrength:
    def test_perfect_linear(self):
        r = trend_strength([float(i) for i in range(1, 51)], window=50)
        assert r["r2"] == pytest.approx(1.0, abs=1e-9)
        assert r["slope"] > 0

    def test_flat_is_zero(self):
        r = trend_strength([5.0] * 50, window=50)
        assert r["r2"] == 0.0


class TestVolScaledPosition:
    def test_scaling(self):
        assert volatility_scaled_position(1.0, 0.02, 0.01) \
            == pytest.approx(0.5)
        assert volatility_scaled_position(-1.0, 0.02, 0.01) \
            == pytest.approx(-0.5)

    def test_cap(self):
        assert volatility_scaled_position(1.0, 0.001, 0.01) == 1.0
        assert volatility_scaled_position(1.0, 0.01, 0.01) == 1.0

    def test_zero_vol_returns_zero(self):
        assert volatility_scaled_position(1.0, 0.0, 0.01) == 0.0


def test_realized_volatility_constant():
    assert realized_volatility([0.01] * 30) == pytest.approx(0.0)
