"""Morning status reporting — builds a snapshot of this account's
state and optionally POSTs it to the Superagent ingest endpoint so
the agent can send the morning WhatsApp report.

Facts only, from the journal and account config:
- mode (paper|live) and balance (last recorded equity_after)
- today's open positions (journal orders placed today)
- kill-switch distance: daily/weekly drawdown used vs the limits
- whether the kill switch is currently blocking

This module NEVER places, approves or alters trades; it is read-only
reporting. The morning report is advisory; the RiskManager and the
kill switch on this host stay authoritative.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .config_loader import load_account
from .kill_switch import AccountKillSwitch, Journal

DEFAULT_POST_TIMEOUT_SECONDS = 10


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def build_status(user_id: str, accounts_dir: str = "config/accounts",
                 journal_dir: str = "logs",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Build the status payload dict from the account + journal."""
    now = now or datetime.now(timezone.utc)
    account = load_account(user_id, accounts_dir=accounts_dir)
    journal = Journal(user_id, journal_dir)
    entries = journal.entries()
    orders = [e for e in entries if e.get("type") == "order"]

    today = now.date().isoformat()
    today_orders = [e for e in orders if str(e.get("ts", ""))[:10] == today]

    balance: Optional[float] = None
    if orders:
        balance = orders[-1].get("equity_after")
        if isinstance(balance, (int, float)):
            balance = float(balance)

    # daily pnl: last equity today minus the equity before today's
    # first order (previous order's equity_after). Unknown -> None.
    daily_pnl: Optional[float] = None
    if today_orders and len(orders) > len(today_orders):
        prev_equity = orders[len(orders) - len(today_orders) - 1].get(
            "equity_after")
        last_equity = orders[-1].get("equity_after")
        if isinstance(prev_equity, (int, float)) and \
                isinstance(last_equity, (int, float)):
            daily_pnl = float(last_equity) - float(prev_equity)

    ks_check = AccountKillSwitch(account, journal_dir).check(
        balance if balance is not None else 0.0, now=now)

    def used(pct: Any) -> float:
        """Drawdown used toward the limit (0 when in profit)."""
        try:
            return round(max(0.0, -float(pct)), 2)
        except (TypeError, ValueError):
            return 0.0

    return {
        "project": "regimedesk",
        "account": user_id,
        "mode": account.mode,
        "generated_at": now.isoformat(),
        "balance": balance,
        "daily_pnl": daily_pnl,
        "open_positions": [
            {"symbol": o.get("instrument"), "side": o.get("side"),
             "size": o.get("size"), "entry": o.get("price"),
             "stop": None, "unrealized_pnl": None}
            for o in today_orders
        ],
        "kill_switch": {
            "daily_used_pct": used(ks_check["daily_drawdown_pct"]),
            "weekly_used_pct": used(ks_check["weekly_drawdown_pct"]),
            "blocked": bool(ks_check["blocked"]),
        },
        "host": os.environ.get("HOSTNAME") or os.uname().nodename,
        "notes": "kill switch limits: daily %.2f%% weekly %.2f%%" % (
            account.daily_drawdown_limit_pct,
            account.weekly_drawdown_limit_pct),
    }


def post_status(payload: Dict[str, Any], post_url: str, token: str,
                timeout: int = DEFAULT_POST_TIMEOUT_SECONDS
                ) -> Tuple[bool, int, str]:
    """POST the status payload. Returns (ok, http_status, body)."""
    body = json.dumps(dict(payload, token=token)).encode("utf-8")
    req = urllib.request.Request(
        post_url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (200 <= resp.status < 300, resp.status,
                    resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return (False, e.code, e.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        return (False, 0, str(e))
