"""RegimeDesk CLI.

Commands:
  technical                 run the technical agent, write the daily brief
  signal --user ID          generate + log signal proposals (never executes)
  execute --user ID --signal-id ID   explicitly attempt execution of one
                            logged proposal (full gate chain: RiskManager
                            veto -> risk disclosure -> kill switch -> broker)
  status --user ID          read-only status view: regime, signals today,
                            orders, running P&L, drawdown vs limit

All displayed numbers are real recorded numbers, including losses. Nothing
in any output states or implies guaranteed or expected gains.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from agents.technical import run as run_technical
from core.config_loader import load_account
from core.data_pipeline import DataPipeline
from core.kill_switch import AccountKillSwitch, Journal
from core.risk_manager import RiskManager
from core.signal_engine import generate, log_signal, regime_snapshot


def _today():
    return datetime.now(timezone.utc)


def cmd_technical(args):
    pipeline = DataPipeline(live=False, source=args.source)
    path, _ = run_technical(pipeline=pipeline)
    print("technical brief written: %s" % path)


def cmd_signal(args):
    load_account(args.user)  # fails loudly if the account doesn't exist
    pipeline = DataPipeline(live=False, source=args.source)
    instruments = ["BTC_USD"]
    for instrument in instruments:
        bars = pipeline.fetch(instrument, "H1", count=300)
        proposal = generate(instrument, bars,
                            account_balance=args.balance)
        if proposal:
            log_signal(proposal)
            regime = {"regime": proposal["regime"]}
            from core.regime_engine import regime_snapshot_path
            regime_snapshot_path(regime)
            print("signal %s: %s %s (proposal only, NOT executed) "
                  "— see logs/live_signals_*.jsonl"
                  % (proposal["signal_id"], proposal["direction"],
                     instrument))
        else:
            print("%s: no trend signal under current rules" % instrument)


def cmd_execute(args):
    from core.executor import execute_order
    # find the logged proposal by id (never re-generated or edited)
    matches = []
    for name in sorted(os.listdir("logs")) if os.path.isdir("logs") else []:
        if name.startswith("live_signals_") and name.endswith(".jsonl"):
            with open(os.path.join("logs", name)) as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("signal_id") == args.signal_id:
                        matches.append(rec)
    if not matches:
        sys.exit("signal id %r not found in logs" % args.signal_id)
    proposal = matches[-1]
    account = load_account(args.user)
    if account.mode == "paper":
        from brokers.oanda_paper import OandaPaperBroker
        broker = OandaPaperBroker()
    else:
        from brokers.mt5_adapter import MT5Broker
        from core.kill_switch import Journal
        broker = MT5Broker(account, journal=Journal(args.user))
    risk_manager = RiskManager()
    kill_switch = AccountKillSwitch(account)
    result = execute_order(proposal, args.user, broker, risk_manager,
                           kill_switch, account_balance=args.balance)
    print(json.dumps(result, indent=2, default=str))
    if result.get("status") != "filled":
        sys.exit(1)


def cmd_doctor(args):
    """System check: Ollama reachability + pulled models, config files,
    MT5 library availability. Informational — exits nonzero on failures."""
    import os as _os
    import urllib.request
    from core.llm_brain import LLMBrain
    failures = []

    def report(name, ok, detail=""):
        print("[%s] %s%s" % ("ok" if ok else "FAIL", name,
                             (" — " + detail) if detail else ""))
        if not ok:
            failures.append(name)

    # 1. Ollama (the LLM brain's default backend — no API key needed)
    brain = LLMBrain()
    url = brain.ollama_url
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags",
                                    timeout=3) as resp:
            models = [m.get("name") for m in json.load(resp).get("models",
                                                                 [])]
        report("Ollama server at %s" % url, True,
               "%d model(s) pulled: %s" % (len(models),
                                          ", ".join(models) or "none"))
        wanted = brain.ollama_model
        have = [m for m in models if m == wanted or m.split(":")[0] == wanted]
        if have:
            report("model %r available" % wanted, True)
        else:
            report("model %r available" % wanted, False,
                   "run: ollama pull %s" % wanted)
    except Exception as exc:
        report("Ollama server at %s" % url, False,
               "unreachable (%s). Install ollama.com or set OLLAMA_URL. "
               "The LLM brain falls back to the deterministic local "
               "reasoner — nothing breaks, but there is no model "
               "reasoning." % exc)

    # 2. configuration files
    report("risk_limits.yaml", _os.path.exists("risk_limits.yaml"))
    report("watchlist.yaml", _os.path.exists("watchlist.yaml"))
    report("accounts directory", _os.path.isdir("config/accounts"))
    for f in sorted(_os.listdir("config/accounts")) if \
            _os.path.isdir("config/accounts") else []:
        if f.endswith(".yaml") and not f.endswith(".yaml.example"):
            # parse properly — never substring-match config files (comments lie)
            from core.config_loader import AccountConfigError
            try:
                acct = load_account(f[:-5], accounts_dir="config/accounts")
                print("      account %s: mode=%s auto_trade=%s "
                      "disclosure_accepted=%s"
                      % (f, acct.mode,
                         acct.extra.get("auto_trade", "off"),
                         acct.risk_disclosure_accepted))
                # credential status — presence only, values never shown
                def cred_state(v):
                    if v:
                        return "set (%d chars)" % len(str(v))
                    return "MISSING"
                print("        credentials: mt5_login=%s mt5_password=%s "
                      "mt5_server=%s (file or MT5_* env; never logged)"
                      % (cred_state(acct.mt5_login),
                         cred_state(acct.mt5_password),
                         cred_state(acct.mt5_server)))
                try:
                    from core.config_loader import llm_settings
                    settings = llm_settings(acct)
                    print("        llm: provider=%s model=%s url=%s "
                          "(advisory only, negative-only power)"
                          % (settings["provider"], settings["model"],
                             settings["url"]))
                except AccountConfigError as exc:
                    report("account llm config %s" % f, False, str(exc))
            except AccountConfigError as exc:
                report("account config %s" % f, False, str(exc))

    # 3. MT5 (only needed for live execution) — read-only terminal probe
    from brokers.mt5_adapter import probe_terminal
    ok, detail = probe_terminal()
    report("MT5 terminal", ok, detail + (" (live execution additionally "
           "requires mode: live + risk_disclosure_accepted: true in the "
           "account file)" if ok else ""))

    print("\n%s" % ("all checks passed" if not failures
                     else "failures: %s" % ", ".join(failures)))
    sys.exit(1 if failures else 0)


def cmd_chief(args):
    """Write the chief-of-staff daily brief (ranked signals + regime
    summary + clearly-labeled, non-authoritative LLM commentary)."""
    from agents.chief_of_staff import run
    from core.data_pipeline import DataPipeline
    pipeline = DataPipeline(live=False, source=args.source, path=args.path)
    path, data = run(pipeline=pipeline)
    print("chief-of-staff brief written: %s" % path)
    for p in data["ranked"]:
        ann = data["annotations"].get(p["signal_id"], {})
        print("  [%s] %s %s (deterministic) — LLM verdict: %s"
              % ("HIGH" if p["combined_strength"] >= 0.7 else "MEDIUM",
                 p["instrument"], p["direction"], ann.get("agreement", "-")))


def cmd_auto(args):
    """Run the auto-trader explicitly (LLM advisory layer included).
    Execution only happens if the account's auto_trade flag allows it."""
    from core.auto_trader import AutoTrader
    from core.data_pipeline import DataPipeline
    trader = AutoTrader(args.user)
    pipeline = DataPipeline(live=args.live, source=args.source,
                            path=args.path)
    instruments = ["BTC_USD"]
    results = trader.run(pipeline, instruments, max_cycles=1)
    for r in results:
        r.pop("reasons", None)
        print(json.dumps(r, default=str))
    print("auto_trade setting: %s | LLM annotations are advisory only — "
          "never a trade trigger" % trader.auto_trade)


def cmd_status(args):
    load_account(args.user)
    today = _today().strftime("%Y%m%d")
    print("=== RegimeDesk status — user %s (%s) ===" % (args.user, _today().date()))
    regime_path = "logs/regime_latest.json"
    if os.path.exists(regime_path):
        with open(regime_path) as f:
            snap = json.load(f)
        print("current regime: %s (as of %s)"
              % (snap.get("regime"), snap.get("timestamp", "?")))
    else:
        print("current regime: no data recorded yet")
    sig_path = os.path.join("logs", "live_signals_%s.jsonl" % today)
    if os.path.exists(sig_path):
        with open(sig_path) as f:
            signals = [json.loads(l) for l in f if l.strip()]
        print("live signals generated today: %d" % len(signals))
        for s in signals[-5:]:
            print("  - %s %s %s (combined %.3f, %s)"
                  % (s.get("timestamp", "?")[:19],
                     s.get("direction"), s.get("instrument"),
                     s.get("combined_strength", 0),
                     "executed" if s.get("executed") else "not executed"))
    else:
        print("live signals generated today: 0")
    journal = Journal(args.user)
    entries = journal.entries()
    orders = [e for e in entries if e.get("type") == "order"]
    today_orders = [e for e in orders
                    if e.get("ts", "")[:10] == _today().date().isoformat()]
    print("orders placed (all time): %d | today: %d"
          % (len(orders), len(today_orders)))
    if orders:
        equity = orders[-1].get("equity_after", float("nan"))
        print("running P&L (recorded equity): %s"
              % json.dumps(equity))
        ks = AccountKillSwitch(load_account(args.user))
        check = ks.check(equity)
        print("drawdown today: %.2f%% vs limit %.2f%% | this week: %.2f%% vs "
              "limit %.2f%%"
              % (check["daily_drawdown_pct"],
                 ks.account.daily_drawdown_limit_pct,
                 check["weekly_drawdown_pct"],
                 ks.account.weekly_drawdown_limit_pct))
        if check["blocked"]:
            print("KILL SWITCH: further live orders for this account are "
                  "blocked — %s" % "; ".join(check["reasons"]))
    else:
        print("running P&L: no orders recorded yet")
    print("mode: recorded facts only — real numbers, including losses; "
          "no gain is guaranteed or implied.")

def cmd_research(args):
    """Run the overnight research swarm (ported from the standalone
    RegimeDesk build): macro/news, regime, on-chain flow, sentiment,
    technical, then Chief of Staff ranking. Research facts only —
    nothing here is a trade proposal or execution."""
    from research.nightly import run_nightly_research
    results = run_nightly_research(os.path.dirname(
        os.path.abspath(__file__)) if False else ".")
    print("research swarm complete:")
    for agent in ("macro", "regime", "onchain", "sentiment",
                  "technical", "chief"):
        print("  %s: %s" % (agent, "ran" if agent in results else "MISSING"))
    print("briefs: briefs/ | machine state: state/")
    print("research output is facts, not trade recommendations")


def cmd_dashboard(args):
    """Launch the Streamlit research dashboard (optional: pip install
    streamlit). Read-only view over briefs/ and state/ — research
    facts only, never a trading surface."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print("streamlit is not installed: pip install streamlit")
        print("then: streamlit run research/dashboard/app.py")
        return 1
    import subprocess, sys
    return subprocess.call([
        sys.executable, "-m", "streamlit", "run",
        "research/dashboard/app.py"])


def cmd_report(args):
    """Build a status snapshot for this account and optionally POST it
    to the Superagent ingest endpoint (morning WhatsApp report).
    Read-only: never places, approves or alters trades. Without
    --post-url (or env STATUS_URL) it just prints the payload."""
    import json as _json
    from core.reporting import build_status, send_beacon
    payload = build_status(args.account)
    print(_json.dumps(payload, indent=2, default=str))
    if not args.api_base:
        print("no --api-base / AGENT_API_BASE set: payload printed only")
        return 0
    if not args.api_key:
        print("no --api-key / AGENT_API_KEY set: refusing to send")
        return 1
    ok, detail = send_beacon(payload, args.api_base, args.api_key)
    print("beacon %s: %s" % ("sent" if ok else "FAILED", detail))
    return 0 if ok else 1


def cmd_go_live(args):
    """Live-trading readiness wizard. Shows the full risk disclosure
    and audits every live gate for the account. It can NEVER switch
    an account to live: mode / risk_disclosure_accepted / auto_trade
    are protected fields only a human can set by editing the YAML.
    Re-run any time to re-audit an account."""
    import sys
    from core.config_loader import load_account
    from core.live_readiness import LiveReadiness, RISK_DISCLOSURE
    account = load_account(args.account)
    lr = LiveReadiness(account)
    print()
    for line in RISK_DISCLOSURE:
        print(line)
    print()
    print("live-readiness audit for account %r:" % args.account)
    for name, ok, detail in lr.checks():
        print("  [%s] %s — %s" % ("ok" if ok else "FAIL", name, detail))
    print()
    if lr.ready:
        print("ALL LIVE GATES PASS. Every live order will additionally")
        print("print and journal an explicit risk warning. Trade small:")
        print("the kill switch limits damage but does not prevent it.")
        return 0
    print("NOT READY: %d gate(s) above still fail."
          % sum(1 for _, ok, _ in lr.checks() if not ok))
    print("To go live, edit the account YAML BY HAND:")
    print("  mode: live")
    print("  risk_disclosure_accepted: true")
    print("  risk_disclosure_accepted_at: <today's date>")
    print("  auto_trade: live            # only if you want auto-execution")
    print("Then re-run: python main.py go-live --account %s" % args.account)
    print("No command, agent or automation will make these edits for you.")
    return 1


def cmd_backtest(args):
    """Historical replay of the deterministic engine with mandatory
    benchmark comparison. Read-only: touches no account, places nothing."""
    pipeline = DataPipeline(live=False, source=args.source,
                            path=args.data_path)
    bars = pipeline.fetch(args.instrument, args.timeframe,
                          count=args.count)
    closes = [b["close"] for b in bars]
    from core.backtest import signal_positions, compare_with_benchmarks
    positions = signal_positions(bars, args.instrument, args.timeframe)
    report = compare_with_benchmarks(closes, positions,
                                    cost_bps=args.cost_bps,
                                    label="%s %s (n=%d)" % (
                                        args.instrument, args.timeframe,
                                        len(bars)))
    print(report["summary"])
    strat = report["strategy"]
    print("  strategy: total %.2f%% | max drawdown %.2f%% | sharpe %.2f"
          % (strat["total_return"] * 100, strat["max_drawdown"] * 100,
             strat["sharpe"]))
    print("  engine fired on %d of %d slots (rest flat by rule)"
          % (sum(1 for p in positions if p != 0.0), len(positions)))
    print("past replay is a fact record, not a prediction or a promise; "
          "live results can and often do differ")


def main():
    p = argparse.ArgumentParser(prog="regimedesk")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("technical")
    t.add_argument("--source", default="synthetic")
    t.set_defaults(fn=cmd_technical)

    s = sub.add_parser("signal")
    s.add_argument("--user", required=True)
    s.add_argument("--source", default="synthetic")
    s.add_argument("--balance", type=float, default=10000.0)
    s.set_defaults(fn=cmd_signal)

    e = sub.add_parser("execute")
    e.add_argument("--user", required=True)
    e.add_argument("--signal-id", required=True)
    e.add_argument("--balance", type=float, default=10000.0)
    e.set_defaults(fn=cmd_execute)

    doc = sub.add_parser("doctor")
    doc.set_defaults(fn=cmd_doctor)

    ch = sub.add_parser("chief")
    ch.add_argument("--source", default="synthetic")
    ch.add_argument("--path", default=None)
    ch.set_defaults(fn=cmd_chief)

    a = sub.add_parser("auto")
    a.add_argument("--user", required=True)
    a.add_argument("--source", default="synthetic")
    a.add_argument("--path", default=None)
    a.add_argument("--live", action="store_true")
    a.set_defaults(fn=cmd_auto)

    st = sub.add_parser("status")
    st.add_argument("--user", required=True)
    st.set_defaults(fn=cmd_status)

    bt = sub.add_parser("backtest")
    bt.add_argument("--instrument", default="BTC_USD")
    bt.add_argument("--timeframe", default="H1")
    bt.add_argument("--source", default="synthetic",
                    choices=["synthetic", "recorded", "file"])
    bt.add_argument("--data-path", default=None)
    bt.add_argument("--count", type=int, default=500)
    bt.add_argument("--cost-bps", type=float, default=2.0)
    bt.set_defaults(fn=cmd_backtest)

    rs = sub.add_parser("research")
    rs.set_defaults(fn=cmd_research)

    db = sub.add_parser("dashboard")
    db.set_defaults(fn=cmd_dashboard)

    gl = sub.add_parser("go-live")
    gl.add_argument("--account", required=True)
    gl.set_defaults(fn=cmd_go_live)

    rp = sub.add_parser("report")
    rp.add_argument("--account", required=True)
    rp.add_argument("--api-base",
                    default=os.environ.get("AGENT_API_BASE", ""))
    rp.add_argument("--api-key",
                    default=os.environ.get("AGENT_API_KEY", ""))
    rp.set_defaults(fn=cmd_report)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
