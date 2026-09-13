"""Trading seasons: contiguous runs of executed orders, per mode.

A "season" is an honest unit of track record — a contiguous stretch of
trading in ONE mode (DEMO = paper, REAL MONEY = live). Seasons are
derived from the append-only journal; nothing is invented or smoothed.
A gap of more than GAP_DAYS days of inactivity ends a season and
starts a new one on the next order.

A REAL MONEY season can only ever appear here if a human has ALREADY
edited the account YAML to `mode: live` by hand — this module records
history. It never creates, switches, or approves anything. Paper
seasons do not predict live performance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

GAP_DAYS = 7.0
LABELS = {"paper": "DEMO", "live": "REAL MONEY"}


def _parse_ts(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def compute_seasons(entries: List[Dict[str, Any]],
                    gap_days: float = GAP_DAYS) -> List[Dict[str, Any]]:
    """Group journaled orders into seasons. Sorted, honest, read-only."""
    orders = []
    for e in entries:
        if e.get("type") != "order":
            continue
        t = _parse_ts(e.get("ts"))
        if t is None or e.get("mode") not in LABELS:
            continue
        orders.append((t, e["mode"]))
    orders.sort(key=lambda x: x[0])

    seasons: List[Dict[str, Any]] = []
    for t, mode in orders:
        if (seasons and seasons[-1]["_mode"] == mode
                and (t - seasons[-1]["_end"]).total_seconds()
                <= gap_days * 86400):
            s = seasons[-1]
            s["_end"] = t
            s["orders"] += 1
        else:
            seasons.append({"_mode": mode, "label": LABELS[mode],
                            "_start": t, "_end": t, "orders": 1})
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(seasons, 1):
        out.append({
            "season": i, "mode": s["_mode"], "label": s["label"],
            "start": s["_start"].isoformat(), "end": s["_end"].isoformat(),
            "orders": s["orders"],
            "span_days": round(
                (s["_end"] - s["_start"]).total_seconds() / 86400, 2),
        })
    return out


def paper_track_record(entries: List[Dict[str, Any]]) -> Tuple[int, float]:
    """(paper_order_count, days_spanned) from journal entries.

    Returns (0, 0.0) when there is no paper history — never invented.
    """
    ts = [_parse_ts(e.get("ts")) for e in entries
          if e.get("type") == "order" and e.get("mode") == "paper"]
    ts = [t for t in ts if t is not None]
    if not ts:
        return 0, 0.0
    span = (max(ts) - min(ts)).total_seconds() / 86400
    return len(ts), max(0.0, span)
