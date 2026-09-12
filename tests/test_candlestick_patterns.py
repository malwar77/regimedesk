"""Candlestick pattern tests against hand-built OHLC cases — including the
required negative cases (a candle with body 50% of range is NOT a pin bar)."""
import pytest

from core.candlestick_patterns import (evening_star, is_doji,
                                        is_engulfing, is_marubozu, is_pin_bar,
                                        morning_star, scan_patterns)

C = dict  # candles as dicts with open/high/low/close


class TestPinBar:
    def test_bullish_pin(self):
        # range 6.5, body 1 (<= 1/3), lower wick 5, upper 0.5
        r = is_pin_bar(C(open=100, high=101.5, low=95, close=101))
        assert r["detected"] and r["direction"] == "bullish"

    def test_bearish_pin(self):
        # range 4.5, body 1, upper wick 3, lower 0.5
        r = is_pin_bar(C(open=99, high=103, low=98.5, close=100))
        assert r["detected"] and r["direction"] == "bearish"

    def test_body_half_of_range_is_not_pin(self):
        # range 4, body 3 (> 1/3 of range) -> NOT a pin bar
        r = is_pin_bar(C(open=100, high=103.5, low=99.5, close=103))
        assert not r["detected"]

    def test_wicks_too_equal_is_not_pin(self):
        # body tiny but wicks balanced -> no dominant side
        r = is_pin_bar(C(open=100, high=101.5, low=98.5, close=100.05))
        assert not r["detected"]


class TestEngulfing:
    def test_bullish_engulfing(self):
        prev = C(open=102, high=103, low=99, close=100)     # bearish body 2
        curr = C(open=99.5, high=104, low=99, close=103)    # bullish body 3.5
        r = is_engulfing(prev, curr)
        assert r["detected"] and r["direction"] == "bullish"

    def test_bearish_engulfing(self):
        prev = C(open=100, high=104, low=99.5, close=103)
        curr = C(open=103.5, high=104, low=99, close=100)
        r = is_engulfing(prev, curr)
        assert r["detected"] and r["direction"] == "bearish"

    def test_not_engulfing_when_gap_prevents(self):
        prev = C(open=102, high=103, low=99, close=100)
        curr = C(open=100.5, high=104, low=100, close=103)  # opens above prev close
        assert not is_engulfing(prev, curr)["detected"]

    def test_not_engulfing_when_body_smaller(self):
        prev = C(open=102, high=103, low=99, close=100)
        curr = C(open=100, high=104, low=99, close=101.5)   # body 1.5 < prev 2
        assert not is_engulfing(prev, curr)["detected"]


class TestDoji:
    def test_doji(self):
        # range 2, body 0.1 (<= 10%)
        r = is_doji(C(open=100, high=101, low=99, close=100.1))
        assert r["detected"]

    def test_not_doji(self):
        r = is_doji(C(open=100, high=101, low=99, close=100.5))  # body 25%
        assert not r["detected"]


class TestMarubozu:
    def test_bullish_marubozu(self):
        r = is_marubozu(C(open=100, high=110, low=100, close=110))
        assert r["detected"] and r["direction"] == "bullish"

    def test_wick_disqualifies(self):
        r = is_marubozu(C(open=100, high=110.5, low=100, close=110))
        # upper wick 0.5 > 2% of range 10.5 (~0.21)
        assert not r["detected"]

    def test_bearish_marubozu(self):
        r = is_marubozu(C(open=110, high=110, low=100, close=100))
        assert r["detected"] and r["direction"] == "bearish"


class TestStars:
    def test_morning_star(self):
        seq = [C(open=110, high=111, low=109, close=100),   # bearish, body 10
               C(open=99, high=100, low=98.5, close=99.5),  # small body 0.5
               C(open=100, high=107, low=99, close=106)]     # close > mid (105)
        assert morning_star(seq)["detected"]

    def test_morning_star_requires_close_above_mid(self):
        seq = [C(open=110, high=111, low=109, close=100),
               C(open=99, high=100, low=98.5, close=99.5),
               C(open=100, high=104, low=99, close=103)]     # close 103 < 105
        assert not morning_star(seq)["detected"]

    def test_evening_star(self):
        seq = [C(open=100, high=111, low=99, close=110),    # bullish, body 10
               C(open=111, high=112, low=110.5, close=111.5),
               C(open=110, high=110.5, low=103, close=104)]  # close 104 < 105
        assert evening_star(seq)["detected"]

    def test_scan_patterns_detects_pin(self):
        bars = [
            C(open=100, high=101.5, low=99, close=100.2, timestamp="t0"),
            C(open=100.2, high=101.7, low=99.2, close=100.1, timestamp="t1"),
            C(open=100.1, high=101.6, low=95, close=101, timestamp="t2"),
        ]
        found = scan_patterns(bars)
        assert any(f["pattern"] == "pin_bar" and f["direction"] == "bullish"
                   for f in found)
