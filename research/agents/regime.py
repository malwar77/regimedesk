"""
Regime Agent

Responsibility:
    Nightly agent that loads the last 90 days of H1/H4 data for major Forex pairs
    and major crypto (BTCUSDT, ETHUSDT), runs the RegimeEngine and writes
    briefs/regime_YYYYMMDD.md plus state/current_regimes.json.
    Fully deterministic. Never invents data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from research.agents.common import utc_now_iso, utc_stamp, write_brief, write_state
from research.data_pipeline import DataPipeline
from research.regime_engine import RegimeEngine

logger = logging.getLogger(__name__)


def run(
    data_dir: Path,
    output_dir: Path,
    state_dir: Path,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Execute the Regime classification pass.

    Returns:
        Dictionary of symbol -> regime detail
    """
    logger.info("Regime Agent started")
    root = Path(data_dir)
    cfg = config or {"data": {"source": "synthetic", "lookback_days": 90}}
    pipeline = DataPipeline(cfg, root=root)
    engine = RegimeEngine(cfg)

    watchlist_path = root / "config" / "watchlist.csv"
    frames = pipeline.get_watchlist_ohlcv(watchlist_path, timeframe="H1")
    details = engine.classify_watchlist_detail(frames)

    stamp = utc_stamp()
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    brief_path = write_brief(output_dir, "regime", _render_brief(details, stamp), stamp=stamp)

    serializable = {
        symbol: {
            "label": d["label"],
            "confidence": d["confidence"],
            "probabilities": d["probabilities"],
            "features": d["features"],
            "n_windows": d["n_windows"],
        }
        for symbol, d in details.items()
    }
    state_path = state_dir / "current_regimes.json"
    state_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    write_state(
        state_dir,
        "regime",
        {
            "agent": "regime",
            "stamp": stamp,
            "as_of": utc_now_iso(),
            "details": serializable,
            "brief_path": str(brief_path),
            "disclaimer": "Deterministic HMM-style filter. Facts only — no trading advice.",
        },
    )
    logger.info("Regime Agent wrote %s and %s", brief_path, state_path)
    return details


def _render_brief(details: Dict[str, Any], stamp: str) -> str:
    lines = [
        f"# Regime brief {stamp}",
        "",
        "Deterministic HMM-style filter. Facts only — no trading advice.",
        "",
        "| Symbol | Regime | Confidence | Trend | Vol |",
        "| --- | --- | --- | --- | --- |",
    ]
    for symbol, d in details.items():
        feat = d.get("features") or {}
        trend = feat.get("trend")
        vol = feat.get("vol")
        trend_s = f"{trend:.4f}" if isinstance(trend, float) else "—"
        vol_s = f"{vol:.4f}" if isinstance(vol, float) else "—"
        lines.append(
            f"| {symbol} | {d['label']} | {d['confidence']:.1%} | {trend_s} | {vol_s} |"
        )
    lines.append("")
    return "\n".join(lines)
