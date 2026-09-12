"""Position sizing — a pure calculator.

position_size = (account_balance * risk_pct/100) / (stop_loss_pips * pip_value)

This helper does NOT bypass or duplicate the RiskManager's authority:
risk_manager.py independently validates risk_pct against risk_limits.yaml
(and its own stop-loss/size rules) before any trade proceeds.
"""


def calculate_position_size(account_balance, risk_pct, stop_loss_pips, pip_value):
    if account_balance <= 0:
        raise ValueError("account_balance must be positive")
    if risk_pct < 0:
        raise ValueError("risk_pct must be >= 0")
    if stop_loss_pips <= 0:
        raise ValueError("stop_loss_pips must be positive")
    if pip_value <= 0:
        raise ValueError("pip_value must be positive")
    return (account_balance * risk_pct / 100.0) / (stop_loss_pips * pip_value)
