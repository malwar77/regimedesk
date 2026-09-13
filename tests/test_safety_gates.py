"""Safety-critical tests: RiskManager veto, execution gate chain, the
risk-disclosure block, per-account kill switch isolation, MT5 adapter
behavior with a stub, and the no-programmatic-mode-switch guarantee."""
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

TODAY = datetime.now(timezone.utc)


from core.config_loader import (AccountConfig, AccountConfigError,
                                 load_account)
from core.kill_switch import AccountKillSwitch, Journal, evaluate_drawdown
from core.risk_manager import RiskManager
from brokers.mt5_adapter import MT5Broker
from brokers.oanda_paper import OandaPaperBroker
from core.executor import execute_order


# ---------------------------------------------------------------- helpers

class FakeMT5:
    """Stub of the MetaTrader5 package surface we use (documented API)."""
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.init_calls = 0
        self.login_args = None
        self.requests = []
        self.order_send_calls = 0

    def initialize(self):
        self.init_calls += 1
        return True

    def last_error(self):
        return (0, "ok")

    def login(self, login, password=None, server=None):
        self.login_args = (login, password, server)
        return True

    def order_send(self, request):
        self.order_send_calls += 1
        self.requests.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=777,
                                volume=request["volume"], price=request["price"])


def write_account(tmp_path, user_id, mode, disclosure, **extra):
    d = tmp_path / "accounts"
    d.mkdir(exist_ok=True)
    lines = ["user_id: %s" % user_id, "mode: %s" % mode,
             "risk_disclosure_accepted: %s" % ("true" if disclosure else "false"),
             "risk_disclosure_accepted_at: %s"
             % ("2026-09-01T00:00:00+00:00" if disclosure else "null"),
             "daily_drawdown_limit_pct: 3.0",
             "weekly_drawdown_limit_pct: 6.0",
             "mt5_login: \"12345\"", "mt5_password: \"sekret\"",
             "mt5_server: \"DemoServer\""]
    for k, v in extra.items():
        lines.append("%s: %s" % (k, v))
    (d / ("%s.yaml" % user_id)).write_text("\n".join(lines) + "\n")
    return str(d)


def proposal_order(p, **over):
    """Executor-shaped order for direct RiskManager.veto calls."""
    o = {"user_id": p.get("user_id", "test"),
         "instrument": p["instrument"], "side": p["direction"],
         "size": p.get("suggested_position_size"),
         "entry_price": p.get("entry_price"),
         "stop_loss": p.get("suggested_stop_loss"),
         "risk_amount": p.get("risk_amount"),
         "account_balance": p.get("account_balance")}
    o.update(over)
    return o


def good_proposal(**over):
    p = {
        "signal_id": "TEST-1", "instrument": "EUR_USD", "direction": "long",
        "entry_price": 1.1000, "suggested_stop_loss": 1.0950,
        "suggested_take_profit": 1.1100, "suggested_position_size": 0.2,
        "risk_amount": 100.0,
    }
    p.update(over)
    return p


def paper_journal_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    return str(d)


def make_rm(tmp_path):
    (tmp_path / "risk_limits.yaml").write_text(
        "max_risk_pct_per_trade: 1.0\nmax_position_size: 10.0\n"
        "max_daily_trades: 10\nrequired_stop_loss: true\n")
    return RiskManager(limits_path=str(tmp_path / "risk_limits.yaml"))


# ------------------------------------------------------- RiskManager

class TestRiskManager:
    def rm(self, tmp_path):
        (tmp_path / "risk_limits.yaml").write_text(
            "max_risk_pct_per_trade: 1.0\nmax_position_size: 10.0\n"
            "max_daily_trades: 10\nrequired_stop_loss: true\n")
        return RiskManager(limits_path=str(tmp_path / "risk_limits.yaml"))

    def test_valid_order_approved(self, tmp_path):
        ok, reasons = self.rm(tmp_path).veto(proposal_order(good_proposal(
            account_balance=10000)))
        assert ok and reasons == []

    def test_missing_stop_loss_refused(self, tmp_path):
        ok, reasons = self.rm(tmp_path).veto(proposal_order(good_proposal(
            suggested_stop_loss=None), account_balance=10000))
        assert not ok and any("stop-loss" in r for r in reasons)

    def test_wrong_side_stop_refused(self, tmp_path):
        ok, _ = self.rm(tmp_path).veto(proposal_order(good_proposal(
            suggested_stop_loss=1.1050), account_balance=10000))
        assert not ok  # long order with stop above entry

    def test_excess_risk_pct_refused(self, tmp_path):
        ok, reasons = self.rm(tmp_path).veto(proposal_order(good_proposal(
            risk_amount=200.0), account_balance=10000))  # 2% > 1% limit
        assert not ok and any("max_risk_pct" in r for r in reasons)

    def test_oversize_refused(self, tmp_path):
        ok, reasons = self.rm(tmp_path).veto(proposal_order(good_proposal(
            suggested_position_size=20.0, risk_amount=50), account_balance=10000))
        assert not ok and any("max_position_size" in r for r in reasons)

    def test_daily_trade_cap(self, tmp_path):
        (tmp_path / "risk_limits.yaml").write_text(
            "max_risk_pct_per_trade: 1.0\nmax_position_size: 10.0\n"
            "max_daily_trades: 2\nrequired_stop_loss: true\n")
        rm = RiskManager(limits_path=str(tmp_path / "risk_limits.yaml"),
                         trades_today_fn=lambda uid: 2)
        ok, reasons = rm.veto(proposal_order(good_proposal(), account_balance=10000))
        assert not ok and any("max_daily_trades" in r for r in reasons)

    def test_limits_immutable_at_runtime(self, tmp_path):
        rm = self.rm(tmp_path)
        with pytest.raises(Exception):
            rm.max_risk_pct_per_trade = 99.0


# --------------------------------------- THE disclosure-blocks-live test

class TestRiskDisclosureGate:
    def test_false_disclosure_blocks_live_orders(self, tmp_path):
        """REQUIRED TEST: risk_disclosure_accepted: false blocks ALL live
        orders even when mode: live is set — at both the executor and the
        MT5 adapter."""
        accounts = write_account(tmp_path, "alice", mode="live",
                                 disclosure=False)
        fake = FakeMT5()
        account = load_account("alice", accounts_dir=accounts)
        assert account.mode == "live"
        assert account.risk_disclosure_accepted is False
        journal = Journal("alice", paper_journal_dir(tmp_path))
        broker = MT5Broker(account, mt5_module=fake, journal=journal)

        result = execute_order(
            good_proposal(), "alice", broker,
            make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))

        assert result["status"] == "refused"
        assert result["stage"] == "risk_disclosure"
        assert fake.order_send_calls == 0      # MT5 never touched
        assert fake.init_calls == 0            # not even initialized

        # defense in depth: the adapter alone also refuses
        direct = broker.place_order({
            "instrument": "EUR_USD", "side": "long", "size": 0.2,
            "entry_price": 1.10, "stop_loss": 1.095})
        assert direct["status"] == "refused"
        assert fake.order_send_calls == 0

    def test_true_disclosure_live_order_reaches_mt5(self, tmp_path):
        accounts = write_account(tmp_path, "bob", mode="live",
                                 disclosure=True)
        fake = FakeMT5()
        account = load_account("bob", accounts_dir=accounts)
        broker = MT5Broker(account, mt5_module=fake)
        result = execute_order(
            good_proposal(), "bob", broker, make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))
        assert result["status"] == "filled"
        assert fake.order_send_calls == 1
        req = fake.requests[0]
        assert req["action"] == FakeMT5.TRADE_ACTION_DEAL
        assert req["type"] == FakeMT5.ORDER_TYPE_BUY
        assert req["sl"] == 1.0950 and req["volume"] == 0.2

class TestModeGuards:
    def test_paper_account_never_routes_to_live_broker(self, tmp_path):
        accounts = write_account(tmp_path, "carol", mode="paper",
                                 disclosure=False)
        account = load_account("carol", accounts_dir=accounts)
        fake = FakeMT5()
        broker = MT5Broker(account, mt5_module=fake)  # live broker (paper=False)
        result = execute_order(
            good_proposal(), "carol", broker, make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))
        assert result["status"] == "refused"
        assert result["stage"] == "mode_mismatch"
        assert fake.order_send_calls == 0

    def test_paper_account_fills_on_paper_broker(self, tmp_path):
        accounts = write_account(tmp_path, "dave", mode="paper",
                                 disclosure=False)
        account = load_account("dave", accounts_dir=accounts)
        paper = OandaPaperBroker()
        result = execute_order(
            good_proposal(), "dave", paper, make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))
        assert result["status"] == "filled"
        assert result["mode"] == "paper"

    def test_risk_veto_precedes_everything(self, tmp_path):
        """No path to any broker without the RiskManager pass — a proposal
        with no stop-loss is refused at the risk stage even in paper mode."""
        accounts = write_account(tmp_path, "erin", mode="paper",
                                 disclosure=False)
        account = load_account("erin", accounts_dir=accounts)
        paper = OandaPaperBroker()
        result = execute_order(
            good_proposal(suggested_stop_loss=None), "erin", paper,
            make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))
        assert result["status"] == "refused"
        assert result["stage"] == "risk_manager"
        assert paper.positions() == []


class TestNoProgrammaticSwitching:
    def test_override_mode_refused(self, tmp_path):
        accounts = write_account(tmp_path, "frank", mode="paper",
                                 disclosure=False)
        with pytest.raises(AccountConfigError):
            load_account("frank", accounts_dir=accounts,
                         overrides={"mode": "live"})
        with pytest.raises(AccountConfigError):
            load_account("frank", accounts_dir=accounts,
                         overrides={"risk_disclosure_accepted": True})

    def test_missing_account_file_is_error_not_default_live(self, tmp_path):
        with pytest.raises(AccountConfigError):
            load_account("ghost", accounts_dir=str(tmp_path / "none"))


# ------------------------------------------------------------ kill switch

class TestKillSwitch:
    def test_pure_evaluation(self):
        r = evaluate_drawdown(9600, 10000, 10000, 3.0, 6.0)
        assert r["blocked"] is True          # -4% daily vs 3% limit
        assert any("daily" in x for x in r["reasons"])

    def test_weekly_breach(self):
        r = evaluate_drawdown(9400, 9900, 10000, 3.0, 6.0)
        assert r["blocked"] is True
        assert any("weekly" in x for x in r["reasons"])

    def test_within_limits(self):
        r = evaluate_drawdown(9850, 10000, 10000, 3.0, 6.0)
        assert r["blocked"] is False and r["reasons"] == []

    def test_per_account_isolation(self, tmp_path):
        """A breach blocks ONLY that account — the whole point of the
        per-account kill switch."""
        accounts = write_account(tmp_path, "blocked_user", mode="paper",
                                 disclosure=False)
        write_account(tmp_path, "healthy_user", mode="paper",
                      disclosure=False)
        logs = paper_journal_dir(tmp_path)
        # blocked_user's journal shows 10000 -> 9600 (4% down, 3% limit)
        j1 = Journal("blocked_user", logs)
        j1.append({"type": "order", "equity_after": 10000.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "01:00:00+00:00"})
        j1.append({"type": "order", "equity_after": 9600.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "05:00:00+00:00"})
        # healthy_user is up
        j2 = Journal("healthy_user", logs)
        j2.append({"type": "order", "equity_after": 10000.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "01:00:00+00:00"})
        j2.append({"type": "order", "equity_after": 10100.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "05:00:00+00:00"})

        a1 = load_account("blocked_user", accounts_dir=accounts)
        a2 = load_account("healthy_user", accounts_dir=accounts)
        c1 = AccountKillSwitch(a1, logs).check(9600.0)
        c2 = AccountKillSwitch(a2, logs).check(10100.0)
        assert c1["blocked"] is True
        assert c2["blocked"] is False

    def test_executor_blocks_on_kill_switch(self, tmp_path):
        accounts = write_account(tmp_path, "down_bad", mode="paper",
                                 disclosure=False)
        logs = paper_journal_dir(tmp_path)
        j = Journal("down_bad", logs)
        j.append({"type": "order", "equity_after": 10000.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "01:00:00+00:00"})
        j.append({"type": "order", "equity_after": 9500.0,
                   "ts": TODAY.strftime("%Y-%m-%dT") + "05:00:00+00:00"})  # -5% vs 3% limit
        account = load_account("down_bad", accounts_dir=accounts)
        paper = OandaPaperBroker()
        result = execute_order(
            good_proposal(risk_amount=95.0), "down_bad", paper, make_rm(tmp_path),
            AccountKillSwitch(account, logs), account_balance=9500.0,
            accounts_dir=accounts, journal_dir=logs)
        assert result["status"] == "refused"
        assert result["stage"] == "kill_switch"
        assert paper.positions() == []


class TestCredentialsNeverLogged:
    def test_password_absent_from_journals_and_refs(self, tmp_path):
        accounts = write_account(tmp_path, "gina", mode="live",
                                 disclosure=True)
        fake = FakeMT5()
        account = load_account("gina", accounts_dir=accounts)
        broker = MT5Broker(account, mt5_module=fake)
        result = execute_order(
            good_proposal(), "gina", broker, make_rm(tmp_path),
            AccountKillSwitch(account, paper_journal_dir(tmp_path)),
            account_balance=10000.0, accounts_dir=accounts,
            journal_dir=paper_journal_dir(tmp_path))
        assert result["status"] == "filled"
        assert fake.login_args == ("12345", "sekret", "DemoServer")
        # scan every journal file for the password
        for name in os.listdir(paper_journal_dir(tmp_path)):
            content = open(os.path.join(paper_journal_dir(tmp_path),
                                       name)).read()
            assert "sekret" not in content
