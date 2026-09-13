"""Research swarm: macro, on-chain, sentiment, technical, chief of staff."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.agents import chief_of_staff, macro_news, onchain_flow, regime, sentiment, technical
from research.agents.technical import analyze_ohlcv, _ema, _rsi
from research.data_pipeline import DataPipeline, load_watchlist
from research.nightly import run_nightly_research

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    work = tmp_path / "desk"
    shutil.copytree(ROOT / "config", work / "config")
    (work / "briefs").mkdir()
    (work / "state").mkdir()
    return work


def test_macro_agent_writes_facts_only(desk: Path) -> None:
    payload = macro_news.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        hours_ahead=24 * 10,
        state_dir=desk / "state",
    )
    assert payload["event_count"] >= 1
    assert payload["flagged_count"] >= 1
    text = Path(payload["brief_path"]).read_text(encoding="utf-8")
    assert "no trading advice" in text.lower()
    assert "BUY" not in text and "SELL" not in text
    assert (desk / "state" / "macro_latest.json").exists()
    assert any(f["direction"] == "caution" for f in payload["findings"])


def test_onchain_agent_crypto_only_cites_source(desk: Path) -> None:
    payload = onchain_flow.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        state_dir=desk / "state",
    )
    symbols = {m["symbol"] for m in payload["metrics"]}
    assert "BTCUSDT" in symbols
    assert "EURUSD" not in symbols
    text = Path(payload["brief_path"]).read_text(encoding="utf-8")
    assert "source=" in text
    assert "timestamp" in text.lower()
    # Crash / Euphoria presets are forced into 3σ space
    flagged = {f["symbol"] for f in payload["findings"]}
    assert "SOLUSDT" in flagged
    assert "BTCUSDT" in flagged
    for m in payload["metrics"]:
        assert m["source"]
        assert m["timestamp"]


def test_sentiment_agent_flags_three_sigma(desk: Path) -> None:
    payload = sentiment.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        state_dir=desk / "state",
    )
    zmap = {m["symbol"]: m["mentions_zscore"] for m in payload["metrics"]}
    assert abs(zmap["SOLUSDT"]) >= 3.0
    assert abs(zmap["BTCUSDT"]) >= 3.0
    text = Path(payload["brief_path"]).read_text(encoding="utf-8")
    assert "no trading advice" in text.lower()
    assert payload["findings"]


def test_technical_uptrend_is_hh_hl() -> None:
    n = 80
    close = 100 * np.exp(np.cumsum(np.full(n, 0.004)))
    open_ = np.empty_like(close)
    open_[0] = 100
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    d1 = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": np.full(n, 1.0)},
        index=idx,
    )
    out = analyze_ohlcv(d1, d1, asset_class="forex")
    assert out["structure"] == "HH-HL"
    assert out["direction"] == "bullish"
    assert out["support"]
    assert out["resistance"]


def test_technical_downtrend_is_lh_ll() -> None:
    n = 80
    close = 100 * np.exp(np.cumsum(np.full(n, -0.004)))
    open_ = np.empty_like(close)
    open_[0] = 100
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    d1 = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": np.full(n, 1.0)},
        index=idx,
    )
    out = analyze_ohlcv(d1, d1, asset_class="forex")
    assert out["structure"] == "LH-LL"
    assert out["direction"] == "bearish"


def test_technical_agent_writes_brief(desk: Path) -> None:
    payload = technical.run(
        desk,
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        state_dir=desk / "state",
    )
    assert "EURUSD" in payload["by_symbol"]
    text = Path(payload["brief_path"]).read_text(encoding="utf-8")
    assert "EURUSD" in text
    assert "no trading advice" in text.lower()
    assert payload["by_symbol"]["EURUSD"]["structure"] in {"HH-HL", "LH-LL", "range"}


def test_chief_does_not_invent_symbols(desk: Path) -> None:
    regime.run(desk, desk / "briefs", desk / "state")
    macro_news.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        hours_ahead=24 * 10,
        state_dir=desk / "state",
    )
    onchain_flow.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        state_dir=desk / "state",
    )
    sentiment.run(
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        root=desk,
        state_dir=desk / "state",
    )
    technical.run(
        desk,
        desk / "config" / "watchlist.csv",
        desk / "briefs",
        state_dir=desk / "state",
    )
    payload = chief_of_staff.run(desk / "briefs", desk / "briefs", state_dir=desk / "state")
    watch = {r["ticker"] for r in load_watchlist(desk / "config" / "watchlist.csv")}
    for row in payload["ranked"]:
        assert row["symbol"] in watch
        assert row["conviction"] in {"HIGH", "MEDIUM"}
        assert "not a trade" in row["notes"].lower()
    text = Path(payload["brief_path"]).read_text(encoding="utf-8")
    assert "not a trade order" in text.lower()
    # Crash + on-chain + sentiment should confirm SOLUSDT as HIGH bearish
    sol = next((r for r in payload["ranked"] if r["symbol"] == "SOLUSDT"), None)
    assert sol is not None
    assert sol["direction"] == "bearish"
    assert sol["conviction"] == "HIGH"
    assert "regime" in sol["agreeing_agents"]


def test_runner_nightly_writes_all_briefs(desk: Path) -> None:
    results = run_nightly_research(desk)
    assert set(results) == {"macro", "regime", "onchain", "sentiment", "technical", "chief"}
    names = {p.name.split("_")[0] for p in (desk / "briefs").glob("*.md")}
    assert names >= {"macro", "regime", "onchain", "sentiment", "technical", "chief"}


def test_sentiment_metrics_deterministic(tmp_path: Path) -> None:
    a = DataPipeline({"data": {"source": "synthetic"}}, root=tmp_path / "a")
    b = DataPipeline({"data": {"source": "synthetic"}}, root=tmp_path / "b")
    sa = a.get_sentiment_metrics("EURUSD")
    sb = b.get_sentiment_metrics("EURUSD")
    assert sa["mentions_24h"] == sb["mentions_24h"]
    assert sa["mentions_zscore"] == sb["mentions_zscore"]
    assert sa["polarity"] == sb["polarity"]


def test_technical_ema_rsi_math():
    """Reference-value validation WITHOUT a fake talib shim: hand
    computed EMA, an independent EMA recurrence, and RSI boundary
    cases (zero losses -> 100, zero gains -> 0)."""
    close = pd.Series([10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 8, 7,
                       6, 5, 4, 3, 2, 1], dtype=float)
    # 1) EMA(3) hand-computed: alpha=0.5, seeded at first close
    #    10 -> 10.5 -> 11.25 -> 12.125
    e3 = _ema(pd.Series([10, 11, 12, 13], dtype=float), 3)
    np.testing.assert_allclose(e3.values, [10, 10.5, 11.25, 12.125],
                               rtol=1e-12)
    # 2) independent EMA(20) recurrence (explicit loop, not _ema)
    alpha = 2.0 / 21.0
    ema = [close.values[0]]
    for v in close.values[1:]:
        ema.append(ema[-1] + alpha * (v - ema[-1]))
    np.testing.assert_allclose(_ema(close, 20).values, ema, rtol=1e-12)
    # 3) RSI boundary cases by definition
    rising = pd.Series([float(x) for x in range(1, 60)])
    falling = pd.Series([float(x) for x in range(60, 1, -1)])
    assert _rsi(rising, 14).iloc[-1] == 100.0    # zero losses
    assert _rsi(falling, 14).iloc[-1] == 0.0     # zero gains
