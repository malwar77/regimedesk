"""Tests: live-trading readiness + the live-order risk warning.

Hand-verified expectations. Nothing here may ever switch an account
to live — the audit is read-only by construction and the executor
warning is informational only (it must not block or alter orders)."""
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.config_loader import AccountConfig, load_account
from core.live_readiness import LiveReadiness, RISK_DISCLOSURE


def make_account(**kw):
    base = dict(user_id="u", broker="mt5", mode="paper",
                risk_disclosure_accepted=False,
                risk_disclosure_accepted_at=None,
                daily_drawdown_limit_pct=3.0,
                weekly_drawdown_limit_pct=6.0,
                mt5_login="12345", mt5_server="Deriv-Demo",
                mt5_password="pw", extra={})
    base.update(kw)
    return AccountConfig(**base)


def write_account_yaml(tmp_path, user_id, mode="paper", disclosure=False):
    """Real account YAML on disk — the executor re-reads the account
    file from disk for gating (anti-tamper), so tests must write it."""
    d = tmp_path / "accounts"
    d.mkdir(exist_ok=True)
    lines = ["user_id: %s" % user_id, "mode: %s" % mode,
             "risk_disclosure_accepted: %s" % ("true" if disclosure else "false"),
             "risk_disclosure_accepted_at: %s"
             % ("2026-09-13T00:00:00+00:00" if disclosure else "null"),
             "daily_drawdown_limit_pct: 3.0",
             "weekly_drawdown_limit_pct: 6.0",
             "mt5_login: \"12345\"", "mt5_password: \"sekret\"",
             "mt5_server: \"DemoServer\""]
    (d / ("%s.yaml" % user_id)).write_text("\n".join(lines) + "\n")
    return str(d)


def test_paper_account_is_not_ready():
    lr = LiveReadiness(make_account())
    assert lr.ready is False
    names = [n for n, _, _ in lr.checks()]
    assert "mode is live" in names
    assert "risk disclosure accepted" in names


def test_fully_live_account_is_ready():
    lr = LiveReadiness(make_account(mode="live",
                                    risk_disclosure_accepted=True,
                                    risk_disclosure_accepted_at="2026-09-13"))
    assert lr.ready is True
    rep = lr.report()
    assert rep["ready"] is True
    assert all(c["ok"] for c in rep["checks"])


def test_missing_timestamp_fails():
    acc = make_account(mode="live", risk_disclosure_accepted=True,
                       risk_disclosure_accepted_at=None)
    checks = {n: ok for n, ok, _ in LiveReadiness(acc).checks()}
    assert checks["disclosure timestamp recorded"] is False


def test_inverted_drawdown_limits_fail():
    # daily limit LARGER than weekly is insane — must fail
    acc = make_account(daily_drawdown_limit_pct=10.0,
                       weekly_drawdown_limit_pct=5.0)
    checks = {n: ok for n, ok, _ in LiveReadiness(acc).checks()}
    assert checks["drawdown kill-switch limits sane (0 < daily <= weekly)"] is False


def test_missing_mt5_credentials_fail():
    acc = make_account(mt5_login=None, mt5_server=None)
    checks = {n: ok for n, ok, _ in LiveReadiness(acc).checks()}
    assert checks["MT5 credentials present"] is False


def test_disclosure_covers_the_hard_truths():
    text = " ".join(RISK_DISCLOSURE).lower()
    # the words a real disclosure must contain
    for word in ("lose", "leverage", "slippage", "outage", "veto",
                 "advisory only", "by hand"):
        assert word in text, "disclosure missing %r" % word


def test_executor_live_order_prints_and_journals_warning(
        tmp_path, capsys):
    from core.executor import execute_order
    from core.kill_switch import AccountKillSwitch, Journal
    from core.risk_manager import RiskManager
    from brokers.oanda_paper import OandaPaperBroker

    logs = tmp_path / "logs"
    logs.mkdir()
    accounts = write_account_yaml(tmp_path, "u", mode="live",
                                 disclosure=True)
    # gate 4 refuses a paper broker for a live account — use a
    # live-stub broker with paper=False to reach the warning
    class LiveStub(OandaPaperBroker):
        paper = False

    proposal = {
        "signal_id": "sig-live-warn", "instrument": "EUR_USD",
        "direction": "long", "entry_price": 1.1000,
        "suggested_stop_loss": 1.0950,
        "suggested_take_profit": 1.1100,
        "suggested_position_size": 0.2, "risk_amount": 10.0,
    }
    result = execute_order(proposal, "u", LiveStub(), RiskManager(),
                           AccountKillSwitch(make_account(mode="live",
                                                          risk_disclosure_accepted=True,
                                                          risk_disclosure_accepted_at="2026-09-13"),
                                             str(logs)),
                           account_balance=10000.0,
                           accounts_dir=accounts,
                           journal_dir=str(logs))
    assert result["status"] == "filled"
    out = capsys.readouterr().out
    assert "LIVE ORDER" in out and "REAL MONEY" in out
    j = Journal("u", str(logs))
    types = [e["type"] for e in j.entries()]
    assert "live_risk_warning" in types


def test_paper_order_prints_no_live_warning(tmp_path, capsys):
    from core.executor import execute_order
    from core.kill_switch import AccountKillSwitch
    from core.risk_manager import RiskManager
    from brokers.oanda_paper import OandaPaperBroker

    logs = tmp_path / "logs"
    logs.mkdir()
    accounts = write_account_yaml(tmp_path, "u", mode="paper",
                                 disclosure=False)
    proposal = {
        "signal_id": "sig-paper-quiet", "instrument": "EUR_USD",
        "direction": "long", "entry_price": 1.1000,
        "suggested_stop_loss": 1.0950,
        "suggested_take_profit": 1.1100,
        "suggested_position_size": 0.2, "risk_amount": 10.0,
    }
    result = execute_order(proposal, "u", OandaPaperBroker(), RiskManager(),
                           AccountKillSwitch(make_account(), str(logs)),
                           account_balance=10000.0,
                           accounts_dir=accounts,
                           journal_dir=str(logs))
    assert result["status"] == "filled"
    assert "REAL MONEY" not in capsys.readouterr().out
