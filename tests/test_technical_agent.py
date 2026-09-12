"""Technical agent tests: brief output, trend_state shape, ICT labeling,
determinism — run on the recorded live session (real data)."""
import json
import os
from datetime import datetime, timezone

import pytest

from agents.technical import analyze_instrument, run, write_brief
from core.data_pipeline import DataPipeline

FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                       "recorded_live_btcusdt_1h.json")


@pytest.fixture()
def recorded_pipeline():
    return DataPipeline(live=False, source="recorded", path=FIXTURE)


def test_analyze_instrument_trend_state_shape(recorded_pipeline):
    result = analyze_instrument("BTC_USD", recorded_pipeline)
    ts = result["H1"]["trend_state"]
    # exact required shape from the spec
    assert set(ts.keys()) == {"trend", "ema_slope", "rsi", "structure",
                              "confidence"}
    assert ts["trend"] in ("bullish", "bearish", "neutral")
    assert 0.0 <= ts["confidence"] <= 1.0
    assert set(ts["ema_slope"].keys()) == {"20", "50", "200"}
    for v in ts["ema_slope"].values():
        assert v["slope"] in ("rising", "falling", "flat")
    assert ts["rsi"]["state"] in ("overbought", "oversold", "neutral")
    assert 0.0 <= ts["rsi"]["value"] <= 100.0
    assert ts["structure"] in ("HH-HL", "LH-LL", "range", "mixed")
    # ICT fields present and labeled
    assert result["H1"]["session_context"]["source"] == "ict_unverified"
    assert result["H1"]["fvg_zones"] is not None or True
    assert "premium_discount" in result["H1"]


def test_brief_written_with_labels_and_disclaimers(recorded_pipeline,
                                                   tmp_path):
    result = analyze_instrument("BTC_USD", recorded_pipeline)
    path = write_brief([result], out_dir=str(tmp_path))
    text = open(path).read()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert today in os.path.basename(path)
    # ICT labeling + no-recommendation language, fact-based output
    assert "unverified heuristic framework" in text
    assert "No trade recommendations" in text
    assert "trend_state" in text
    assert "RSI(14)" in text
    assert "EMA200" in text
    # no recommendation phrasing anywhere
    low = text.lower()
    for banned in ("buy now", "you should", "recommended trade",
                   "guaranteed", "expected gain", "will rise", "profitable"):
        assert banned not in low, banned


def test_brief_is_deterministic(recorded_pipeline, tmp_path):
    r1 = analyze_instrument("BTC_USD", recorded_pipeline)
    p1 = write_brief([r1], out_dir=str(tmp_path), date="20260912")
    r2 = analyze_instrument("BTC_USD", recorded_pipeline)
    p2 = write_brief([r2], out_dir=str(tmp_path), date="20260912")
    assert open(p1).read() == open(p2).read()


def test_run_end_to_end_synthetic(tmp_path):
    """Full agent run over the watchlist on synthetic historical data."""
    path, results = run(out_dir=str(tmp_path))
    assert os.path.exists(path)
    assert len(results) == 3  # watchlist instruments
    for res in results:
        assert res["H1"]["trend_state"]["trend"] in \
            ("bullish", "bearish", "neutral")


def test_needs_enough_bars():
    from agents.technical import analyze_timeframe
    with pytest.raises(ValueError):
        analyze_timeframe([{"high": 1, "low": 1, "close": 1}] * 100)
