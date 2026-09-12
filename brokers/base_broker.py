"""Broker interface. All brokers — paper or live — implement this."""
from abc import ABC, abstractmethod


class Broker(ABC):
    name = "broker"
    paper = True  # every broker states loudly whether it is paper or live

    @abstractmethod
    def place_order(self, order):
        """order = {"instrument", "side", "size", "entry_price",
        "stop_loss", ...}. Returns {"status": "filled"|"rejected", ...}."""

    @abstractmethod
    def account_state(self):
        """{"balance", "equity", "currency", ...} — real numbers only."""

    @abstractmethod
    def positions(self):
        """Open positions as a list of dicts."""
