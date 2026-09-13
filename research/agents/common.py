"""Shared helpers for research agents: stamps, file IO, finding records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def finding(
    *,
    agent: str,
    summary: str,
    direction: str = "none",
    symbol: Optional[str] = None,
    symbols: Optional[Iterable[str]] = None,
    facts: Optional[List[str]] = None,
    kind: str = "info",
) -> Dict[str, Any]:
    """A single fact the Chief of Staff may rank. Never a trade order."""
    tickers = [symbol] if symbol else list(symbols or [])
    return {
        "agent": agent,
        "symbol": tickers[0] if len(tickers) == 1 else None,
        "symbols": tickers,
        "direction": direction,
        "kind": kind,
        "summary": summary,
        "facts": facts or [],
    }


def write_brief(output_dir: Path, name: str, body: str, stamp: Optional[str] = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}_{stamp or utc_stamp()}.md"
    path.write_text(body, encoding="utf-8")
    return path


def write_state(state_dir: Path, name: str, payload: Dict[str, Any]) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    serializable = json.loads(json.dumps(payload, default=str))
    path = state_dir / f"{name}_latest.json"
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    return path


def read_state(state_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    path = state_dir / f"{name}_latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def md_table(headers: List[str], rows: List[List[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
