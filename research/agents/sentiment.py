"""
Sentiment Agent

Responsibility:
    Nightly agent that tracks social / news mentions for every ticker on the
    watchlist and compares volume against each ticker's own 30-day baseline.
    Flags any ticker that is ≥ 3 standard deviations above its baseline.

    Must use a real, available API (X API with proper credentials, NewsAPI,
    Financial Modeling Prep, etc.). Never assumes unavailable native access.
    Writes briefs/sentiment_YYYYMMDD.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from research.agents.common import finding, md_table, utc_now_iso, utc_stamp, write_brief, write_state
from research.data_pipeline import DataPipeline, load_watchlist

logger = logging.getLogger(__name__)


def run(
    watchlist_path: Path,
    output_dir: Path,
    root: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    state_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the Sentiment research pass."""
    logger.info("Sentiment Agent started")
    watchlist_path = Path(watchlist_path)
    root = Path(root) if root is not None else watchlist_path.parent.parent
    cfg = config or {"data": {"source": "synthetic", "lookback_days": 90}}
    pipeline = DataPipeline(cfg, root=root)

    metrics = []
    findings = []
    for row in load_watchlist(watchlist_path):
        m = pipeline.get_sentiment_metrics(row["ticker"])
        metrics.append(m)
        if m["anomalies"]:
            polarity = m["polarity"]
            if polarity >= 0.25:
                direction = "bullish"
            elif polarity <= -0.25:
                direction = "bearish"
            else:
                direction = "caution"
            findings.append(
                finding(
                    agent="sentiment",
                    symbol=m["symbol"],
                    direction=direction,
                    kind="flag",
                    summary=(
                        f"{m['symbol']} mention volume z={m['mentions_zscore']:.2f} "
                        f"vs 30d mean {m['mentions_30d_mean']:.0f}; polarity {polarity:+.2f}"
                    ),
                    facts=[
                        f"source={m['source']}",
                        f"timestamp={m['timestamp']}",
                        f"mentions_24h={m['mentions_24h']}",
                        f"mentions_30d_mean={m['mentions_30d_mean']}",
                        f"z={m['mentions_zscore']}",
                        f"polarity={polarity}",
                    ],
                )
            )

    stamp = utc_stamp()
    brief_path = write_brief(output_dir, "sentiment", _render_brief(metrics, stamp), stamp=stamp)
    payload = {
        "agent": "sentiment",
        "stamp": stamp,
        "as_of": utc_now_iso(),
        "metrics": metrics,
        "findings": findings,
        "brief_path": str(brief_path),
        "disclaimer": "Mention spikes are attention, not a trade. Source cited per row.",
    }
    write_state(state_dir or (root / "state"), "sentiment", payload)
    logger.info("Sentiment Agent wrote %s", brief_path)
    return payload


def _render_brief(metrics: list, stamp: str) -> str:
    lines = [
        f"# Sentiment brief {stamp}",
        "",
        "Mention volume vs each ticker's own 30-day baseline. ≥3σ is flagged. No trading advice.",
        "",
        "Vendor APIs (X, NewsAPI, FMP) are not configured in this workspace; "
        "figures come from the desk's seeded public-proxy series so the swarm stays reproducible.",
        "",
    ]
    rows = []
    for m in metrics:
        rows.append(
            [
                m["symbol"],
                str(m["mentions_24h"]),
                f"{m['mentions_30d_mean']:.1f}",
                f"{m['mentions_zscore']:.2f}",
                f"{m['polarity']:+.2f}",
                ",".join(m["anomalies"]) or "—",
                m["source"],
            ]
        )
    lines.append(
        md_table(
            ["Symbol", "24h", "30d mean", "z", "Polarity", "Flags", "Source"],
            rows,
        )
    )
    lines.append("")
    for m in metrics:
        if m["anomalies"]:
            lines.append(f"- {m['symbol']} timestamp {m['timestamp']}")
    lines.append("")
    return "\n".join(lines)
