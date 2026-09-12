"""Signal engine + live pipeline integration tests:

- proposals structure, SL/TP sanity, no execution
- live signal JSONL logging (every signal, executed or not)
- regime engine rules
- no-lookahead replay of a RECORDED live session (real market data,
  not synthetic)
- benchmark comparison surfacing
"""
import json
import os
from datetime import datetime, timezone

import pytest

from core.backtest import (backtest, buy_and_hold_positions,
                           compare_with_benchmarks, ma_crossover_positions)
from core.data_pipeline import DataPipeline, PipelineError, resample_h1_to_h4
from core.regime_engine import classify_regime
from core.signal_engine import build_features, generate, log_signal
from tests.util import downtrend_closes, make_bars, uptrend_closes

FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                       "recorded_live_btcusdt_1h.json")


class TestSignalEngine:
    def test_uptrend_generates_long_proposal(self):
        bars = make_bars(uptrend_closes())
        p = generate("EUR_USD", bars, account_balance=10000.0)
        assert p is not None
        assert p["direction"] == "long"
        assert p["entry_price"] == bars[-1]["close"]
        assert p["suggested_stop_loss"] < p["entry_price"]
        assert p["suggested_take_profit"] is None or \
            p["suggested_take_profit"] > p["entry_price"]
        assert p["primary_signal"] == "trend_following"
        assert p["risk_amount"] == 100.0  # 1% of 10000
        assert p["suggested_position_size"] > 0
        assert p["executed"] is False

    def test_downtrend_generates_short_or_none(self):
        bars = make_bars(downtrend_closes())
        p = generate("EUR_USD", bars, account_balance=10000.0)
        assert p is None or p["direction"] == "short"

    def test_choppy_data_no_signal(self):
        # sideways + weak trend -> r2 gate / strength threshold must block
        closes = [100.0]
        for i in range(299):
            closes.append(closes[-1] + ((1.5 if i % 2 == 0 else -1.5)
                          if i % 7 else 0.0))
        p = generate("EUR_USD", make_bars(closes))
        assert p is None

    def test_disclaimers_and_ict_labeling(self):
        p = generate("EUR_USD", make_bars(uptrend_closes()))
        blob = json.dumps(p)
        assert "unverified heuristic framework" in blob
        assert "final veto" in blob or "veto" in blob
        assert p["supporting_context"]["session_context"]["source"] \
            == "ict_unverified"

    def test_too_little_data_returns_none(self):
        assert generate("EUR_USD", make_bars([100.0] * 150)) is None

    def test_jsonl_log_written_with_reasoning(self, tmp_path):
        p = generate("EUR_USD", make_bars(uptrend_closes()))
        path = log_signal(p, log_dir=str(tmp_path))
        assert os.path.exists(path)
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert today in os.path.basename(path)
        with open(path) as f:
            rec = json.loads(f.read().strip())
        assert rec["signal_id"] == p["signal_id"]
        assert len(rec["reasoning"]) >= 3
        assert rec["executed"] is False


class TestRegimeEngine:
    def test_bullish_trend(self):
        r = classify_regime({
            "ema_slopes": {"20": "rising", "50": "rising", "200": "rising"},
            "trend_strength": {"r2": 0.8},
            "donchian": {"direction": "up"}})
        assert r["regime"] == "bullish_trend"

    def test_bearish_trend(self):
        r = classify_regime({
            "ema_slopes": {"20": "falling", "50": "falling", "200": "falling"},
            "trend_strength": {"r2": 0.8},
            "donchian": {"direction": "down"}})
        assert r["regime"] == "bearish_trend"

    def test_weak_strength_is_range(self):
        r = classify_regime({
            "ema_slopes": {"20": "rising", "50": "rising", "200": "rising"},
            "trend_strength": {"r2": 0.1},
            "donchian": {"direction": "up"}})
        assert r["regime"] == "range"

    def test_mixed_is_transition(self):
        r = classify_regime({
            "ema_slopes": {"20": "rising", "50": "flat", "200": "falling"},
            "trend_strength": {"r2": 0.5},
            "donchian": {"direction": "inside"}})
        assert r["regime"] == "transition"


class TestRecordedLiveSession:
    """Live-mode integration test on a RECORDED live session (real Coinbase
    BTC-USD hourly bars) — verifying the engines run unchanged against live
    data and that NO lookahead bias creeps in."""

    @pytest.fixture()
    def bars(self):
        pipe = DataPipeline(live=True, source="recorded", path=FIXTURE)
        return pipe.fetch("BTC_USD", "H1", count=400)

    def test_recorded_session_loads_real_data(self, bars):
        assert len(bars) >= 300
        ts = [b["timestamp"] for b in bars]
        assert ts == sorted(ts)  # strictly chronological real timestamps

    def test_engines_run_unchanged_on_live_data(self, bars):
        f = build_features(bars)
        assert f["regime"]["regime"] in ("bullish_trend", "bearish_trend",
                                         "range", "transition")
        p = generate("BTC_USD", bars, account_balance=10000.0)
        # a real, trending market may or may not trip the threshold; either
        # way the engine must produce a valid structure, never an exception
        if p is not None:
            assert p["direction"] in ("long", "short")
            assert p["entry_price"] == bars[-1]["close"]

    def test_no_lookahead_in_replay(self, bars):
        """Stream the recorded session bar by bar. At every step the
        callback may only see the prefix. Features computed during the
        replay must equal features computed on that same prefix offline
        (streaming == batch), and every generated proposal must be built
        strictly from bars up to its own timestamp."""
        pipe = DataPipeline(live=True, source="recorded", path=FIXTURE)
        proposals = []
        checked = 0

        def on_bar(prefix):
            nonlocal checked
            i = len(prefix) - 1
            if i < 210 or i % 50:  # sample indices to keep it fast
                return
            live = build_features(prefix)
            batch = build_features(bars[: i + 1])
            assert live["ema_slopes"] == batch["ema_slopes"]
            assert live["rsi"] == batch["rsi"]
            assert live["trend_strength"] == batch["trend_strength"]
            p = generate("BTC_USD", prefix, account_balance=10000.0)
            if p is not None:
                # proposal facts come only from the prefix:
                assert p["entry_price"] == bars[i]["close"]
                assert bars[i]["timestamp"] in p["signal_id"]
                proposals.append((i, p))
            checked += 1

        pipe.stream("BTC_USD", "H1", on_bar)
        assert checked >= 2
        # each proposal references only bars at or before its own index
        for i, p in proposals:
            assert p["entry_price"] == bars[i]["close"]

    def test_synthetic_source_requires_no_live_flag(self):
        with pytest.raises(PipelineError):
            DataPipeline(live=False, source="oanda")

    def test_h1_to_h4_resample(self, bars):
        from datetime import datetime
        h4 = resample_h1_to_h4(bars)
        assert len(h4) < len(bars)
        # find the first boundary-aligned group of 4 H1 bars
        start = next(i for i, b in enumerate(bars)
                     if datetime.fromisoformat(b["timestamp"]).hour % 4 == 0
                     and i + 4 <= len(bars))
        group = bars[start:start + 4]
        bar4 = next(h for h in h4
                    if h["timestamp"] == group[0]["timestamp"]
                    and h["close"] == group[-1]["close"])
        assert bar4["high"] == max(b["high"] for b in group)
        assert bar4["low"] == min(b["low"] for b in group)


class TestBenchmarkComparison:
    def test_flat_strategy_underperforms_and_it_is_surfaced(self):
        closes = uptrend_closes(n=300, rate=0.001)
        flat = [0.0] * (len(closes) - 1)
        report = compare_with_benchmarks(closes, flat, cost_bps=2.0,
                                         label="flat")
        assert report["beats_buy_and_hold"] is False
        assert "does NOT beat both benchmarks" in report["summary"]

    def test_short_in_downtrend_beats_both_benchmarks(self):
        closes = downtrend_closes(n=300, rate=0.002)
        short = [-1.0] * (len(closes) - 1)
        report = compare_with_benchmarks(closes, short, cost_bps=2.0)
        assert report["beats_buy_and_hold"] is True
        assert report["beats_ma_crossover"] is True
        assert report["summary"].startswith("strategy")

    def test_hand_checked_backtest_math(self):
        # closes [100,101,100,102]; hold long throughout, zero costs:
        # 1.01 * (1-0.00990099) * 1.02 = 1.02 (exactly, by construction)
        closes = [100.0, 101.0, 100.0, 102.0]
        r = backtest(closes, [1.0, 1.0, 1.0], cost_bps=0.0)
        assert abs(r["total_return"] - 0.02) < 1e-9

    def test_ma_crossover_positions_shape(self):
        closes = uptrend_closes(n=260)
        pos = ma_crossover_positions(closes, fast=50, slow=200)
        assert len(pos) == len(closes) - 1
        assert all(p in (0.0, 1.0) for p in pos)
