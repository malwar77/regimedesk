"""The ONLY route from a signal proposal to any broker.

Execution order of gates (each one can refuse; every refusal is journaled):
1. RiskManager.veto(order)              — the unchanged risk veto
2. account config gate                  — live mode requires
                                          risk_disclosure_accepted: true
3. per-account kill switch              — drawdown limit breach blocks
                                          that account's live orders
4. broker/mode consistency              — paper accounts trade on paper
                                          brokers only; live accounts on
                                          the MT5 adapter only

No account is ever silently switched from paper to live: the mode lives in
the account YAML file, can only be edited by hand, and load_account refuses
programmatic overrides of it.
"""
from .config_loader import load_account
from .kill_switch import Journal


def execute_order(proposal, user_id, broker, risk_manager, kill_switch,
                  account_balance, accounts_dir="config/accounts",
                  journal_dir="logs", trades_today_fn=None):
    """Turn a signal proposal into an order and attempt execution.

    Returns {"status": "filled"|"refused", "stage": ..., "reasons": [...]}.
    Never raises for a refusal — refusals are facts, logged to the journal.
    """
    journal = Journal(user_id, journal_dir)

    order = {
        "user_id": user_id,
        "signal_id": proposal.get("signal_id"),
        "instrument": proposal["instrument"],
        "side": proposal["direction"],
        "size": proposal.get("suggested_position_size"),
        "entry_price": proposal.get("entry_price"),
        "stop_loss": proposal.get("suggested_stop_loss"),
        "risk_amount": proposal.get("risk_amount"),
        "account_balance": account_balance,
    }

    # Gate 1: the RiskManager veto (risk lives in code, never in prompts)
    trades_fn = trades_today_fn or (lambda: sum(
        1 for e in journal.entries()
        if e.get("type") == "order"))
    approved, reasons = risk_manager.veto(order, trades_today=trades_fn())
    if not approved:
        journal.append({"type": "refusal", "stage": "risk_manager",
                        "reasons": reasons})
        return {"status": "refused", "stage": "risk_manager", "reasons": reasons}

    # Gate 2: account config (paper/live + risk disclosure)
    account = load_account(user_id, accounts_dir=accounts_dir)
    if account.mode == "live" and not account.risk_disclosure_accepted:
        reasons = ["live mode requested but risk_disclosure_accepted is false; "
                   "live orders are refused regardless of other settings"]
        journal.append({"type": "refusal", "stage": "risk_disclosure",
                        "reasons": reasons})
        return {"status": "refused", "stage": "risk_disclosure", "reasons": reasons}

    # Gate 3: per-account kill switch
    ks = kill_switch.check(account_balance)
    if ks["blocked"]:
        return {"status": "refused", "stage": "kill_switch",
                "reasons": ks["reasons"]}

    # Gate 4: broker/mode consistency — no silent switching, ever
    broker_paper = bool(getattr(broker, "paper", True))
    if account.mode == "paper" and not broker_paper:
        reasons = ["account is in paper mode; refusing to route to a live broker"]
        journal.append({"type": "refusal", "stage": "mode_mismatch",
                        "reasons": reasons})
        return {"status": "refused", "stage": "mode_mismatch", "reasons": reasons}
    if account.mode == "live" and broker_paper:
        reasons = ["account is in live mode but a paper broker was supplied"]
        journal.append({"type": "refusal", "stage": "mode_mismatch",
                        "reasons": reasons})
        return {"status": "refused", "stage": "mode_mismatch", "reasons": reasons}

    result = broker.place_order(order)
    if result.get("status") == "filled":
        journal.append({
            "type": "order",
            "mode": account.mode,
            "instrument": order["instrument"],
            "side": order["side"],
            "size": order["size"],
            "price": result.get("price"),
            "risk_amount": order["risk_amount"],
            "equity_after": result.get("equity_after", account_balance),
            "signal_id": order["signal_id"],
        })
    return result
