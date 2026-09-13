"""Tests: morning status reporting (build_status + post_status).

Hand-verified expectations. Reporting is read-only: it must never
place, approve or alter trades — these tests also pin that the
payload shape matches the ingest contract (project, account, mode,
generated_at required; kill_switch object; positions list)."""
import io
import json
import os
import sys
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.reporting as reporting
from core.kill_switch import Journal
from core.reporting import build_status, post_status


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def write_account(tmp_path, user_id="rep_user"):
    d = tmp_path / "accounts"
    d.mkdir(exist_ok=True)
    lines = ["user_id: %s" % user_id, "mode: paper",
             "risk_disclosure_accepted: false",
             "risk_disclosure_accepted_at: null",
             "daily_drawdown_limit_pct: 3.0",
             "weekly_drawdown_limit_pct: 6.0",
             "mt5_login: \"12345\"", "mt5_password: \"sekret\"",
             "mt5_server: \"DemoServer\""]
    (d / ("%s.yaml" % user_id)).write_text("\n".join(lines) + "\n")
    return str(d)


def add_order(journal, ts, equity_after, instrument="EUR_USD",
              side="long", size=0.2, price=1.1000):
    journal.append({"type": "order", "ts": ts, "mode": "paper",
                    "instrument": instrument, "side": side,
                    "size": size, "price": price, "risk_amount": 10.0,
                    "equity_after": equity_after, "signal_id": "t"})


NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_build_status_no_orders(tmp_path):
    accounts = write_account(tmp_path)
    logs = str(tmp_path / "logs")
    os.makedirs(logs, exist_ok=True)
    s = build_status("rep_user", accounts, logs, now=NOW)
    assert s["project"] == "regimedesk"
    assert s["mode"] == "paper"
    assert s["balance"] is None          # nothing traded: unknown
    assert s["daily_pnl"] is None
    assert s["open_positions"] == []
    assert s["kill_switch"]["blocked"] is False
    assert s["generated_at"] == NOW.isoformat()


def test_build_status_math_hand_verified(tmp_path):
    accounts = write_account(tmp_path)
    logs = str(tmp_path / "logs")
    os.makedirs(logs, exist_ok=True)
    j = Journal("rep_user", logs)
    # yesterday: equity ended at 10000 (also the day-before baseline)
    add_order(j, "2026-09-12T15:00:00+00:00", 10000.0)
    # today: two orders. Kill-switch baseline = FIRST mark of the day
    # (core semantics): 10000. Equity then drops to 9950.
    add_order(j, "2026-09-13T10:00:00+00:00", 10000.0,
              instrument="GBP_USD", side="long", size=0.1, price=1.2500)
    add_order(j, "2026-09-13T11:00:00+00:00", 9950.0,
              instrument="EUR_USD", side="long", size=0.2, price=1.1000)

    s = build_status("rep_user", accounts, logs, now=NOW)
    # balance = last recorded equity
    assert s["balance"] == 9950.0
    # daily pnl = last equity today - equity before today's first order
    assert s["daily_pnl"] == -50.0
    # two positions: today's orders
    assert len(s["open_positions"]) == 2
    p = s["open_positions"][0]
    assert p["symbol"] == "GBP_USD" and p["side"] == "long"
    assert p["entry"] == 1.2500
    # kill switch: (9950-10000)/10000 = -0.50% vs 3% daily limit
    # -> 0.5 used, not blocked. Hand-verified: 50/10000 = 0.5%.
    assert s["kill_switch"]["daily_used_pct"] == 0.5
    assert s["kill_switch"]["blocked"] is False


def test_kill_switch_used_clamps_profit_to_zero(tmp_path):
    accounts = write_account(tmp_path)
    logs = str(tmp_path / "logs")
    os.makedirs(logs, exist_ok=True)
    j = Journal("rep_user", logs)
    add_order(j, "2026-09-12T15:00:00+00:00", 10000.0)
    add_order(j, "2026-09-13T10:00:00+00:00", 10000.0)
    add_order(j, "2026-09-13T11:00:00+00:00", 10500.0)  # +5%: in profit
    s = build_status("rep_user", accounts, logs, now=NOW)
    assert s["kill_switch"]["daily_used_pct"] == 0.0
    assert s["daily_pnl"] == 500.0


def test_kill_switch_blocked_when_limit_breached(tmp_path):
    accounts = write_account(tmp_path)
    logs = str(tmp_path / "logs")
    os.makedirs(logs, exist_ok=True)
    j = Journal("rep_user", logs)
    add_order(j, "2026-09-12T15:00:00+00:00", 10000.0)
    add_order(j, "2026-09-13T10:00:00+00:00", 10000.0)
    add_order(j, "2026-09-13T11:00:00+00:00", 9600.0)  # -4% < -3% limit
    s = build_status("rep_user", accounts, logs, now=NOW)
    assert s["kill_switch"]["daily_used_pct"] == 4.0
    assert s["kill_switch"]["blocked"] is True


def test_post_status_round_trip(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return FakeResponse(200, '{"ok": true}')

    monkeypatch.setattr(reporting.urllib.request, "urlopen", fake_urlopen)
    payload = {"project": "regimedesk", "account": "x",
               "mode": "paper", "generated_at": NOW.isoformat()}
    ok, code, body = post_status(payload, "https://example.invalid/ingest",
                                 "sekrit-token")
    assert ok is True and code == 200
    assert json.loads(body)["ok"] is True
    assert captured["url"] == "https://example.invalid/ingest"
    # the token rides in the body, added by post_status
    assert captured["data"]["token"] == "sekrit-token"
    assert captured["data"]["project"] == "regimedesk"
    assert captured["data"]["account"] == "x"


def test_post_status_http_error_reported(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"ok": false, "error": "unauthorized"}'))
    monkeypatch.setattr(reporting.urllib.request, "urlopen", fake_urlopen)
    ok, code, body = post_status({"x": 1}, "https://x/y", "bad")
    assert ok is False and code == 401
    assert "unauthorized" in body


def test_post_status_unreachable_host(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise OSError("connection refused")
    monkeypatch.setattr(reporting.urllib.request, "urlopen", fake_urlopen)
    ok, code, body = post_status({"x": 1}, "https://x/y", "t", timeout=2)
    assert ok is False and code == 0
    assert "connection refused" in body
