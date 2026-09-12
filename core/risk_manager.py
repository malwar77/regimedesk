"""RiskManager — the immutable veto layer.

RISK LIVES IN CODE, NEVER IN PROMPTS. Every order — paper or live — must
pass RiskManager.veto() before it reaches any broker. There is no code path
around this (see core/executor.py: it is the only route to a broker, and it
calls veto() first).

Checks (from risk_limits.yaml):
- required_stop_loss: an order MUST carry a stop-loss on the correct side
- risk_pct per trade <= max_risk_pct_per_trade
- position size <= max_position_size
- trades today (via the journal counter) <= max_daily_trades

The per-account drawdown kill switch (core/kill_switch.py) is a separate,
additional gate — this file does not duplicate it.
"""
import os
from .config_loader import parse_simple_yaml


class RiskLimitsError(Exception):
    pass


class RiskManager:
    def __init__(self, limits_path="risk_limits.yaml", trades_today_fn=None):
        if not os.path.exists(limits_path):
            raise RiskLimitsError("risk_limits.yaml missing at %s" % limits_path)
        with open(limits_path) as f:
            limits = parse_simple_yaml(f.read())
        self.max_risk_pct_per_trade = float(limits["max_risk_pct_per_trade"])
        self.max_position_size = float(limits["max_position_size"])
        self.max_daily_trades = int(limits["max_daily_trades"])
        self.required_stop_loss = bool(limits.get("required_stop_loss", True))
        self.trades_today_fn = trades_today_fn or (lambda user_id: 0)
        # immutable once loaded — no setter exists on purpose
        self._locked = True

    def __setattr__(self, name, value):
        if getattr(self, "_locked", False) and not name.startswith("_"):
            raise RiskLimitsError(
                "risk limits are immutable at runtime; edit risk_limits.yaml "
                "and restart")
        object.__setattr__(self, name, value)

    def veto(self, order, trades_today=None):
        """Return (approved: bool, reasons: list[str]). Never raises on a bad
        order — a refusal is data, not an exception.

        trades_today: optional explicit count; defaults to the manager's
        trades_today_fn. The manager is never mutated by callers."""
        if trades_today is None:
            trades_today = self.trades_today_fn(order.get("user_id", ""))
        reasons = []
        side = order.get("side")
        entry = order.get("entry_price")
        sl = order.get("stop_loss")
        size = order.get("size")
        risk_amount = order.get("risk_amount")
        balance = order.get("account_balance")

        if side not in ("long", "short"):
            reasons.append("invalid side")
        if self.required_stop_loss:
            if sl is None:
                reasons.append("order has no stop-loss")
            elif side == "long" and not sl < entry:
                reasons.append("long order stop-loss must be below entry")
            elif side == "short" and not sl > entry:
                reasons.append("short order stop-loss must be above entry")
        if size is None or size <= 0:
            reasons.append("invalid size")
        elif size > self.max_position_size:
            reasons.append("size %.4f exceeds max_position_size %.4f"
                           % (size, self.max_position_size))
        if balance and balance > 0 and risk_amount is not None:
            risk_pct = risk_amount / balance * 100.0
            if risk_pct > self.max_risk_pct_per_trade:
                reasons.append("risk %.2f%% exceeds max_risk_pct_per_trade "
                               "%.2f%%" % (risk_pct, self.max_risk_pct_per_trade))
        elif risk_amount is None:
            reasons.append("order carries no risk_amount")
        if trades_today >= self.max_daily_trades:
            reasons.append("max_daily_trades %d reached (%d today)"
                           % (self.max_daily_trades, trades_today))
        return (len(reasons) == 0), reasons
