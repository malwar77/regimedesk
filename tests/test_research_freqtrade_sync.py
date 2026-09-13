import json
from pathlib import Path

from research.freqtrade_sync import main as sync_main


def test_freqtrade_sync_writes_config(tmp_path: Path):
    # Run the sync script (it writes to freqtrade_integration/config.json)
    root = Path(__file__).resolve().parents[1]
    # Ensure script runs without raising
    sync_main()
    cfg_path = root / "freqtrade_integration" / "config.json"
    assert cfg_path.exists(), "Freqtrade config was not written"
    cfg = json.loads(cfg_path.read_text())
    # Basic sanity checks
    assert cfg.get("dry_run") is True
    assert isinstance(cfg.get("pairlists"), list)
    assert cfg.get("max_open_trades") is not None
    assert float(cfg.get("stoploss")) <= 0
"""
Test to ensure Freqtrade config.json stays in sync with risk_limits.yaml.
This test verifies that the stoploss, stake_amount, and max_open_trades in
Freqtrade's config.json are derived correctly from the risk_limits.yaml.
"""

import json
import yaml
import subprocess
import sys
from pathlib import Path

def load_risk_limits() -> dict:
    """Load risk_limits.yaml."""
    risk_limits_path = Path(__file__).parent.parent / "config" / "risk_limits.yaml"
    with risk_limits_path.open("r") as f:
        return yaml.safe_load(f)

def load_watchlist() -> list:
    """Load watchlist.csv and return crypto pairs in Freqtrade format."""
    watchlist_path = Path(__file__).parent.parent / "config" / "watchlist.csv"
    crypto_pairs = []
    with watchlist_path.open("r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["asset_class"] == "crypto":
                ticker = row["ticker"]
                if ticker.endswith("USDT"):
                    pair = f"{ticker[:-4]}/USDT"
                elif ticker.endswith("USD"):
                    pair = f"{ticker[:-3]}/USD"
                else:
                    if len(ticker) >= 4:
                        pair = f"{ticker[:-3]}/{ticker[-3:]}"
                    else:
                        pair = ticker
                crypto_pairs.append(pair)
    return crypto_pairs

def compute_expected_params(risk_limits: dict) -> dict:
    """Compute expected Freqtrade parameters from risk_limits."""
    account = risk_limits["account"]
    position_sizing = risk_limits["position_sizing"]
    exposure = risk_limits["exposure"]
    orders = risk_limits["orders"]

    starting_equity = float(account["starting_equity"])
    risk_per_trade_pct = float(position_sizing["risk_per_trade_pct"])
    max_position_size_pct = float(position_sizing["max_position_size_pct"])
    max_crypto_positions = int(exposure["max_crypto_positions"])

    # Calculate stoploss (absolute value)
    stoploss_abs = risk_per_trade_pct / max_position_size_pct  # in percentage
    stoploss = -stoploss_abs / 100.0  # Freqtrade expects a negative decimal

    # Calculate stake_amount
    risk_amount_usd = starting_equity * (risk_per_trade_pct / 100.0)
    stake_amount = risk_amount_usd / abs(stoploss)  # in USDT

    # Ensure stake_amount does not exceed max_position_size_pct of equity
    max_stake_amount = starting_equity * (max_position_size_pct / 100.0)
    if stake_amount > max_stake_amount:
        stake_amount = max_stake_amount

    # max_open_trades
    max_open_trades = max_crypto_positions

    return {
        "stoploss": stoploss,
        "stake_amount": stake_amount,
        "max_open_trades": max_open_trades,
    }

def test_freqtrade_config_sync():
    """Test that Freqtrade config.json is in sync with risk_limits.yaml."""
    # Run the sync script to generate config.json
    sync_script = Path(__file__).parent.parent / "research" / "freqtrade_sync.py"
    result = subprocess.run(
        [sys.executable, str(sync_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Sync script failed: {result.stderr}"

    # Load the generated config
    config_path = Path(__file__).parent.parent / "freqtrade_integration" / "config.json"
    assert config_path.exists(), "Freqtrade config.json was not generated"
    with config_path.open("r") as f:
        config = json.load(f)

    # Load risk limits and compute expected values
    risk_limits = load_risk_limits()
    expected = compute_expected_params(risk_limits)

    # Compare stoploss
    assert abs(config["stoploss"] - expected["stoploss"]) < 1e-9, \
        f"Stoploss mismatch: expected {expected['stoploss']}, got {config['stoploss']}"

    # Compare stake_amount
    assert abs(config["stake_amount"] - expected["stake_amount"]) < 1e-9, \
        f"Stake amount mismatch: expected {expected['stake_amount']}, got {config['stake_amount']}"

    # Compare max_open_trades
    assert config["max_open_trades"] == expected["max_open_trades"], \
        f"Max open trades mismatch: expected {expected['max_open_trades']}, got {config['max_open_trades']}"

    # Additionally, check that dry_run is True
    assert config["dry_run"] is True, "dry_run must be True"

    print("All tests passed!")

if __name__ == "__main__":
    # Import csv inside the function to avoid top-level import if not needed
    import csv
    test_freqtrade_config_sync()
