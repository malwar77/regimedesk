"""Doctor command: MT5 terminal probe tests.

The probe is READ-ONLY: it may initialize/inspect the terminal, but it
must never place orders, read credentials, or print the full account
number. These tests pin that down with a stub of the documented API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brokers.mt5_adapter import probe_terminal  # noqa: E402


class FakeMT5Probe:
    """Stub of the documented MetaTrader5 surface the probe uses."""

    def __init__(self, init_ok=True, trade_allowed=True, login="12345678",
                 balance=10500.0, currency="USD", server="DemoServer",
                 leverage=100):
        self.init_ok = init_ok
        self.initialized = False
        self.shutdown_calls = 0
        self.order_send_calls = 0  # must stay zero: read-only probe
        self._ti = type("TI", (), {"trade_allowed": trade_allowed})
        self._ai = type("AI", (), {
            "login": login, "balance": balance, "currency": currency,
            "server": server, "leverage": leverage})

    def initialize(self):
        self.initialized = True
        return self.init_ok

    def last_error(self):
        return (-6, "terminal not running")

    def terminal_info(self):
        return self._ti if self.init_ok else None

    def account_info(self):
        return self._ai if self.init_ok else None

    def order_send(self, request):  # must never be called by the probe
        self.order_send_calls += 1
        raise AssertionError("probe_terminal must never send orders")

    def shutdown(self):
        self.shutdown_calls += 1
        self.initialized = False


class TestProbeTerminal:
    def test_healthy_terminal(self):
        fake = FakeMT5Probe()
        ok, detail = probe_terminal(fake)
        assert ok is True
        assert "terminal connected" in detail
        assert "balance 10500.0 USD" in detail
        assert "DemoServer" in detail
        # read-only hygiene
        assert fake.order_send_calls == 0
        assert fake.shutdown_calls == 1

    def test_account_number_masked(self):
        fake = FakeMT5Probe(login="12345678")
        ok, detail = probe_terminal(fake)
        assert ok is True
        assert "***678" in detail          # masked
        assert "12345678" not in detail    # full number never printed

    def test_terminal_not_running(self):
        fake = FakeMT5Probe(init_ok=False)
        ok, detail = probe_terminal(fake)
        assert ok is False
        assert "initialize failed" in detail
        assert "terminal installed, running, and logged in" in detail
        assert fake.shutdown_calls == 1  # cleanup even on failure

    def test_probe_error_degrades_not_crashes(self):
        class Boom:
            def initialize(self):
                raise RuntimeError("weird terminal state")

            def shutdown(self):
                pass
        ok, detail = probe_terminal(Boom())
        assert ok is False
        assert "probe error" in detail

    def test_package_missing_reports_cleanly(self, monkeypatch):
        # simulate MetaTrader5 not being installed
        monkeypatch.setitem(sys.modules, "MetaTrader5", None)
        ok, detail = probe_terminal(None)
        assert ok is False
        assert "not installed" in detail
        assert "paper mode unaffected" in detail
