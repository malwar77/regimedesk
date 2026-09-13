"""Nightly research swarm runner.

Ported from the standalone RegimeDesk (Obi's local build): runs the
research agents in schedule order and writes markdown briefs + state
JSON. Research only — nothing here proposes an executable trade, and
the RiskManager gates in core/ remain untouched and absolute.

The agents run on deterministic synthetic/proxy data from
research/data_pipeline.py by default (paper-mode development data,
clearly labeled in every brief).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from research.agents import (chief_of_staff, macro_news, onchain_flow,
                             regime, sentiment, technical)

logger = logging.getLogger(__name__)


def load_settings(root: Path) -> Dict[str, Any]:
    """config/settings.yaml if present, else defaults."""
    path = root / "config" / "settings.yaml"
    if path.exists():
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
        return data
    return {}


def run_nightly_research(root: Optional[Path] = None,
                         config: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
    """Trigger all research agents in schedule order.

    Writes briefs to {root}/briefs and machine state to {root}/state.
    Returns a dict of per-agent results — facts, never orders.
    """
    root = Path(root or ".")
    config = config if config is not None else load_settings(root)
    briefs = root / "briefs"
    state = root / "state"
    watchlist = root / "config" / "watchlist.csv"
    briefs.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Any] = {}
    results["macro"] = macro_news.run(
        watchlist, briefs, root=root, config=config, state_dir=state)
    results["regime"] = regime.run(root, briefs, state, config=config)
    results["onchain"] = onchain_flow.run(
        watchlist, briefs, root=root, config=config, state_dir=state)
    results["sentiment"] = sentiment.run(
        watchlist, briefs, root=root, config=config, state_dir=state)
    results["technical"] = technical.run(
        root, watchlist, briefs, config=config, state_dir=state)
    results["chief"] = chief_of_staff.run(briefs, briefs, state_dir=state)
    logger.info("nightly research complete")
    return results


if __name__ == "__main__":                                  # pragma: no cover
    out = run_nightly_research(Path(__file__).resolve().parent.parent)
    print(json.dumps({k: str(v) for k, v in out.items()}, indent=2))
