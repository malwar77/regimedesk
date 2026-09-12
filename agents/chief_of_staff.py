"""Chief of Staff agent — daily ranked-signal brief.

Strict layering (order matters and is enforced in code):
1. Deterministic signals (core.signal_engine.generate) — the only facts.
2. Deterministic ranking + HIGH/MEDIUM conviction labels (this file) —
   computed BEFORE any LLM annotation exists, so commentary can never
   influence ranking, labels, or ordering.
3. LLM commentary — appended AFTER ranking is finalized, read-only.
   Rendered exclusively from the annotation's summary/agreement/notes/
   source fields (an explicit whitelist — any other keys in an annotation
   are never rendered and never read).

LLM VERDICT EXECUTION SEMANTICS (resolved explicitly, not just logged):
- "against"    -> the auto-trader SKIPS that signal (advisory veto). This
                  is the ONLY verdict with any functional effect anywhere.
- "questions"  -> purely descriptive. NO effect on whether a signal
                  proceeds, its sizing, its ranking, or anything else.
- "supports"   -> purely descriptive. NO effect on execution, sizing, or
                  ranking.
The brief header states this so a human reader never has to guess.
"""
import os
from datetime import datetime, timezone

from core.llm_brain import LLMBrain
from core.signal_engine import build_features, generate

HIGH_STRENGTH = 0.7  # combined strength needed for HIGH conviction

HEADER_EXECUTION_NOTE = (
    "LLM verdicts: against = signal skipped; questions/supports = "
    "commentary only, no effect on execution.")

# The ONLY annotation fields ever rendered in the brief. Display-only.
RENDERED_ANNOTATION_FIELDS = ("summary", "agreement", "notes", "source")


def conviction_label(proposal):
    """Deterministic HIGH/MEDIUM label. No LLM input, ever."""
    aligned = (
        (proposal["direction"] == "long"
         and proposal["regime"] == "bullish_trend")
        or (proposal["direction"] == "short"
            and proposal["regime"] == "bearish_trend"))
    if proposal["combined_strength"] >= HIGH_STRENGTH and aligned:
        return "HIGH"
    return "MEDIUM"


def rank_signals(proposals):
    """Deterministic ranking: HIGH before MEDIUM, then combined strength
    descending. Takes ONLY deterministic proposals — nothing from the LLM."""
    return sorted(
        proposals,
        key=lambda p: (0 if conviction_label(p) == "HIGH" else 1,
                       -p["combined_strength"]))


def annotate_ranked(ranked, llm=None):
    """Annotate AFTER ranking is final. Returns {signal_id: annotation}.
    Annotations are stored in their own namespace and never written back
    into the proposals."""
    llm = llm or LLMBrain()
    return {p["signal_id"]: llm.annotate(p) for p in ranked}


def write_brief(ranked, annotations, no_signals, out_dir="briefs", date=None):
    """Write briefs/chief_of_staff_YYYYMMDD.md.

    Facts sections are rendered from the deterministic proposals only.
    The LLM Commentary section is appended last and rendered from the
    whitelisted annotation fields only — display-only by construction."""
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "chief_of_staff_%s.md" % date)

    lines = [
        "# Chief of Staff Brief — %s" % date,
        "",
        "Ranked signals, conviction labels and ordering are computed by the "
        "deterministic engine. %s" % HEADER_EXECUTION_NOTE,
        "",
        "## Ranked Signals",
        "",
    ]
    if not ranked:
        lines.append("_No deterministic signals today._")
    for p in ranked:
        ctx = p["supporting_context"]
        lines.append("### [%s] %s — %s" % (
            conviction_label(p), p["instrument"], p["direction"]))
        lines.append("- combined strength: %.3f | regime: %s"
                     % (p["combined_strength"], p["regime"]))
        lines.append("- entry: %s | suggested stop-loss: %s (%s) | "
                      "suggested take-profit: %s"
                     % (p["entry_price"], p["suggested_stop_loss"],
                        p["suggested_stop_basis"], p["suggested_take_profit"]))
        lines.append("- suggested size: %s | risk amount: %s"
                     % (p["suggested_position_size"], p["risk_amount"]))
        lines.append("- structure: %s | RSI(14): %s (%s)"
                     % (ctx["structure"], ctx["rsi"]["value"],
                        ctx["rsi"]["state"]))
        lines.append("- session context: %s"
                     % ctx["session_context"]["label"])
        lines.append("")

    lines.append("## Regime Summary")
    lines.append("")
    for p in ranked:
        lines.append("- %s: %s (signal: %s %s)"
                     % (p["instrument"], p["regime"], p["direction"],
                        conviction_label(p)))
    for instrument, regime in no_signals:
        lines.append("- %s: %s (no signal under current rules)"
                     % (instrument, regime))
    lines.append("")

    # ---- appended read-only LLM layer, AFTER the facts ----
    lines.append("## LLM Commentary (non-authoritative)")
    lines.append("")
    lines.append("_The entries below are the model's interpretive read on a "
                 "signal, distinct from the deterministic facts above. They "
                 "did not influence ranking or conviction labels, and only "
                 "an 'against' verdict has any functional effect (skipping "
                 "a signal)._")
    lines.append("")
    if not annotations:
        lines.append("_No commentary available._")
    for p in ranked:
        ann = annotations.get(p["signal_id"])
        if not ann:
            continue
        notes = "; ".join(ann.get("notes", [])) or "none"
        # rendered ONLY from whitelisted fields — display-only
        lines.append("**[LLM READ -- not a fact]** %s %s signal: %s "
                     "Verdict: %s. Notes: \"%s\" (source: %s)"
                     % (p["instrument"], p["direction"],
                        ann.get("summary", ""), ann.get("agreement", ""),
                        notes, ann.get("source", "")))
        lines.append("")
    lines.append("---")
    lines.append(HEADER_EXECUTION_NOTE)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def run(pipeline=None, llm=None, out_dir="briefs",
        watchlist_path="watchlist.yaml", date=None):
    """Full daily cycle: signals -> ranking -> (then) LLM commentary ->
    brief. Returns (brief_path, data)."""
    from agents.technical import load_watchlist
    from core.data_pipeline import DataPipeline

    pipeline = pipeline or DataPipeline(live=False, source="synthetic")
    proposals, no_signals = [], []
    for instrument in load_watchlist(watchlist_path):
        bars = pipeline.fetch(instrument, "H1", count=300)
        p = generate(instrument, bars, account_balance=10000.0)
        if p is None:
            regime = build_features(bars)["regime"]["regime"] \
                if len(bars) >= 210 else "insufficient data"
            no_signals.append((instrument, regime))
        else:
            proposals.append(p)

    ranked = rank_signals(proposals)          # deterministic, LLM-free
    annotations = annotate_ranked(ranked, llm)  # AFTER ranking is final
    path = write_brief(ranked, annotations, no_signals, out_dir=out_dir,
                       date=date)
    return path, {"ranked": ranked, "annotations": annotations,
                  "no_signals": no_signals}
