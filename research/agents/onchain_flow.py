"""
On-chain / Flow Agent (crypto only)

Responsibility:
    Nightly agent focused on crypto instruments.
    Collects exchange netflow, funding rates, large wallet movements (>$5M),
    and open-interest changes. Flags any 3-standard-deviation anomaly versus
    the 30-day baseline.

    Every number must be accompanied by source + timestamp.
    Writes briefs/onchain_YYYYMMDD.md.
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
    """Execute the On-chain / Flow research pass (crypto instruments only)."""
    logger.info("OnchainFlow Agent started")
    watchlist_path = Path(watchlist_path)
    root = Path(root) if root is not None else watchlist_path.parent.parent
    cfg = config or {"data": {"source": "synthetic", "lookback_days": 90}}
    pipeline = DataPipeline(cfg, root=root)

    metrics = []
    findings = []
    for row in load_watchlist(watchlist_path):
        if row["asset_class"] != "crypto":
            continue
        m = pipeline.get_onchain_metrics(row["ticker"])
        metrics.append(m)
        direction = _direction(m)
        if m["anomalies"]:
            findings.append(
                finding(
                    agent="onchain",
                    symbol=m["symbol"],
                    direction=direction,
                    kind="flag",
                    summary=_summary(m),
                    facts=[
                        f"source={m['source']}",
                        f"timestamp={m['timestamp']}",
                        f"netflow_usd={m['exchange_netflow_usd']:.0f}",
                        f"netflow_z={m['netflow_zscore']}",
                        f"funding={m['funding_rate']}",
                        f"funding_z={m['funding_zscore']}",
                        f"oi_change_pct={m['open_interest_change_pct']}",
                        f"whale_usd={m['largest_wallet_move_usd']:.0f}",
                        f"anomalies={','.join(m['anomalies'])}",
                    ],
                )
            )

    stamp = utc_stamp()
    brief_path = write_brief(output_dir, "onchain", _render_brief(metrics, stamp), stamp=stamp)
    payload = {
        "agent": "onchain",
        "stamp": stamp,
        "as_of": utc_now_iso(),
        "metrics": metrics,
        "findings": findings,
        "brief_path": str(brief_path),
        "disclaimer": "Every figure cites source + timestamp. Not trading advice.",
    }
    write_state(state_dir or (root / "state"), "onchain", payload)
    logger.info("OnchainFlow Agent wrote %s (%s crypto names)", brief_path, len(metrics))
    return payload


def _direction(m: Dict[str, Any]) -> str:
    """Map flow facts to a research leaning — not a trade."""
    z_n = m["netflow_zscore"]
    z_f = m["funding_zscore"]
    # Positive netflow = coins onto exchanges (sell-side pressure)
    if z_n >= 3.0 or z_f <= -3.0:
        return "bearish"
    if z_n <= -3.0 or z_f >= 3.0:
        return "bullish"
    if m["anomalies"]:
        return "caution"
    return "none"


def _summary(m: Dict[str, Any]) -> str:
    bits = [f"{m['symbol']} on-chain"]
    if "netflow_3sigma" in m["anomalies"]:
        side = "exchange inflow" if m["netflow_zscore"] > 0 else "exchange outflow"
        bits.append(f"{side} z={m['netflow_zscore']}")
    if "funding_3sigma" in m["anomalies"]:
        bits.append(f"funding {m['funding_rate']:.4%} z={m['funding_zscore']}")
    if "whale_gt_5m" in m["anomalies"]:
        bits.append(f"wallet ${m['largest_wallet_move_usd'] / 1e6:.1f}M")
    return "; ".join(bits)


def _render_brief(metrics: list, stamp: str) -> str:
    lines = [
        f"# On-chain brief {stamp}",
        "",
        "Crypto flow vs 30-day baseline. Every number has source + timestamp. No trading advice.",
        "",
    ]
    if not metrics:
        lines.append("No crypto names on the watchlist.")
        lines.append("")
        return "\n".join(lines)

    rows = []
    for m in metrics:
        rows.append(
            [
                m["symbol"],
                f"{m['exchange_netflow_usd'] / 1e6:.1f}M",
                f"{m['netflow_zscore']:.2f}",
                f"{m['funding_rate']:.4%}",
                f"{m['funding_zscore']:.2f}",
                f"{m['open_interest_change_pct']:.2f}%",
                f"${m['largest_wallet_move_usd'] / 1e6:.1f}M",
                ",".join(m["anomalies"]) or "—",
            ]
        )
    lines.append(
        md_table(
            ["Symbol", "Netflow", "z", "Funding", "z", "OI Δ", "Whale", "Flags"],
            rows,
        )
    )
    lines.append("")
    lines.append("Source column: synthetic-public-proxy (seeded; live vendor keys not configured).")
    for m in metrics:
        lines.append(f"- {m['symbol']} timestamp {m['timestamp']} source={m['source']}")
    lines.append("")
    return "\n".join(lines)
