"""Auto-trader — runs the deterministic pipeline on a schedule and routes
proposals through the UNCHANGED gate chain.

Per-account auto_trade setting (in the account YAML, default off, protected
like mode/risk-disclosure — never settable programmatically):
- auto_trade: off    -> signals are generated, LLM-annotated and logged only
- auto_trade: paper  -> approved proposals execute on the PAPER broker
- auto_trade: live   -> approved proposals execute on the MT5 adapter, and
  ONLY when the account also has mode: live + risk_disclosure_accepted:
  true (the executor + adapter enforce this independently)

The LLM brain is advisory with negative-only power: "against" skips
execution of that signal. It can never create or enlarge a trade. Risk
authority stays with risk_manager.py; the drawdown kill switch stays
per-account. Nothing here relaxes any existing gate.
"""
from .config_loader import build_llm_brain, load_account
from .kill_switch import AccountKillSwitch
from .signal_engine import generate, log_signal
from .executor import execute_order
from .risk_manager import RiskManager

ALLOWED_AUTO_TRADE = ("off", "paper", "live")


class AutoTrader:
    def __init__(self, user_id, accounts_dir="config/accounts",
                 journal_dir="logs", risk_manager=None, llm=None,
                 broker_factory=None, accounts_balance=10000.0):
        self.user_id = user_id
        self.accounts_dir = accounts_dir
        self.journal_dir = journal_dir
        self.account = load_account(user_id, accounts_dir=accounts_dir)
        auto = self.account.extra.get("auto_trade", "off")
        if auto not in ALLOWED_AUTO_TRADE:
            raise ValueError("auto_trade must be one of %s (got %r)"
                              % (ALLOWED_AUTO_TRADE, auto))
        self.auto_trade = auto
        self.risk_manager = risk_manager or RiskManager()
        self.llm = llm or build_llm_brain(self.account)
        self.kill_switch = AccountKillSwitch(self.account, journal_dir)
        self.balance = accounts_balance
        self._broker_factory = broker_factory or self._default_broker

    def _default_broker(self, account):
        if account.mode == "live":
            from brokers.mt5_adapter import MT5Broker
            from .kill_switch import Journal
            return MT5Broker(account, journal=Journal(self.user_id,
                                                      self.journal_dir))
        from brokers.oanda_paper import OandaPaperBroker
        return OandaPaperBroker()

    @property
    def execution_allowed(self):
        if self.auto_trade == "off":
            return False
        if self.auto_trade == "paper":
            return True  # paper broker chosen via account mode (paper)
        # live auto-trading additionally requires the human opt-ins
        return (self.account.mode == "live"
                and self.account.risk_disclosure_accepted)

    def step(self, instrument, bars):
        """One evaluation cycle for one instrument. Returns a result dict.
        The LLM can only veto ('against' -> skip execution), never create."""
        proposal = generate(instrument, bars, account_balance=self.balance)
        if proposal is None:
            return {"status": "no_signal", "instrument": instrument}

        annotation = self.llm.annotate(proposal)
        proposal["llm_annotation"] = annotation

        if not self.execution_allowed:
            log_signal(proposal, log_dir=self.journal_dir)  # logged ALWAYS
            return {"status": "logged_only", "instrument": instrument,
                    "llm_agreement": annotation["agreement"]}
        # LLM VERDICT EXECUTION SEMANTICS (resolved, not just logged):
        # - "against"    -> SKIP this signal (advisory veto). The ONLY
        #                   verdict with any functional effect.
        # - "questions"  -> purely descriptive; execution proceeds exactly
        #                   as it would have — no change to proceeding,
        #                   sizing, ranking, or anything else.
        # - "supports"   -> purely descriptive; same as "questions".
        if annotation["agreement"] == "against":
            # the LLM's only power: skip this signal, with the reason logged
            log_signal(proposal, log_dir=self.journal_dir)
            return {"status": "llm_advisory_veto", "instrument": instrument,
                    "llm_agreement": "against"}

        broker = self._broker_factory(self.account)
        result = execute_order(proposal, self.user_id, broker,
                               self.risk_manager, self.kill_switch,
                               account_balance=self.balance,
                               accounts_dir=self.accounts_dir,
                               journal_dir=self.journal_dir)
        result["llm_agreement"] = annotation["agreement"]
        if result.get("status") == "filled":
            proposal["executed"] = True  # the log reflects the real outcome
        log_signal(proposal, log_dir=self.journal_dir)  # logged ALWAYS
        return result

    def run(self, pipeline, instruments, timeframe="H1", count=300,
            max_cycles=None, sleep_seconds=3600):
        """Iterate the pipeline. With live feeds this polls every
        sleep_seconds; with recorded/synthetic data it runs once per
        instrument unless max_cycles is set."""
        import time
        cycles = 0
        results = []
        while True:
            for instrument in instruments:
                bars = pipeline.fetch(instrument, timeframe, count=count)
                results.append(self.step(instrument, bars))
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return results
            if not pipeline.live:
                return results
            time.sleep(sleep_seconds)
