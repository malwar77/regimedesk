"""Tests: backtest metrics (hand-computed), engine replay, no-lookahead,
benchmark honesty. Numbers below are computed BY HAND from first
principles and cross-checked independently — not by calling the code
under test."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.backtest import (backtest, buy_and_hold_positions,
                           compare_with_benchmarks, ma_crossover_positions,
                           signal_positions)
from tests.util import make_bars


# ---------------------------------------------------------------------------
# backtest() — hand-computed reference values
# ---------------------------------------------------------------------------
def test_backtest_long_no_costs_hand_computed():
    # closes: 100, 110, 105, 120, 130
    # positions[i] held from close i to i+1 (len = len(closes) - 1)
    #   i=0 pos 0 -> 0
    #   i=1 pos 1 -> 110 -> 105:  -0.0454545...
    #   i=2 pos 1 -> 105 -> 120:  +0.1428571...
    #   i=3 pos 1 -> 120 -> 130:  +0.0833333...
    # curve: 1, 1, 0.9545454..., 1.0909090..., 1.1818181...
    closes = [100.0, 110.0, 105.0, 120.0, 130.0]
    res = backtest(closes, [0.0, 1.0, 1.0, 1.0], cost_bps=0.0)
    assert res["final_equity"] == pytest.approx(1.0 * 0.9545454545
                                                * 1.1428571429
                                                * 1.0833333333, abs=1e-6)
    assert res["total_return"] == pytest.approx(0.1818182, abs=1e-6)
    # only dip: 0.9545454... vs running peak 1.0 -> -0.0454545...
    assert res["max_drawdown"] == pytest.approx(-0.0454545, abs=1e-6)


def test_backtest_costs_hand_computed():
    # Flat -> long at i=1: turnover 1.0 once, then no more changes.
    # gross each step as above; the turnover step multiplies by
    # (1 - cost_bps/10000).
    closes = [100.0, 110.0, 105.0]
    res = backtest(closes, [0.0, 1.0], cost_bps=10.0)   # 10 bps = 0.001
    # i=1: (1 - 0.0454545...) * (1 - 0.001) = 0.9545454... * 0.999
    #      = 0.9535909...  (hand-computed)
    assert res["final_equity"] == pytest.approx(0.9535909, abs=1e-6)
    assert res["total_return"] == pytest.approx(-0.0464091, abs=1e-6)


def test_backtest_short_position():
    # Short from 100 -> 110 loses 10%: equity 0.9 exactly (no costs).
    res = backtest([100.0, 110.0], [-1.0], cost_bps=0.0)
    assert res["total_return"] == pytest.approx(-0.1, abs=1e-9)
    assert res["max_drawdown"] == pytest.approx(-0.1, abs=1e-9)


def test_backtest_input_validation():
    with pytest.raises(ValueError):
        backtest([100.0, 101.0], [], cost_bps=0.0)      # positions too short
    with pytest.raises(ValueError):
        backtest([100.0, 101.0], [1.0], cost_bps=-1)   # negative cost


def test_ma_crossover_and_buy_hold_positions():
    # flat until both SMAs exist; fast>slow only after enough data
    closes = [float(x) for x in range(1, 8)]            # strictly rising
    pos = ma_crossover_positions(closes, fast=2, slow=3)
    assert len(pos) == len(closes) - 1
    # with fast=2 slow=3: first valid slot is i=2 (slow window exists at
    # index 3); values before that are 0.0
    assert pos[0] == 0.0 and pos[1] == 0.0
    assert all(p == 1.0 for p in pos[2:])
    assert buy_and_hold_positions(5) == [1.0] * 5
    with pytest.raises(ValueError):
        ma_crossover_positions(closes, fast=3, slow=2)


# ---------------------------------------------------------------------------
# compare_with_benchmarks — honesty flags
# ---------------------------------------------------------------------------
def test_benchmarks_flat_strategy_falling_market_beats_both():
    # All-flat strategy in a falling market: 0% vs negative benchmarks.
    closes = [100.0 * (0.99 ** i) for i in range(30)]
    rep = compare_with_benchmarks(closes, [0.0] * (len(closes) - 1),
                                  cost_bps=0.0, label="flat")
    assert rep["strategy"]["total_return"] == 0.0
    assert rep["buy_and_hold"]["total_return"] < 0.0
    # B&H loses, but MA-crossover stays flat in a strictly falling
    # series too -> a TIE at 0.0, which must be reported as NOT beating
    # both. The report must say so plainly.
    assert rep["beats_buy_and_hold"] is True
    assert rep["beats_ma_crossover"] is False
    assert rep["ma_crossover"]["total_return"] == 0.0
    assert "does NOT beat both benchmarks" in rep["summary"]


def test_benchmarks_flat_strategy_rising_market_honest_fail():
    closes = [100.0 * (1.01 ** i) for i in range(30)]
    rep = compare_with_benchmarks(closes, [0.0] * (len(closes) - 1),
                                  cost_bps=0.0, label="flat")
    assert rep["beats_buy_and_hold"] is False
    assert "does NOT beat both benchmarks" in rep["summary"]


# ---------------------------------------------------------------------------
# signal_positions — mapping, and NO LOOKAHEAD with the real engine
# ---------------------------------------------------------------------------
def test_signal_positions_stub_mapping():
    bars = make_bars([100.0 + i for i in range(5)])
    # stub: fires long when it has seen >=3 bars, short when >=5
    def gen(instrument, history, timeframe="H1"):
        n = len(history)
        if n >= 5:
            return {"direction": "short"}
        if n >= 3:
            return {"direction": "long"}
        return None
    pos = signal_positions(bars, generator=gen)
    assert pos == [0.0, 0.0, 1.0, 1.0]     # slots 0,1: none; 2,3: long/short
    assert len(pos) == len(bars) - 1


def test_signal_positions_no_lookahead_real_engine():
    """The killer property: positions computed on bars[:k] must be a
    prefix-invariant of positions computed on the FULL series — the
    signal at slot i depends only on bars[:i+1]."""
    from core.data_pipeline import DataPipeline
    pipeline = DataPipeline(live=False, source="synthetic")
    bars = pipeline.fetch("BTC_USD", "H1", count=260)
    full = signal_positions(bars, "BTC_USD", "H1")
    for k in (240, 250):
        truncated = signal_positions(bars[:k], "BTC_USD", "H1")
        assert truncated == full[:k - 1]
    assert all(p in (-1.0, 0.0, 1.0) for p in full)


def test_signal_positions_engine_fires_on_uptrend():
    from core.data_pipeline import DataPipeline
    pipeline = DataPipeline(live=False, source="synthetic")
    bars = pipeline.fetch("BTC_USD", "H1", count=260)
    pos = signal_positions(bars, "BTC_USD", "H1")
    # synthetic walk is mildly trending; at least one nonzero slot must
    # exist on 260 bars (engine demonstrably fires in replay)
    assert any(p != 0.0 for p in pos)
