"""Crypto PAPER broker (ccxt data, paper execution).

Prices may be refreshed from a ccxt exchange when available; execution is
always simulated. Result dicts carry "mode": "paper".
"""
from .base_broker import Broker


class CcxtCryptoBroker(Broker):
    name = "ccxt_crypto"
    paper = True

    def __init__(self, starting_balance=10000.0, currency="USD",
                 exchange_id=None):
        self.balance = float(starting_balance)
        self.currency = currency
        self._positions = []
        self._next_id = 1
        self._exchange_id = exchange_id

    def _refresh_price(self, instrument):
        if not self._exchange_id:
            return None
        try:
            import ccxt
            ex = getattr(ccxt, self._exchange_id)()
            ticker = ex.fetch_ticker(instrument.replace("_", "/"))
            return float(ticker["last"])
        except Exception:
            return None

    def place_order(self, order):
        price = self._refresh_price(order["instrument"]) \
            or order.get("entry_price")
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
        return {"status": "filled", "mode": "paper",
                "order_id": pos["id"], "price": price,
                "equity_after": self.balance}

    def account_state(self):
        return {"balance": self.balance, "equity": self.balance,
                "currency": self.currency, "mode": "paper"}

    def positions(self):
        return list(self._positions)
