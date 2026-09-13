"""Unit tests for RegimeEngine — each synthetic path must map to the intended regime."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.data_pipeline import DataPipeline
from research.regime_engine import RegimeEngine

ROOT = Path(__file__).resolve().parents[1]


def _path(n: int, drift: float, vol: float, start: float = 100.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    shocks = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(shocks))
    open_ = np.empty_like(close)
    open_[0] = start
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": np.full(n, 1000.0)},
        index=idx,
    )


@pytest.fixture
def engine() -> RegimeEngine:
    return RegimeEngine({"data": {"regime_window": 24, "regime_step": 12}})


def test_probabilities_sum_to_one(engine: RegimeEngine) -> None:
    probs = engine.classify(_path(400, 0.0, 0.003))
    assert set(probs) == set(engine.REGIMES)
    assert pytest.approx(sum(probs.values()), abs=1e-9) == 1.0
    assert all(0.0 <= v <= 1.0 for v in probs.values())


def test_crash_path_is_crash(engine: RegimeEngine) -> None:
    detail = engine.classify_detail(_path(500, drift=-0.0030, vol=0.020, seed=7))
    assert detail["label"] == "Crash"
    assert detail["probabilities"]["Crash"] == max(detail["probabilities"].values())


def test_bear_path_is_bear(engine: RegimeEngine) -> None:
    detail = engine.classify_detail(_path(500, drift=-0.00055, vol=0.0065, seed=11))
    assert detail["label"] in {"Bear", "Crash"}
    assert detail["probabilities"]["Bear"] + detail["probabilities"]["Crash"] > 0.5


def test_neutral_path_is_neutral(engine: RegimeEngine) -> None:
    detail = engine.classify_detail(_path(500, drift=0.00001, vol=0.0020, seed=3))
    assert detail["label"] == "Neutral"
    assert detail["probabilities"]["Neutral"] > 0.4


def test_bull_path_is_bull(engine: RegimeEngine) -> None:
    detail = engine.classify_detail(_path(500, drift=0.0007, vol=0.0040, seed=19))
    assert detail["label"] in {"Bull", "Euphoria"}
    assert detail["probabilities"]["Bull"] >= detail["probabilities"]["Bear"]
    assert detail["probabilities"]["Bull"] >= detail["probabilities"]["Neutral"]


def test_euphoria_path_is_euphoria(engine: RegimeEngine) -> None:
    detail = engine.classify_detail(_path(500, drift=0.0020, vol=0.014, seed=23))
    assert detail["label"] == "Euphoria"
    assert detail["probabilities"]["Euphoria"] == max(detail["probabilities"].values())


def test_short_series_does_not_crash(engine: RegimeEngine) -> None:
    tiny = _path(10, 0.0, 0.01)
    probs = engine.classify(tiny)
    assert pytest.approx(sum(probs.values()), abs=1e-9) == 1.0


def test_empty_frame_uniform_neutral(engine: RegimeEngine) -> None:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    detail = engine.classify_detail(empty)
    assert detail["label"] == "Neutral"
    assert pytest.approx(sum(detail["probabilities"].values()), abs=1e-9) == 1.0


def test_watchlist_synthetic_presets(engine: RegimeEngine, tmp_path: Path) -> None:
    pipe = DataPipeline({"data": {"source": "synthetic", "lookback_days": 90}}, root=tmp_path)
    data = pipe.get_watchlist_ohlcv(ROOT / "config" / "watchlist.csv", timeframe="H1", lookback_days=90)
    details = engine.classify_watchlist_detail(data)
    assert "EURUSD" in details
    # Presets are designed so these land on the intended side of the map
    assert details["SOLUSDT"]["label"] == "Crash"
    assert details["EURUSD"]["label"] == "Neutral"
    assert details["BTCUSDT"]["label"] in {"Euphoria", "Bull"}
    assert details["USDJPY"]["label"] in {"Bull", "Euphoria"}
    probs = engine.classify_watchlist(data)
    assert set(probs) == set(data)
