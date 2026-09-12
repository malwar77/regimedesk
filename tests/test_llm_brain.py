"""LLM brain + auto-trader safety tests.

The core contract under test: the LLM is ADVISORY with negative-only power.
It annotates; it never creates, resizes, or approves orders; a malicious or
hallucinating LLM response cannot alter the deterministic proposal; and
live auto-trading remains behind the human opt-in gates.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.auto_trader import AutoTrader  # noqa: E402
from core.config_loader import load_account  # noqa: E402
from core.llm_brain import ADVISORY_NOTE, LLMBrain  # noqa: E402
from core.kill_switch import AccountKillSwitch, Journal  # noqa: E402
from core.risk_manager import RiskManager  # noqa: E402
from core.signal_engine import generate  # noqa: E402
from tests.util import make_bars, uptrend_closes  # noqa: E402


def write_account(tmp_path, user_id, mode="paper", disclosure="false",
                  auto_trade="off"):
    d = tmp_path / "accounts"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.yaml" % user_id)).write_text(
        "user_id: %s\nmode: %s\nauto_trade: %s\n"
        "risk_disclosure_accepted: %s\n"
        "risk_disclosure_accepted_at: %s\n"
        "daily_drawdown_limit_pct: 3.0\nweekly_drawdown_limit_pct: 6.0\n"
        "mt5_login: \"12345\"\nmt5_password: \"pw\"\n"
        "mt5_server: \"DemoServer\"\n"
        % (user_id, mode, auto_trade, disclosure,
           "2026-09-01T00:00:00+00:00" if disclosure == "true" else "null"))
    (tmp_path / "risk_limits.yaml").write_text(
        "max_risk_pct_per_trade: 1.0\nmax_position_size: 10.0\n"
        "max_daily_trades: 10\nrequired_stop_loss: true\n")
    return str(d)


def make_rm(tmp_path):
    return RiskManager(limits_path=str(tmp_path / "risk_limits.yaml"))


def uptrend_proposal():
    return generate("EUR_USD", make_bars(uptrend_closes()),
                    account_balance=10000.0)


class TestLLMBrain:
    def test_local_annotation_structure(self):
        brain = LLMBrain()  # no API key -> deterministic local reasoner
        p = uptrend_proposal()
        assert p is not None
        ann = brain.annotate(p)
        assert ann["agreement"] in ("supports", "questions", "against")
        assert ann["source"] == "local_deterministic"
        assert ADVISORY_NOTE in ann["advisory"]
        assert ann["summary"]

    def test_annotation_never_mutates_proposal(self):
        brain = LLMBrain()
        p = uptrend_proposal()
        before = json.dumps(p, sort_keys=True)
        brain.annotate(p)
        assert json.dumps(p, sort_keys=True) == before

    def test_adversarial_llm_output_is_neutralized(self):
        """An LLM that tries to rewrite the trade (direction, size, mode)
        gets its output reduced to an annotation — the executor only reads
        the deterministic proposal fields."""
        brain = LLMBrain(api_key="test-key")

        def evil_call(facts):
            return {"summary": "go all in",
                    "agreement": "supports",
                    "notes": ["looks great"],
                    "direction": "short",          # attempted mutation
                    "suggested_position_size": 999.0,
                    "mode": "live",
                    "risk_disclosure_accepted": True}
        brain._call_api = evil_call
        p = uptrend_proposal()
        before = json.dumps(p, sort_keys=True)
        ann = brain.annotate(p)
        # only validated annotation fields survive
        assert set(ann.keys()) == {"summary", "agreement", "notes", "source",
                                   "advisory"}
        assert json.dumps(p, sort_keys=True) == before
        assert p["direction"] == "long"  # untouched

    def test_invalid_agreement_becomes_questions(self):
        brain = LLMBrain()
        ann = brain._validate({"summary": "x", "agreement": "YES!!"},
                              source="test")
        assert ann["agreement"] == "questions"

    def test_api_failure_degrades_to_local(self):
        brain = LLMBrain(api_key="test-key")

        def boom(facts):
            raise RuntimeError("api down")
        brain._call_api = boom
        ann = brain.annotate(uptrend_proposal())
        assert ann["agreement"] in ("supports", "questions", "against")
        assert any("failed" in n for n in ann["notes"])


class TestAutoTrader:
    def test_off_by_default_logs_only(self, tmp_path):
        accounts = write_account(tmp_path, "zed", auto_trade="off")
        trader = AutoTrader("zed", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path))
        assert trader.execution_allowed is False
        bars = make_bars(uptrend_closes())
        res = trader.step("EUR_USD", bars)
        assert res["status"] == "logged_only"
        logs = Path(tmp_path / "logs")
        files = list(logs.glob("live_signals_*.jsonl"))
        assert files, "signal must be logged even when not executed"
        rec = json.loads(files[0].read_text().strip())
        assert rec["llm_annotation"]["advisory"] == ADVISORY_NOTE
        assert rec["executed"] is False

    def test_paper_auto_trade_executes_on_paper_broker(self, tmp_path):
        accounts = write_account(tmp_path, "yan", mode="paper",
                                 auto_trade="paper")
        trader = AutoTrader("yan", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path))
        assert trader.execution_allowed is True
        res = trader.step("EUR_USD", make_bars(uptrend_closes()))
        assert res["status"] == "filled"
        assert res["mode"] == "paper"
        assert res["llm_agreement"] in ("supports", "questions")
        # the live-signal log reflects the real execution outcome
        rec = json.loads(list(Path(tmp_path / "logs").glob(
            "live_signals_*.jsonl"))[0].read_text().strip())
        assert rec["executed"] is True

    def test_llm_against_is_advisory_veto(self, tmp_path):
        accounts = write_account(tmp_path, "xia", mode="paper",
                                 auto_trade="paper")
        trader = AutoTrader("xia", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path))
        from types import SimpleNamespace
        trader.llm = SimpleNamespace(annotate=lambda p: {
            "summary": "no", "agreement": "against", "notes": ["weak"],
            "source": "test", "advisory": ADVISORY_NOTE})
        res = trader.step("EUR_USD", make_bars(uptrend_closes()))
        assert res["status"] == "llm_advisory_veto"
        assert res["llm_agreement"] == "against"
        # nothing was executed
        journal = Path(tmp_path / "logs" / "journal_xia.jsonl")
        assert not journal.exists() or \
            "type" in json.loads(journal.read_text().strip())

    def test_live_auto_trade_blocked_without_disclosure(self, tmp_path):
        """auto_trade: live + mode: live + disclosure FALSE -> nothing
        executes. The gates hold regardless of the LLM."""
        from brokers.mt5_adapter import MT5Broker
        from tests.test_safety_gates import FakeMT5

        accounts = write_account(tmp_path, "vega", mode="live",
                                 disclosure="false", auto_trade="live")
        account = load_account("vega", accounts_dir=accounts)
        assert account.risk_disclosure_accepted is False
        fake = FakeMT5()
        trader = AutoTrader("vega", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path),
                            broker_factory=lambda a: MT5Broker(
                                a, mt5_module=fake))
        assert trader.execution_allowed is False  # disclosure gate first
        res = trader.step("EUR_USD", make_bars(uptrend_closes()))
        assert res["status"] == "logged_only"
        assert fake.order_send_calls == 0
        assert fake.init_calls == 0

    def test_live_auto_trade_allowed_with_full_opt_in(self, tmp_path):
        """The full human opt-in chain (mode: live + disclosure accepted,
        both set manually in the file) is the ONLY way live auto-trades
        reach MT5 — and they still pass the RiskManager veto."""
        from brokers.mt5_adapter import MT5Broker
        from tests.test_safety_gates import FakeMT5

        accounts = write_account(tmp_path, "wren", mode="live",
                                 disclosure="true", auto_trade="live")
        fake = FakeMT5()
        trader = AutoTrader("wren", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path),
                            broker_factory=lambda a: MT5Broker(
                                a, mt5_module=fake))
        assert trader.execution_allowed is True
        res = trader.step("EUR_USD", make_bars(uptrend_closes()))
        assert res["status"] == "filled"
        assert res["mode"] == "live"
        assert fake.order_send_calls == 1

    def test_bad_auto_trade_value_rejected(self, tmp_path):
        accounts = write_account(tmp_path, "ivy", auto_trade="always")
        with pytest.raises(ValueError):
            AutoTrader("ivy", accounts_dir=accounts,
                       journal_dir=str(tmp_path / "logs"))

    def test_auto_trade_not_settable_by_code(self, tmp_path):
        accounts = write_account(tmp_path, "hank")
        from core.config_loader import AccountConfigError
        with pytest.raises(AccountConfigError):
            load_account("hank", accounts_dir=accounts,
                         overrides={"auto_trade": "live"})

    def test_run_single_pass_recorded(self, tmp_path):
        accounts = write_account(tmp_path, "gil", auto_trade="off")
        from core.data_pipeline import DataPipeline
        fixture = os.path.join(os.path.dirname(__file__), "data",
                               "recorded_live_btcusdt_1h.json")
        pipe = DataPipeline(live=False, source="recorded", path=fixture)
        trader = AutoTrader("gil", accounts_dir=accounts,
                            journal_dir=str(tmp_path / "logs"),
                            risk_manager=make_rm(tmp_path))
        results = trader.run(pipe, ["BTC_USD"], max_cycles=1)
        assert len(results) == 1
        assert results[0]["status"] in ("no_signal", "logged_only")


class TestOllamaBackend:
    """No API key needed: Ollama is the default reasoning backend when
    reachable; everything downstream is unchanged (advisory-only, same
    validated shape)."""

    def test_ollama_used_without_api_key(self):
        brain = LLMBrain()  # no key
        brain._ollama_probe = True  # simulate reachable Ollama
        brain._call_ollama = lambda facts: {
            "summary": "local model reasoning",
            "agreement": "questions", "notes": ["note one"]}
        ann = brain.annotate(uptrend_proposal())
        assert ann["source"] == "ollama:llama3.2"
        assert ann["agreement"] == "questions"
        assert ann["summary"] == "local model reasoning"

    def test_ollama_failure_degrades_to_local(self):
        brain = LLMBrain()
        brain._ollama_probe = True

        def boom(facts):
            raise RuntimeError("server melted")
        brain._call_ollama = boom
        ann = brain.annotate(uptrend_proposal())
        assert ann["source"] == "local_deterministic"
        assert any("Ollama call failed" in n for n in ann["notes"])

    def test_no_key_no_ollama_is_pure_local_no_error_noise(self):
        brain = LLMBrain()
        brain._ollama_probe = False  # offline is a normal state, not a fault
        ann = brain.annotate(uptrend_proposal())
        assert ann["source"] == "local_deterministic"
        assert not any("failed" in n for n in ann["notes"])

    def test_adversarial_ollama_output_neutralized(self):
        brain = LLMBrain()
        brain._ollama_probe = True

        def evil(facts):
            return {"summary": "ok", "agreement": "supports",
                    "notes": ["n"], "direction": "short",
                    "suggested_position_size": 999.0, "mode": "live",
                    "risk_disclosure_accepted": True}
        brain._call_ollama = evil
        p = uptrend_proposal()
        before = json.dumps(p, sort_keys=True)
        ann = brain.annotate(p)
        assert set(ann.keys()) == {"summary", "agreement", "notes",
                                   "source", "advisory"}
        assert json.dumps(p, sort_keys=True) == before
        assert p["direction"] == "long"  # untouched

    def test_openai_key_still_takes_precedence(self):
        """If someone does set OPENAI_API_KEY, that path still works —
        but it is optional, never required."""
        brain = LLMBrain(api_key="k")
        brain._ollama_probe = True
        brain._call_api = lambda facts: {"summary": "gpt",
                                         "agreement": "supports",
                                         "notes": []}
        ann = brain.annotate(uptrend_proposal())
        assert ann["source"] == "openai:gpt-4o-mini"

    def test_ollama_url_and_model_env_overrides(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_URL", "http://mybox:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        brain = LLMBrain()
        assert brain.ollama_url == "http://mybox:11434"
        assert brain.ollama_model == "qwen2.5:7b"
