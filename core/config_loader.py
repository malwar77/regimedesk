"""Account configuration loading.

SAFETY MODEL — this is where the paper/live boundary lives:

- Every account config (config/accounts/{user_id}.yaml) defaults to
  `mode: paper` when the file or field is missing.
- `mode: live` can ONLY be set by a human editing the file. There is no
  code path that changes mode or risk-disclosure programmatically —
  `load_account` refuses any overrides that touch these fields.
- Live mode additionally requires `risk_disclosure_accepted: true` with
  an `risk_disclosure_accepted_at` timestamp. The executor refuses any
  live order when this is false, regardless of other settings.

Credentials (mt5_login/mt5_password/mt5_server) are stored per-account,
read here, and never logged.
"""
import os
from dataclasses import dataclass, field


class AccountConfigError(Exception):
    pass


@dataclass
class AccountConfig:
    user_id: str
    broker: str = "mt5"
    mode: str = "paper"  # HARD DEFAULT: paper
    risk_disclosure_accepted: bool = False
    risk_disclosure_accepted_at: str = None
    daily_drawdown_limit_pct: float = 3.0
    weekly_drawdown_limit_pct: float = 6.0
    mt5_login: str = None
    mt5_password: str = None
    mt5_server: str = None
    extra: dict = field(default_factory=dict)  # includes auto_trade (off|paper|live)


# Fields that may NEVER be set programmatically, only by editing the file.
_PROTECTED_FIELDS = {"mode", "risk_disclosure_accepted",
                     "risk_disclosure_accepted_at", "auto_trade"}


def _parse_scalar(raw):
    v = raw.strip()
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        return v[1:-1]
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def parse_simple_yaml(text):
    """Parse the flat 'key: value' YAML subset used by RegimeDesk configs.
    Supports comments (#) and blank lines; no nested structures by design —
    account files are flat and explicit."""
    out = {}
    for line in text.splitlines():
        s = line.split("#", 1)[0].strip()
        if not s or ":" not in s:
            continue
        key, _, raw = s.partition(":")
        out[key.strip()] = _parse_scalar(raw)
    return out


def load_account(user_id, accounts_dir="config/accounts", overrides=None):
    """Load an account config. Returns AccountConfig.

    Refuses any `overrides` touching protected fields (mode, risk
    disclosure) — no code path, LLM prompt, or agent can flip a user to
    live. Only manual file editing can.
    """
    if overrides:
        bad = _PROTECTED_FIELDS.intersection(overrides)
        if bad:
            raise AccountConfigError(
                "refusing programmatic override of protected field(s): %s. "
                "mode/risk-disclosure can only be changed by editing the "
                "account file manually." % sorted(bad))
    path = os.path.join(accounts_dir, "%s.yaml" % user_id)
    data = {}
    if os.path.exists(path):
        with open(path) as f:
            data = parse_simple_yaml(f.read())
    else:
        raise AccountConfigError("no account config found at %s" % path)
    data.update(overrides or {})
    mode = data.get("mode", "paper")
    if mode not in ("paper", "live"):
        raise AccountConfigError("invalid mode %r (must be paper or live)" % mode)
    return AccountConfig(
        user_id=user_id,
        broker=str(data.get("broker", "mt5")),
        mode=mode,
        risk_disclosure_accepted=bool(data.get("risk_disclosure_accepted", False)),
        risk_disclosure_accepted_at=data.get("risk_disclosure_accepted_at"),
        daily_drawdown_limit_pct=float(data.get("daily_drawdown_limit_pct", 3.0)),
        weekly_drawdown_limit_pct=float(data.get("weekly_drawdown_limit_pct", 6.0)),
        mt5_login=data.get("mt5_login"),
        mt5_password=data.get("mt5_password"),
        mt5_server=data.get("mt5_server"),
        extra={k: v for k, v in data.items()
               if k not in {"broker", "mode", "risk_disclosure_accepted",
                            "risk_disclosure_accepted_at",
                            "daily_drawdown_limit_pct",
                            "weekly_drawdown_limit_pct",
                            "mt5_login", "mt5_password", "mt5_server"}},
    )
