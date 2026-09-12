"""Chief-of-staff brief + LLM commentary layer tests.

Contract under test:
- LLM commentary is appended AFTER ranking is finalized and can never
  influence ranking, HIGH/MEDIUM labels, or ordering.
- "questions"/"supports" are purely descriptive: they never change whether
  a signal proceeds, its sizing, or its ranking. Only "against" does
  anything (skips the signal) — the negative-only design.
- Without OPENAI_API_KEY the local reasoner is conservative on ambiguity
  (prefers "questions" over "supports").
- Commentary is display-only: no annotation field can alter stop_loss,
  take_profit, risk, position size, or mode, and non-whitelisted annotation
  keys are never rendered.
"""
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.chief_of_staff import (HEADER_EXECUTION_NOTE, annotate_ranked,
                                   conviction_label, rank_signals,
                                   write_brief)
from core.llm_brain import ADVISORY_NOTE, LLMBrain  # noqa: E402
from core.signal_engine import generate  # noqa: E402
from tests.util import make_bars, uptrend_closes  # noqa: E402


def real_proposal():
    p = generate("EUR_USD", make_bars(uptrend_closes()),
                 account_balance=10000.0)
    assert p is not None
    return p


def stub_brain(verdict, summary="stub", extra=None):
    class Stub:
        def annotate(self, proposal):
            ann = {"summary": summary, "agreement": verdict,
                   "notes": ["stub note"], "source": "test",
                   "advisory": ADVISORY_NOTE}
            if extra:
                ann.update(extra)
            return ann
    return Stub()


class TestRankingUnaffectedByLLM:
    def test_labels_are_deterministic(self):
        p1 = real_proposal()  # strong uptrend -> HIGH
        p2 = copy.deepcopy(p1)
        p2["combined_strength"] = 0.5
        p2["regime"] = "transition"
        assert conviction_label(p1) == "HIGH"
        assert conviction_label(p2) == "MEDIUM"
        assert [x["signal_id"] for x in rank_signals([p2, p1])] == \
            [p1["signal_id"], p2["signal_id"]]

    def test_verdicts_never_change_ranking_or_order(self, tmp_path):
        """The exact requirement: ranking/labels/ordering computed exactly
        as before, regardless of what the LLM says."""
        p1 = real_proposal()
        p2 = copy.deepcopy(p1)
        p2["signal_id"] = p1["signal_id"] + "-weak"
        p2["combined_strength"] = 0.5
        p2["regime"] = "transition"
        ranked = rank_signals([p2, p1])

        for verdict in ("supports", "questions", "against"):
            annotations = annotate_ranked(ranked, stub_brain(verdict))
            path = write_brief(ranked, annotations, [], out_dir=str(tmp_path),
                               date="20260912")
            text = open(path).read()
            # same order, same labels, regardless of verdict
            assert text.index("[HIGH] EUR_USD") < text.index("[MEDIUM] EUR_USD")
            # commentary appears only after the facts sections
            assert text.index("## Ranked Signals") < \
                text.index("## Regime Summary") < \
                text.index("## LLM Commentary (non-authoritative)")

    def test_facts_identical_with_and_without_commentary(self, tmp_path):
        ranked = rank_signals([real_proposal()])
        path_plain = write_brief(ranked, {}, [], out_dir=str(tmp_path),
                                 date="20260912")
        ann = annotate_ranked(ranked, stub_brain("questions"))
        path_llm = write_brief(ranked, ann, [], out_dir=str(tmp_path),
                               date="20260912")
        facts_plain = open(path_plain).read().split(
            "## LLM Commentary")[0]
        facts_llm = open(path_llm).read().split("## LLM Commentary")[0]
        assert facts_plain == facts_llm


class TestDisplayOnly:
    def test_malicious_annotation_fields_never_rendered_or_applied(
            self, tmp_path):
        """No new path by which commentary text can alter stop_loss,
        take_profit, risk_pct, position size, or mode. Non-whitelisted
        annotation keys are not rendered at all."""
        ranked = rank_signals([real_proposal()])
        before = json.dumps(ranked[0], sort_keys=True, default=str)
        evil_extra = {"suggested_stop_loss": "999", "stop_loss": "999",
                      "take_profit": "999", "suggested_position_size": "999",
                      "risk_pct": "50", "mode": "live",
                      "risk_disclosure_accepted": True}
        annotations = annotate_ranked(ranked, stub_brain(
            "supports", summary="evil", extra=evil_extra))
        path = write_brief(ranked, annotations, [], out_dir=str(tmp_path),
                           date="20260912")
        text = open(path).read()
        # proposals untouched
        assert json.dumps(ranked[0], sort_keys=True, default=str) == before
        # the injected values appear nowhere in the brief
        for v in ("999", "mode: live"):
            assert v not in text
        # every commentary entry is unmistakably labeled
        for line in text.splitlines():
            if "EUR_USD long signal: evil" in line:
                assert line.strip().startswith("**[LLM READ -- not a fact]**")

    def test_header_states_verdict_semantics(self, tmp_path):
        ranked = rank_signals([real_proposal()])
        path = write_brief(ranked, {}, [], out_dir=str(tmp_path),
                           date="20260912")
        text = open(path).read()
        assert HEADER_EXECUTION_NOTE in text
        assert "against = signal skipped" in text
        assert "commentary only, no effect on execution" in text


class TestVerdictExecutionSemantics:
    """The explicit test: questions/supports never change whether a signal
    proceeds or its sizing; only 'against' does (by skipping)."""

    def _trader(self, tmp_path, verdict, auto_trade="paper"):
        from core.auto_trader import AutoTrader
        from core.risk_manager import RiskManager
        d = tmp_path / "accounts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "acct.yaml").write_text(
            "user_id: acct\nmode: paper\nauto_trade: %s\n"
            "risk_disclosure_accepted: false\n"
            "risk_disclosure_accepted_at: null\n"
            "daily_drawdown_limit_pct: 3.0\n"
            "weekly_drawdown_limit_pct: 6.0\n" % auto_trade)
        (tmp_path / "risk_limits.yaml").write_text(
            "max_risk_pct_per_trade: 1.0\nmax_position_size: 10.0\n"
            "max_daily_trades: 10\nrequired_stop_loss: true\n")
        t = AutoTrader("acct", accounts_dir=str(d),
                       journal_dir=str(tmp_path / "logs"),
                       risk_manager=RiskManager(
                           str(tmp_path / "risk_limits.yaml")))
        t.llm = stub_brain(verdict)
        return t

    def test_questions_and_supports_do_not_affect_execution(self, tmp_path):
        bars = make_bars(uptrend_closes())
        res_q = self._trader(tmp_path, "questions").step("EUR_USD", bars)
        res_s = self._trader(tmp_path, "supports").step("EUR_USD", bars)
        assert res_q["status"] == "filled"
        assert res_s["status"] == "filled"
        # identical proceeds + identical sizing (same proposal -> same fill)
        for key in ("price", "mode"):
            assert res_q[key] == res_s[key]

    def test_only_against_skips(self, tmp_path):
        res = self._trader(tmp_path, "against").step(
            "EUR_USD", make_bars(uptrend_closes()))
        assert res["status"] == "llm_advisory_veto"
        assert res["llm_agreement"] == "against"

    def test_verdict_never_changes_sizing_or_stops(self, tmp_path):
        """Whatever the verdict, the proposal reaching execution carries
        the same stop/size/risk as the deterministic engine produced."""
        from core.signal_engine import generate as gen
        p = real_proposal()
        for verdict in ("supports", "questions", "against"):
            trader = self._trader(tmp_path, verdict)
            proposal = gen("EUR_USD", make_bars(uptrend_closes()),
                           account_balance=10000.0)
            before = (proposal["suggested_stop_loss"],
                      proposal["suggested_position_size"],
                      proposal["risk_amount"])
            trader.llm.annotate(proposal)  # annotation cannot mutate
            assert (proposal["suggested_stop_loss"],
                    proposal["suggested_position_size"],
                    proposal["risk_amount"]) == before


class TestLocalFallbackConservative:
    """No OPENAI_API_KEY -> deterministic local reasoner defaults to a
    cautious posture on ambiguous cases, never silently to agreement."""

    def make_brain(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        return LLMBrain()

    def proposal_with(self, direction="long", strength=1.0, regime=None,
                      structure="HH-HL", rsi_state="neutral", rsi_val=50.0):
        return {
            "signal_id": "x", "instrument": "EUR_USD", "direction": direction,
            "combined_strength": strength, "regime": regime or "bullish_trend",
            "supporting_context": {
                "structure": structure,
                "rsi": {"value": rsi_val, "state": rsi_state},
                "candlestick_flags": [], "session_context": {},
                "premium_discount": 0.5},
        }

    def test_conflicting_structure_reads_questions_not_supports(
            self, monkeypatch):
        brain = self.make_brain(monkeypatch)
        # structure conflicts with direction -> must NOT default to supports
        ann = brain.annotate(self.proposal_with(direction="long",
                                           structure="LH-LL"))
        assert ann["agreement"] == "questions"
        assert ann["source"] == "local_deterministic"

    def test_stretched_rsi_reads_questions(self, monkeypatch):
        brain = self.make_brain(monkeypatch)
        ann = brain.annotate(self.proposal_with(rsi_state="overbought",
                                           rsi_val=74.0))
        assert ann["agreement"] == "questions"

    def test_marginal_strength_reads_questions(self, monkeypatch):
        brain = self.make_brain(monkeypatch)
        for s in (0.5, 0.6, 0.65):  # anything below HIGH (0.7) is cautious
            ann = brain.annotate(self.proposal_with(strength=s))
            assert ann["agreement"] == "questions", s
            assert any("cautious" in n for n in ann["notes"])

    def test_clean_aligned_signal_still_supports(self, monkeypatch):
        brain = self.make_brain(monkeypatch)
        ann = brain.annotate(self.proposal_with(strength=1.0, structure="HH-HL"))
        assert ann["agreement"] == "supports"

    def test_ambiguous_by_default_is_not_silent_agreement(self, monkeypatch):
        """The blanket property: for every ambiguous case we can construct,
        the local reasoner never silently returns supports."""
        brain = self.make_brain(monkeypatch)
        ambiguous = [
            self.proposal_with(direction="long", structure="LH-LL"),
            self.proposal_with(direction="short", structure="HH-HL"),
            self.proposal_with(strength=0.55),
            self.proposal_with(direction="long", rsi_state="overbought",
                          rsi_val=71.2),
            self.proposal_with(direction="short", rsi_state="oversold",
                          rsi_val=28.0),
        ]
        for p in ambiguous:
            assert brain.annotate(p)["agreement"] != "supports", \
                "ambiguous case silently agreed: %s" % p


class TestRunEndToEnd:
    def test_full_brief_generated(self, tmp_path):
        from agents.chief_of_staff import run
        path, data = run(out_dir=str(tmp_path), date="20260912")
        assert os.path.exists(path)
        assert "## LLM Commentary (non-authoritative)" in open(path).read()
        # proposals were not mutated by annotation
        for p in data["ranked"]:
            assert "llm_annotation" not in p
