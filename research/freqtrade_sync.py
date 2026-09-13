"""Sync script to generate Freqtrade config.json from RegimeDesk's risk_limits.yaml and watchlist.csv.

This script derives Freqtrade `stoploss`, `stake_amount` and `max_open_trades`
from the authoritative `config/risk_limits.yaml` and writes
`freqtrade_integration/config.json`. `dry_run` is hardcoded to `true`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "freqtrade_integration" / "config.json"
RISK_LIMITS = ROOT / "config" / "risk_limits.yaml"
WATCHLIST = ROOT / "config" / "watchlist.csv"


def load_watchlist_pairs(path: Path) -> List[str]:
    out: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("asset_class", "").strip().lower() == "crypto":
                t = row.get("ticker", "")
                if t.endswith("USDT"):
                    out.append(f"{t[:-4]}/USDT")
                elif t.endswith("USD"):
                    out.append(f"{t[:-3]}/USD")
                else:
                    out.append(t)
    return out


def compute_expected_params(risk_limits: Dict[str, Any]) -> Dict[str, Any]:
    account = risk_limits["account"]
    position_sizing = risk_limits["position_sizing"]
    exposure = risk_limits["exposure"]

    starting_equity = float(account["starting_equity"])
    risk_per_trade_pct = float(position_sizing["risk_per_trade_pct"])  # e.g., 0.75
    max_position_size_pct = float(position_sizing["max_position_size_pct"])  # e.g., 8.0
    max_crypto_positions = int(exposure["max_crypto_positions"])  # e.g., 3

    # Derivation per test expectations
    stoploss_abs = risk_per_trade_pct / max_position_size_pct
    stoploss = -stoploss_abs / 100.0

    risk_amount_usd = starting_equity * (risk_per_trade_pct / 100.0)
    stake_amount = risk_amount_usd / abs(stoploss)
    max_stake_amount = starting_equity * (max_position_size_pct / 100.0)
    if stake_amount > max_stake_amount:
        stake_amount = max_stake_amount

    return {
        "stoploss": stoploss,
        "stake_amount": stake_amount,
        "max_open_trades": max_crypto_positions,
    }


def main() -> int:
    cfg = yaml.safe_load(RISK_LIMITS.read_text())
    pairs = load_watchlist_pairs(WATCHLIST)
    params = compute_expected_params(cfg)

    out = {
        "dry_run": True,
        "pairlists": pairs,
        "max_open_trades": params["max_open_trades"],
        "stoploss": float(params["stoploss"]),
        "stake_currency": cfg.get("account", {}).get("base_currency", "USD"),
        "stake_amount": float(params["stake_amount"]),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote freqtrade config to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
