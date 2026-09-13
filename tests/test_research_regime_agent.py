"""Regime research agent writes a brief and state file."""

from __future__ import annotations

from pathlib import Path
import shutil

from research.agents.regime import run


def test_regime_agent_writes_brief_and_state(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    work = tmp_path / "desk"
    shutil.copytree(root / "config", work / "config")
    details = run(work, work / "briefs", work / "state")
    assert "EURUSD" in details
    briefs = list((work / "briefs").glob("regime_*.md"))
    assert briefs
    assert (work / "state" / "current_regimes.json").exists()
    text = briefs[0].read_text(encoding="utf-8")
    assert "EURUSD" in text
    assert "no trading advice" in text.lower()
