"""Exit-strategy math vs hand-calculated reference values — off-by-one
errors in swing/period selection are the classic bug class here."""
import pytest

from core.exit_strategy import (chandelier_stop, chandelier_stop_short,
                                fib_extension_target, parabolic_sar,
                                swing_pivot_targets)


class TestChandelierStop:
    def test_long_reference(self):
        # highest of last 5 highs = 14; 14 - 3*1 = 11
        highs = [10.0, 11.0, 12.0, 13.0, 14.0]
        assert chandelier_stop(highs, atr=1.0, period=5, multiplier=3.0) \
            == pytest.approx(11.0)

    def test_uses_only_last_period_bars(self):
        # an earlier higher spike (20) must NOT affect a period-5 stop
        highs = [20.0, 10.0, 11.0, 12.0, 13.0, 14.0]
        assert chandelier_stop(highs, atr=1.0, period=5, multiplier=3.0) \
            == pytest.approx(11.0)

    def test_short_reference(self):
        lows = [10.0, 11.0, 12.0, 13.0, 14.0]
        assert chandelier_stop_short(lows, atr=1.0, period=5, multiplier=3.0) \
            == pytest.approx(13.0)  # lowest low 10 + 3*1

    def test_needs_enough_bars(self):
        with pytest.raises(ValueError):
            chandelier_stop([1.0, 2.0], atr=1.0, period=5)


class TestParabolicSAR:
    def bars(self):
        # hand-worked series: no reversal, clamp binds only at i=2
        return [
            {"high": 50.0, "low": 30.0, "close": 49.0},
            {"high": 52.0, "low": 50.0, "close": 51.5},
            {"high": 53.0, "low": 51.0, "close": 52.0},
            {"high": 54.0, "low": 52.0, "close": 53.0},
        ]

    def test_reference_series(self):
        # sar0 = 30 (l0), ep = 50, af = 0.02
        # i1: 30 + 0.02*(50-30) = 30.4 ; ep=52, af=0.04
        # i2: 30.4 + 0.04*(52-30.4) = 31.264 -> clamp vs l0=30 -> 30 ; ep=53, af=0.06
        # i3: 30 + 0.06*(53-30) = 31.38
        sar = parabolic_sar(self.bars())
        assert sar == pytest.approx([30.0, 30.4, 30.0, 31.38], abs=1e-9)

    def test_reversal_flips_to_prior_ep(self):
        bars = [
            {"high": 50.0, "low": 30.0, "close": 49.0},
            {"high": 52.0, "low": 50.0, "close": 51.5},
            {"high": 55.0, "low": 29.0, "close": 30.0},  # pierces sar (30)
        ]
        sar = parabolic_sar(bars)
        # reversal at i=2: sar becomes the prior EP (52), trend flips down
        assert sar[-1] == pytest.approx(52.0, abs=1e-9)

    def test_needs_two_bars(self):
        with pytest.raises(ValueError):
            parabolic_sar([{"high": 1, "low": 1, "close": 1}])


class TestFibExtension:
    def test_bullish_reference_values(self):
        # swing 1.2000 -> 1.2100, distance 0.0100
        assert fib_extension_target(1.2000, 1.2100, 0.382) \
            == pytest.approx(1.21382)
        assert fib_extension_target(1.2000, 1.2100, 0.618) \
            == pytest.approx(1.21618)
        assert fib_extension_target(1.2000, 1.2100, 1.0) \
            == pytest.approx(1.2200)

    def test_bearish_impulse(self):
        # swing_high given as 1.2000 (< swing_low 1.2100): bearish impulse
        # target = 1.2000 - 0.618 * 0.0100
        assert fib_extension_target(1.2100, 1.2000, 0.618) \
            == pytest.approx(1.19382)

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            fib_extension_target(1.2000, 1.2100, 0.0)
        with pytest.raises(ValueError):
            fib_extension_target(1.2000, 1.2000, 0.618)  # zero distance


class TestSwingPivotTargets:
    def test_explicit_lookback_pivots(self):
        bars = [
            {"high": 10.0, "low": 9.0, "timestamp": "t0"},
            {"high": 11.0, "low": 9.5, "timestamp": "t1"},
            {"high": 12.0, "low": 10.0, "timestamp": "t2"},   # pivot high
            {"high": 11.5, "low": 10.0, "timestamp": "t3"},
            {"high": 11.0, "low": 8.0, "timestamp": "t4"},    # pivot low
            {"high": 10.0, "low": 8.5, "timestamp": "t5"},
            {"high": 10.5, "low": 9.0, "timestamp": "t6"},
        ]
        assert swing_pivot_targets(bars, lookback=7) == [12.0, 8.0]

    def test_lookback_is_respected(self):
        bars = [
            {"high": 10.0, "low": 9.0, "timestamp": "t0"},
            {"high": 11.0, "low": 9.5, "timestamp": "t1"},
            {"high": 12.0, "low": 10.0, "timestamp": "t2"},
        ]
        # a 3-bar lookback cannot confirm a k=2 pivot (needs neighbors)
        assert swing_pivot_targets(bars, lookback=3) == []
        with pytest.raises(ValueError):
            swing_pivot_targets(bars, lookback=99)
