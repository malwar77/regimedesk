"""Live-trading readiness: risk disclosure + account audit.

This module is the human's pre-live checklist. It NEVER switches an
account to live — `mode`, `risk_disclosure_accepted`,
`risk_disclosure_accepted_at` and `auto_trade` are protected fields
that only a human editing the account YAML can set. Here we only:
  1. show the full risk disclosure, and
  2. audit whether an account would currently pass every live gate.

Risk lives in code, never in prompts: nothing in this file can relax
the executor/RiskManager/kill-switch gates.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RISK_DISCLOSURE: Tuple[str, ...] = (
    "LIVE TRADING RISK DISCLOSURE",
    "",
    "You are about to enable REAL-MONEY trading. Read this fully:",
    "",
    "1. You can lose money — including your entire deposited capital.",
    "   Past backtest or paper results do NOT predict live performance.",
    "2. Leverage magnifies losses as much as gains. A small adverse",
    "   move can wipe out margin and trigger forced liquidation.",
    "3. Slippage, gaps, spreads and weekend/overnight swaps make live",
    "   fills worse than paper fills. Stops do NOT guarantee an exit",
    "   price — the market can gap through them.",
    "4. Brokers, VPS, network or power outages can leave positions",
    "   unmanaged at any time, including while you sleep.",
    "5. The drawdown kill switch limits daily/weekly damage but cannot",
    "   prevent losses entirely, and may itself execute at bad prices.",
    "6. The LLM component is ADVISORY ONLY. It cannot create, resize",
    "   or approve trades. Risk decisions live in code (RiskManager",
    "   + kill switch), which can veto but never guarantee profit.",
    "7. You are solely responsible for legal/tax compliance in your",
    "   jurisdiction and for your broker account's terms of use.",
    "",
    "If you understand and accept ALL of the above, you enable live",
    "mode by editing the account YAML BY HAND — no command, agent or",
    "automation will do it for you (see `python main.py go-live`).",
)


class LiveReadiness:
    """Audit one account against every live gate. Read-only."""

    # a live gate only passes with real DEMO evidence behind it:
    # at least PAPER_MIN_ORDERS paper orders spanning at least
    # PAPER_MIN_DAYS days. Young accounts have no track record —
    # the audit refuses to bless a live switch without one.
    PAPER_MIN_ORDERS = 30
    PAPER_MIN_DAYS = 30.0

    def __init__(self, account, journal_dir: str = "logs") -> None:
        self.account = account
        self.journal_dir = journal_dir

    def _paper_history(self) -> Tuple[int, float]:
        try:
            from .kill_switch import Journal
            from .seasons import paper_track_record
            entries = Journal(self.account.user_id,
                              self.journal_dir).entries()
            return paper_track_record(entries)
        except Exception:
            return 0, 0.0

    def checks(self) -> List[Tuple[str, bool, str]]:
        """Ordered list of (name, passed, human detail)."""
        acc = self.account
        out: List[Tuple[str, bool, str]] = []

        out.append((
            "mode is live",
            acc.mode == "live",
            "currently %r — set `mode: live` by hand in the account "
            "YAML to go live" % acc.mode))

        if acc.mode == "live":
            # only demand DEMO evidence when the live gate is actually
            # being attempted
            n, days = self._paper_history()
            out.append((
                "demo track record (>= %d paper orders over >= %.0f days)"
                % (self.PAPER_MIN_ORDERS, self.PAPER_MIN_DAYS),
                n >= self.PAPER_MIN_ORDERS and days >= self.PAPER_MIN_DAYS,
                "%d paper orders spanning %.1f days — run the account in "
                "paper mode and build a real DEMO season first"
                % (n, days)))

        out.append((
            "risk disclosure accepted",
            bool(acc.risk_disclosure_accepted),
            "set `risk_disclosure_accepted: true` by hand, only after "
            "reading the disclosure"))

        out.append((
            "disclosure timestamp recorded",
            bool(getattr(acc, "risk_disclosure_accepted_at", None)),
            "set `risk_disclosure_accepted_at: <date>` when you accept"))

        daily = float(acc.daily_drawdown_limit_pct)
        weekly = float(acc.weekly_drawdown_limit_pct)
        out.append((
            "drawdown kill-switch limits sane (0 < daily <= weekly)",
            0 < daily <= weekly,
            "daily %.2f%% / weekly %.2f%%" % (daily, weekly)))

        auto = (acc.extra or {}).get("auto_trade", "off")
        out.append((
            "auto_trade valid (off|paper|live)",
            auto in ("off", "paper", "live"),
            "currently %r — live auto-trading additionally requires "
            "auto_trade: live in the YAML" % auto))

        missing = [k for k in ("mt5_login", "mt5_server")
                   if not getattr(acc, k, None)]
        out.append((
            "MT5 credentials present",
            not missing,
            "missing: %s" % ", ".join(missing) if missing else
            "login + server configured"))

        return out

    @property
    def ready(self) -> bool:
        return all(ok for _, ok, _ in self.checks())

    def report(self) -> Dict[str, Any]:
        checks = self.checks()
        return {
            "ready": all(ok for _, ok, _ in checks),
            "account": self.account.user_id,
            "checks": [{"name": n, "ok": ok, "detail": d}
                       for n, ok, d in checks],
        }


def print_disclosure() -> None:                   # pragma: no cover
    for line in RISK_DISCLOSURE:
        print(line)
