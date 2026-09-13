"""Unit tests for DataPipeline — synthetic source, no vendor keys required."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from research.data_pipeline import (
    SYNTHETIC_REGIME_PRESETS,
    DataPipeline,
    load_watchlist,
    tickers_for_currency,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pipeline(tmp_path: Path) -> DataPipeline:
    return DataPipeline(
        {"data": {"source": "synthetic", "lookback_days": 90}},
        root=tmp_path,
    )


def test_load_watchlist() -> None:
    rows = load_watchlist(ROOT / "config" / "watchlist.csv")
    tickers = [r["ticker"] for r in rows]
    assert "EURUSD" in tickers
    assert "BTCUSDT" in tickers
    assert all("asset_class" in r for r in rows)


def test_ohlcv_schema_and_integrity(pipeline: DataPipeline) -> None:
    df = pipeline.get_ohlcv("EURUSD", timeframe="H1", lookback_days=30)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) >= 24 * 20
    assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-12).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-12).all()
    assert (df["volume"] >= 0).all()
    assert df.index.is_monotonic_increasing


def test_ohlcv_is_deterministic(pipeline: DataPipeline, tmp_path: Path) -> None:
    other = DataPipeline({"data": {"source": "synthetic", "lookback_days": 30}}, root=tmp_path / "b")
    a = pipeline.get_ohlcv("BTCUSDT", "H1", 30).reset_index(drop=True)
    b = other.get_ohlcv("BTCUSDT", "H1", 30).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12)


def test_timeframes_resample_length(pipeline: DataPipeline) -> None:
    h1 = pipeline.get_ohlcv("ETHUSDT", "H1", 20)
    h4 = pipeline.get_ohlcv("ETHUSDT", "H4", 20)
    d1 = pipeline.get_ohlcv("ETHUSDT", "D1", 20)
    assert len(h1) > len(h4) > len(d1)
    assert len(d1) >= 18


def test_rejects_bad_timeframe(pipeline: DataPipeline) -> None:
    with pytest.raises(ValueError):
        pipeline.get_ohlcv("EURUSD", timeframe="M1")


def test_watchlist_ohlcv(pipeline: DataPipeline) -> None:
    data = pipeline.get_watchlist_ohlcv(ROOT / "config" / "watchlist.csv", timeframe="D1", lookback_days=40)
    assert set(data) == set(SYNTHETIC_REGIME_PRESETS)
    assert all(len(frame) > 10 for frame in data.values())


def test_economic_calendar_flags_high_impact(pipeline: DataPipeline) -> None:
    events = pipeline.get_economic_calendar(hours_ahead=48)
    assert isinstance(events, list)
    # Horizon is 48h so the list may be short over a weekend; 14-day window always has flags
    wide = pipeline.get_economic_calendar(hours_ahead=24 * 10)
    flagged = [e for e in wide if e["flags_majors"]]
    assert flagged, "expected at least one >0.8% historical-move event"
    assert all("time" in e and "currency" in e and "title" in e for e in wide)


def test_onchain_crypto_only(pipeline: DataPipeline) -> None:
    metrics = pipeline.get_onchain_metrics("BTCUSDT")
    assert metrics["symbol"] == "BTCUSDT"
    assert "funding_rate" in metrics
    assert "exchange_netflow_usd" in metrics
    assert "anomalies" in metrics
    with pytest.raises(ValueError):
        pipeline.get_onchain_metrics("EURUSD")


def test_sentiment_flags_crash_preset(pipeline: DataPipeline) -> None:
    sol = pipeline.get_sentiment_metrics("SOLUSDT")
    assert "mentions_3sigma" in sol["anomalies"]
    assert sol["mentions_zscore"] >= 3.0
    eurusd = pipeline.get_sentiment_metrics("EURUSD")
    assert abs(eurusd["mentions_zscore"]) < 3.0


def test_tickers_for_currency() -> None:
    usd = tickers_for_currency("USD")
    assert "EURUSD" in usd
    assert "BTCUSDT" in usd
    assert tickers_for_currency("EUR") == ["EURUSD"]
