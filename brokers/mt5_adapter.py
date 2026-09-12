"""MetaTrader5 adapter — OPTIONAL live execution, per-account, explicit opt-in.

Uses the MetaTrader5 Python package exactly per MT5's documented API:
mt5.initialize(), mt5.login(login, password=..., server=...),
mt5.order_send(request), mt5.account_info(), mt5.positions_get().
No invented endpoints.

SAFETY GATES (defense in depth — core/executor.py enforces these too):
1. Refuses every order unless the account's OWN config file says
   mode: live, set manually by that user. Never by an LLM, agent, default,
   or code path (config_loader refuses programmatic overrides).
2. Refuses every live order unless risk_disclosure_accepted is true with
   a timestamp, REGARDLESS of any other setting.
3. Credentials come from the per-account config and are never logged.
4. The executor routes every order through the unchanged RiskManager veto
   and the per-account kill switch before this adapter is ever called.

The MetaTrader5 package runs on Windows with a running MT5 terminal; in
other environments the import raises and the adapter stays unavailable —
paper mode remains the default everywhere.
"""
from .base_broker import Broker


class MT5Broker(Broker):
    name = "mt5"
    paper = False  # this is a LIVE broker — never used for paper accounts

    def __init__(self, account, mt5_module=None, journal=None):
        """account: core.config_loader.AccountConfig.
        mt5_module: injectable for testing (defaults to lazy import of the
        MetaTrader5 package)."""
        self._account = account
        self._mt5_module = mt5_module
        self._journal = journal
        self._initialized = None

    def _mt5(self):
        if self._mt5_module is not None:
            return self._mt5_module
        import MetaTrader5 as mt5  # documented package name
        self._mt5_module = mt5
        return mt5

    def _refuse(self, reasons):
        if self._journal is not None:
            self._journal.append({"type": "refusal", "stage": "mt5_adapter",
                                  "reasons": reasons})
        return {"status": "refused", "stage": "mt5_adapter", "reasons": reasons}

    def _connect(self):
        mt5 = self._mt5()
        if not mt5.initialize():
            raise ConnectionError("MT5 initialize failed: %s"
                                  % mt5.last_error())
        # credentials from the per-account config only; never logged
        ok = mt5.login(self._account.mt5_login,
                       password=self._account.mt5_password,
                       server=self._account.mt5_server)
        if not ok:
            raise ConnectionError("MT5 login failed: %s" % mt5.last_error())
        self._initialized = True

    def place_order(self, order):
        # GATE 1: explicit live opt-in from the account's own config file
        if self._account.mode != "live":
            return self._refuse(["account mode is %r; live orders require "
                                 "mode: live set manually in the account file"
                                 % self._account.mode])
        # GATE 2: risk disclosure — absolute refusal when false
        if not self._account.risk_disclosure_accepted:
            return self._refuse(["risk_disclosure_accepted is false; refusing "
                                 "live order regardless of other settings"])
        if not self._account.mt5_login or not self._account.mt5_password \
                or not self._account.mt5_server:
            return self._refuse(["incomplete MT5 credentials in account config"])

        if self._initialized is not True:
            self._connect()

        mt5 = self._mt5()
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order["instrument"].replace("_", ""),
            "volume": float(order["size"]),
            "type": mt5.ORDER_TYPE_BUY if order["side"] == "long"
            else mt5.ORDER_TYPE_SELL,
            "price": float(order["entry_price"]),
            "sl": float(order["stop_loss"]),
            "deviation": 20,
            "magic": 20260912,
            "comment": "regimedesk",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        tp = order.get("take_profit") or order.get("suggested_take_profit")
        if tp is not None:
            request["tp"] = float(tp)
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError("MT5 order_send returned None: %s"
                               % mt5.last_error())
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"status": "rejected", "stage": "mt5_adapter",
                    "retcode": result.retcode, "comment": result.comment}
        return {
            "status": "filled",
            "mode": "live",
            "order_ticket": result.order,
            "price": result.price if hasattr(result, "price") else
            float(order["entry_price"]),
            "volume": result.volume,
        }

    def account_state(self):
        if self._initialized is not True:
            self._connect()
        mt5 = self._mt5()
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account_info failed: %s"
                               % mt5.last_error())
        return {"balance": info.balance, "equity": info.equity,
                "currency": info.currency, "mode": "live"}

    def positions(self):
        if self._initialized is not True:
            self._connect()
        mt5 = self._mt5()
        raw = mt5.positions_get() or []
        return [{"instrument": p.symbol, "side": "long" if p.type == 0
                 else "short", "size": p.volume, "entry_price": p.price_open,
                 "profit": p.profit} for p in raw]

def probe_terminal(mt5_module=None):
    """READ-ONLY terminal connectivity probe for the doctor command.

    Uses only documented API calls — initialize() (terminal's current
    login), terminal_info(), account_info(), last_error(), shutdown().
    Sends no orders, reads no credentials, logs no secrets; the account
    number is masked. Returns (ok: bool, detail: str)."""
    if mt5_module is None:
        try:
            import MetaTrader5 as mt5_module
        except ImportError as exc:
            return False, ("MetaTrader5 package not installed (%s) — "
                           "live mode unavailable; paper mode unaffected"
                           % exc)
    try:
        if not mt5_module.initialize():
            return False, ("initialize failed: %s — is the MetaTrader 5 "
                           "terminal installed, running, and logged in?"
                           % (mt5_module.last_error(),))
        ti = mt5_module.terminal_info()
        if ti is None:
            return False, ("terminal_info unavailable: %s"
                           % (mt5_module.last_error(),))
        detail = ("terminal connected, trade_allowed=%s"
                  % getattr(ti, "trade_allowed", "?"))
        ai = mt5_module.account_info()
        if ai is not None:
            login = str(getattr(ai, "login", ""))
            masked = ("***" + login[-3:]) if len(login) >= 3 else "***"
            detail += (" | account #%s on %s: balance %s %s, leverage 1:%s"
                       % (masked, getattr(ai, "server", "?"),
                          getattr(ai, "balance", "?"),
                          getattr(ai, "currency", "?"),
                          getattr(ai, "leverage", "?")))
        return True, detail
    except Exception as exc:  # noqa: BLE001 — probe never crashes the doctor
        return False, "probe error: %s" % exc
    finally:
        try:
            mt5_module.shutdown()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
