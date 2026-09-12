"""Forex PAPER broker — the hard default execution venue.

Simulates fills at the order price and tracks a paper balance. It is always
labeled paper: result dicts carry "mode": "paper" so no output can be
mistaken for live trading.
"""
from .base_broker import Broker


class OandaPaperBroker(Broker):
    name = "oanda_paper"
    paper = True

    def __init__(self, starting_balance=10000.0, currency="USD"):
        self.balance = float(starting_balance)
        self.currency = currency
        self._positions = []
        self._next_id = 1

    def place_order(self, order):
        price = order.get("entry_price")
        if price is None or price <= 0:
            return {"status": "rejected", "reason": "invalid price",
                    "mode": "paper"}
        pos = {
            "id": self._next_id,
            "instrument": order["instrument"],
            "side": order["side"],
            "size": order["size"],
            "entry_price": price,
            "stop_loss": order.get("stop_loss"),
            "take_profit": order.get("take_profit"),
        }
        self._next_id += 1
        self._positions.append(pos)
        return {
            "status": "filled",
            "mode": "paper",
            "order_id": pos["id"],
            "price": price,
            "equity_after": self.balance,  # unchanged at entry (no leverage costs modeled)
        }

    def account_state(self):
        return {"balance": self.balance, "equity": self.balance,
                "currency": self.currency, "mode": "paper"}

    def positions(self):
        return list(self._positions)
