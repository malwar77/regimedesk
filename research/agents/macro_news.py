"""
Macro/News Agent

Responsibility:
    Nightly research agent that reads the watchlist, pulls high-impact economic
    calendar events for the next 48 hours, and flags anything that has
    historically moved major Forex pairs / BTC / ETH by more than 0.8%.

    Outputs facts only. Never issues trading advice or signals.
    Writes a clean markdown brief to briefs/macro_YYYYMMDD.md.
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
    hours_ahead: int = 48,
    state_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the Macro/News research pass. Facts only — no trading advice."""
    logger.info("MacroNews Agent started")
    watchlist_path = Path(watchlist_path)
    root = Path(root) if root is not None else watchlist_path.parent.parent
    cfg = config or {"data": {"source": "synthetic", "lookback_days": 90}}
    pipeline = DataPipeline(cfg, root=root)
    watchlist = load_watchlist(watchlist_path)
    tickers = {row["ticker"] for row in watchlist}

    events = pipeline.get_economic_calendar(hours_ahead=hours_ahead)
    findings = []
    flagged = []
    for ev in events:
        affected = [t for t in ev.get("affected_tickers") or [] if t in tickers]
        record = {
            "time": ev["time"].isoformat() if hasattr(ev["time"], "isoformat") else str(ev["time"]),
            "currency": ev["currency"],
            "title": ev["title"],
            "impact": ev["impact"],
            "historically_moves_pct": ev["historically_moves_pct"],
            "flags_majors": bool(ev.get("flags_majors")),
            "affected_tickers": affected,
        }
        if record["flags_majors"]:
            flagged.append(record)
            findings.append(
                finding(
                    agent="macro",
                    symbols=affected,
                    direction="caution",
                    kind="flag",
                    summary=(
                        f"{ev['title']} ({ev['currency']}) historically moves majors "
                        f"{ev['historically_moves_pct']:.1f}%"
                    ),
                    facts=[
                        f"time={record['time']}",
                        f"impact={ev['impact']}",
                        f"affected={','.join(affected) or '—'}",
                    ],
                )
            )

    stamp = utc_stamp()
    brief = _render_brief(events, flagged, stamp, hours_ahead)
    brief_path = write_brief(output_dir, "macro", brief, stamp=stamp)
    payload = {
        "agent": "macro",
        "stamp": stamp,
        "as_of": utc_now_iso(),
        "hours_ahead": hours_ahead,
        "event_count": len(events),
        "flagged_count": len(flagged),
        "events": [
            {
                "time": ev["time"].isoformat() if hasattr(ev["time"], "isoformat") else str(ev["time"]),
                "currency": ev["currency"],
                "title": ev["title"],
                "impact": ev["impact"],
                "historically_moves_pct": ev["historically_moves_pct"],
                "flags_majors": bool(ev.get("flags_majors")),
                "affected_tickers": [t for t in ev.get("affected_tickers") or [] if t in tickers],
            }
            for ev in events
        ],
        "findings": findings,
        "brief_path": str(brief_path),
        "disclaimer": "Facts only. No trading advice.",
    }
    write_state(state_dir or (root / "state"), "macro", payload)
    logger.info("MacroNews Agent wrote %s (%s events, %s flagged)", brief_path, len(events), len(flagged))
    return payload


def _render_brief(events: list, flagged: list, stamp: str, hours_ahead: int) -> str:
    lines = [
        f"# Macro brief {stamp}",
        "",
        f"High-impact calendar, next {hours_ahead}h UTC. Facts only — no trading advice.",
        "",
    ]
    if not events:
        lines.append("No scheduled prints inside the horizon.")
        lines.append("")
        return "\n".join(lines)

    rows = []
    for ev in events:
        when = ev["time"].strftime("%a %H:%M") if hasattr(ev["time"], "strftime") else str(ev["time"])
        flag = "yes" if ev.get("flags_majors") else "—"
        rows.append(
            [
                when,
                ev["currency"],
                ev["title"],
                ev["impact"],
                f"{ev['historically_moves_pct']:.1f}%",
                flag,
            ]
        )
    lines.append(md_table(["When UTC", "CCY", "Event", "Impact", "Hist move", ">0.8%"], rows))
    lines.append("")
    if flagged:
        lines.append(f"{len(flagged)} print(s) historically move majors / BTC / ETH by more than 0.8%.")
    else:
        lines.append("No >0.8% historical-move flags in this window.")
    lines.append("")
    return "\n".join(lines)
