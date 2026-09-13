"""
Chief of Staff Agent

Responsibility:
    Morning synthesis agent.
    Reads all prior briefs for the current date, applies confirmation rules:
        - HIGH conviction  = ≥2 independent agents agree + regime alignment
        - MEDIUM conviction = single-agent signal
    Produces one ranked morning brief. Never invents signals — only synthesizes
    and ranks what the other agents reported.

    Writes briefs/chief_YYYYMMDD.md and (optionally) pushes HIGH signals.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research.agents.common import read_state, utc_now_iso, utc_stamp, write_brief, write_state

logger = logging.getLogger(__name__)

AGENT_FILES = ("macro", "regime", "onchain", "sentiment", "technical")
BULL_REGIMES = {"Bull", "Euphoria"}
BEAR_REGIMES = {"Bear", "Crash"}


def run(
    briefs_dir: Path,
    output_dir: Path,
    state_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the Chief of Staff synthesis pass."""
    logger.info("Chief of Staff Agent started")
    briefs_dir = Path(briefs_dir)
    state_dir = Path(state_dir) if state_dir is not None else briefs_dir.parent / "state"

    bundles = _load_bundles(state_dir)
    regimes = _regime_labels(bundles.get("regime"))
    findings = _collect_findings(bundles)
    ranked = _rank(findings, regimes)

    stamp = utc_stamp()
    brief_path = write_brief(output_dir, "chief", _render_brief(ranked, regimes, stamp), stamp=stamp)
    payload = {
        "agent": "chief",
        "stamp": stamp,
        "as_of": utc_now_iso(),
        "ranked": ranked,
        "high_count": sum(1 for r in ranked if r["conviction"] == "HIGH"),
        "medium_count": sum(1 for r in ranked if r["conviction"] == "MEDIUM"),
        "source_agents": sorted(bundles.keys()),
        "brief_path": str(brief_path),
        "disclaimer": "Synthesis only. No new facts. Not a trade order.",
    }
    write_state(state_dir, "chief", payload)
    logger.info("Chief of Staff wrote %s (%s ranked items)", brief_path, len(ranked))
    return payload


def _load_bundles(state_dir: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name in AGENT_FILES:
        payload = read_state(state_dir, name)
        if payload:
            out[name] = payload
    return out


def _regime_labels(regime_bundle: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not regime_bundle:
        return {}
    # Regime agent stores {symbol: {label, ...}} at state/current_regimes.json
    # and also a copy under regime_latest if present.
    labels: Dict[str, str] = {}
    details = regime_bundle.get("details") or regime_bundle
    if isinstance(details, dict):
        for symbol, body in details.items():
            if isinstance(body, dict) and "label" in body:
                labels[symbol] = str(body["label"])
    return labels


def _collect_findings(bundles: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for name, bundle in bundles.items():
        if name == "regime":
            details = bundle.get("details") or {}
            for symbol, body in details.items():
                if not isinstance(body, dict):
                    continue
                label = body.get("label", "Neutral")
                if label in BULL_REGIMES:
                    direction = "bullish"
                elif label in BEAR_REGIMES:
                    direction = "bearish"
                else:
                    continue
                findings.append(
                    {
                        "agent": "regime",
                        "symbol": symbol,
                        "symbols": [symbol],
                        "direction": direction,
                        "kind": "info",
                        "summary": f"{symbol} regime {label} ({body.get('confidence', 0):.0%})",
                        "facts": [f"label={label}"],
                    }
                )
            continue
        for item in bundle.get("findings") or []:
            findings.append(item)
    return findings


def _rank(findings: List[Dict[str, Any]], regimes: Dict[str, str]) -> List[Dict[str, Any]]:
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in findings:
        symbols: Iterable[str] = item.get("symbols") or ([item["symbol"]] if item.get("symbol") else [])
        for symbol in symbols:
            by_symbol[symbol].append(item)

    ranked: List[Dict[str, Any]] = []
    for symbol, items in by_symbol.items():
        directional = [i for i in items if i.get("direction") in {"bullish", "bearish"}]
        bull_agents = sorted({i["agent"] for i in directional if i["direction"] == "bullish"})
        bear_agents = sorted({i["agent"] for i in directional if i["direction"] == "bearish"})
        caution_agents = sorted({i["agent"] for i in items if i.get("direction") == "caution"})
        regime = regimes.get(symbol, "Neutral")

        direction = "none"
        agreeing: List[str] = []
        if len(bull_agents) >= len(bear_agents) and bull_agents:
            direction = "bullish"
            agreeing = bull_agents
        elif bear_agents:
            direction = "bearish"
            agreeing = bear_agents

        aligned = (direction == "bullish" and regime in BULL_REGIMES) or (
            direction == "bearish" and regime in BEAR_REGIMES
        )
        if direction != "none" and len(agreeing) >= 2 and aligned:
            conviction = "HIGH"
        elif direction != "none" or caution_agents:
            conviction = "MEDIUM"
        else:
            continue

        summaries = [i["summary"] for i in items]
        ranked.append(
            {
                "symbol": symbol,
                "conviction": conviction,
                "direction": direction if direction != "none" else "caution",
                "regime": regime,
                "agreeing_agents": agreeing,
                "caution_agents": caution_agents,
                "summary": " · ".join(summaries[:3]),
                "notes": _notes(direction, regime, aligned, caution_agents),
            }
        )

    order = {"HIGH": 0, "MEDIUM": 1}
    ranked.sort(
        key=lambda r: (
            order.get(r["conviction"], 9),
            -len(r["agreeing_agents"]),
            r["symbol"],
        )
    )
    for i, row in enumerate(ranked, start=1):
        row["rank"] = i
    return ranked


def _notes(direction: str, regime: str, aligned: bool, caution_agents: List[str]) -> str:
    bits = []
    if direction in {"bullish", "bearish"} and not aligned:
        bits.append(f"direction {direction} is not aligned with regime {regime}")
    if caution_agents:
        bits.append("caution from " + ", ".join(caution_agents))
    bits.append("research only — not a trade proposal")
    return "; ".join(bits)


def _render_brief(ranked: List[Dict[str, Any]], regimes: Dict[str, str], stamp: str) -> str:
    lines = [
        f"# Chief of Staff morning brief {stamp}",
        "",
        "Synthesis of overnight agents. HIGH = ≥2 independent agents agree AND regime alignment. "
        "MEDIUM = single-agent or unaligned. No new facts. Not a trade order.",
        "",
    ]
    if not ranked:
        lines.append("No synthesizable items — overnight agents reported nothing directional.")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Rank | Symbol | Conviction | Dir | Regime | Agents | Summary |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in ranked:
        agents = ",".join(row["agreeing_agents"] or row["caution_agents"] or [])
        lines.append(
            f"| {row['rank']} | {row['symbol']} | {row['conviction']} | {row['direction']} | "
            f"{row['regime']} | {agents} | {row['summary']} |"
        )
    lines.append("")
    lines.append(f"HIGH {sum(1 for r in ranked if r['conviction'] == 'HIGH')} · "
                 f"MEDIUM {sum(1 for r in ranked if r['conviction'] == 'MEDIUM')}")
    lines.append("")
    return "\n".join(lines)
