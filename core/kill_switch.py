"""Per-account kill switch: blocks further LIVE orders for one account when
that account's own daily/weekly drawdown limit is breached.

Per-account by construction — the journal is one file per user
(logs/journal_{user_id}.jsonl) and blocking user A never touches user B.
Every refusal is logged with the reason.
"""
import json
import os
from datetime import datetime, timezone


def evaluate_drawdown(current_equity, day_start_equity, week_start_equity,
                      daily_limit_pct, weekly_limit_pct):
    """Pure function. Returns a dict of facts plus a blocked flag."""
    def pct(now, start):
        if not start or start <= 0:
            return 0.0
        return (now - start) / start * 100.0

    daily = pct(current_equity, day_start_equity)
    weekly = pct(current_equity, week_start_equity)
    blocked = daily <= -abs(daily_limit_pct) or weekly <= -abs(weekly_limit_pct)
    reasons = []
    if daily <= -abs(daily_limit_pct):
        reasons.append("daily drawdown %.2f%% breached limit %.2f%%"
                       % (daily, -daily_limit_pct))
    if weekly <= -abs(weekly_limit_pct):
        reasons.append("weekly drawdown %.2f%% breached limit %.2f%%"
                       % (weekly, -weekly_limit_pct))
    return {
        "blocked": blocked,
        "daily_drawdown_pct": daily,
        "weekly_drawdown_pct": weekly,
        "reasons": reasons,
    }


class Journal:
    """Append-only per-user journal: logs/journal_{user_id}.jsonl.

    Entry types:
    - {"type": "order", "ts", "mode", "instrument", "side", "size",
       "price", "risk_amount", "equity_after"}
    - {"type": "refusal", "ts", "stage", "reasons"}
    Credentials never appear here.
    """

    def __init__(self, user_id, journal_dir="logs"):
        self.user_id = user_id
        self.path = os.path.join(journal_dir, "journal_%s.jsonl" % user_id)
        os.makedirs(journal_dir, exist_ok=True)

    def append(self, entry):
        entry = dict(entry)
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        entry["user_id"] = self.user_id
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def entries(self):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


class AccountKillSwitch:
    """Checks one account's journal before every order."""

    def __init__(self, account, journal_dir="logs"):
        self.account = account
        self.journal = Journal(account.user_id, journal_dir)

    def _bounds(self, now):
        """Equity at the start of the current UTC day and week, from the
        journal's equity marks. Falls back to current equity when no mark
        exists (nothing recorded -> nothing breached)."""
        entries = self.journal.entries()
        marks = [e for e in entries if e.get("type") == "order"
                 and "equity_after" in e]
        day0 = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        week0 = day0 - timedelta(days=now.weekday())
        day_start = week_start = None
        for e in marks:
            ts = datetime.fromisoformat(e["ts"])
            if ts >= day0 and day_start is None:
                day_start = e["equity_after"]
            if ts >= week0 and week_start is None:
                week_start = e["equity_after"]
        # earliest mark at/after the boundary is the baseline
        return day_start, week_start

    def check(self, current_equity, now=None):
        """Returns the drawdown evaluation dict; logs the block if breached.
        The caller MUST refuse the order when result['blocked'] is True."""
        now = now or datetime.now(timezone.utc)
        day_start, week_start = self._bounds(now)
        result = evaluate_drawdown(
            current_equity,
            day_start if day_start is not None else current_equity,
            week_start if week_start is not None else current_equity,
            self.account.daily_drawdown_limit_pct,
            self.account.weekly_drawdown_limit_pct,
        )
        if result["blocked"]:
            self.journal.append({
                "type": "refusal",
                "stage": "kill_switch",
                "reasons": result["reasons"],
            })
        return result


from datetime import timedelta  # noqa: E402  (used in _bounds)
