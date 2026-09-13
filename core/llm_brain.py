"""LLM brain — an ADVISORY reasoning layer over the deterministic signal engine.

DESIGN CONTRACT (matches the project's standing safety rules):
- The LLM REASONS about proposals; it never creates, modifies, sizes, or
  approves orders. The rule-based trend engine is the only trigger.
- Its output is an annotation, kept in its own namespace
  ("llm_annotation") and validated. Fields like direction/size/stop_loss
  coming back from the LLM are IGNORED by construction — the executor
  builds orders from the deterministic proposal fields only.
- Its only power is NEGATIVE: agreement == "against" makes the auto-trader
  skip execution of that signal (an advisory veto). It can reduce trading;
  it can never increase it.
- Live execution remains human-opt-in only (mode + risk disclosure in the
  account file) and still passes the RiskManager veto and kill switch.

Backend chain (first available wins — all return the same validated
annotation shape):
1. OpenAI Chat Completions (only if OPENAI_API_KEY is set) — optional.
2. Ollama (default when reachable, NO API KEY) — a local model server at
   OLLAMA_URL (default http://localhost:11434), model OLLAMA_MODEL
   (default llama3.2), called via the documented /api/chat endpoint with
   JSON-mode format. The reasoning stays on your machine.
3. Deterministic local reasoner — pure-Python annotation from the
   proposal's own facts, so tests and offline use never depend on any
   server. Conservative posture: ambiguous cases read "questions".
"""
import json
import os
import urllib.request

ADVISORY_NOTE = ("LLM output is advisory only and never a trade trigger; "
                 "orders come solely from the deterministic engine through "
                 "the RiskManager gates.")

ALLOWED_AGREEMENT = ("supports", "questions", "against")

_SYSTEM_PROMPT = (
    "You are the reasoning layer of a rule-based trading system "
    "(RegimeDesk). You CANNOT place, modify, size, or approve any trade. "
    "You only analyze a rule-generated signal proposal and reply with a "
    "strict JSON object: "
    '{"summary": string (2-4 sentences of factual reasoning), '
    '"agreement": "supports" | "questions" | "against", '
    '"notes": [string, ...] (concrete observations about the supplied '
    "facts)}. Reason only from the supplied facts. Never promise or imply "
    "gains. If the evidence is contradictory, say so plainly."
)


class LLMBrain:
    PROVIDERS = ("auto", "ollama", "openai", "off")

    def __init__(self, api_key=None, model="gpt-4o-mini", timeout=25,
                 api_url="https://api.openai.com/v1/chat/completions",
                 ollama_url=None, ollama_model=None, provider="auto"):
        if provider not in self.PROVIDERS:
            raise ValueError("provider must be one of %s (got %r)"
                             % (self.PROVIDERS, provider))
        self.provider = provider
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model  # OpenAI model (only used if api_key is set)
        self.timeout = timeout
        self.api_url = api_url
        self.ollama_url = (ollama_url or os.environ.get("OLLAMA_URL")
                           or "http://localhost:11434")
        self.ollama_model = (ollama_model
                             or os.environ.get("OLLAMA_MODEL")
                             or "llama3.2")
        self.last_source = None
        self._ollama_probe = None  # None = unchecked; then True/False

    def _ollama_available(self):
        """Cheap reachability probe (GET /api/tags), cached. No key, no
        auth — Ollama is a local server."""
        if self._ollama_probe is None:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(self.ollama_url + "/api/tags",
                                           method="GET"), timeout=2)
                self._ollama_probe = True
            except Exception:  # noqa: BLE001 — offline is a normal state
                self._ollama_probe = False
        return self._ollama_probe

    def annotate(self, proposal):
        """Return {"summary", "agreement", "notes", "source", "advisory"}.
        Never mutates the proposal. Never raises — any backend failure
        degrades to the deterministic local annotation."""
        facts = self._facts(proposal)
        provider = self.provider
        if provider == "off":
            # explicitly disabled: deterministic local reasoner only,
            # even if Ollama or an API key happens to be available
            ann = self._validate(self._local(facts),
                                 source="local_deterministic")
            self.last_source = ann["source"]
            return ann
        if provider == "openai" or (provider == "auto"
                                    and self.api_key):
            # explicit OpenAI choice, or auto with a key present
            if self.api_key:
                return self._annotate_via(
                    lambda: self._call_api(facts),
                    "openai:%s" % self.model, facts, "LLM API call failed")
            if provider == "openai":
                ann = self._validate(self._local(facts),
                                     source="local_deterministic")
                ann["notes"] = ann["notes"] + [
                    "llm_provider is openai but OPENAI_API_KEY is not set; "
                    "using local reasoning"]
                self.last_source = ann["source"]
                return ann
        if self._ollama_available():  # default path: local model, no key
            return self._annotate_via(
                lambda: self._call_ollama(facts),
                "ollama:%s" % self.ollama_model, facts,
                "Ollama call failed")
        # no backend at all: pure deterministic local reasoner
        ann = self._validate(self._local(facts),
                             source="local_deterministic")
        self.last_source = ann["source"]
        return ann

    def _annotate_via(self, call, source, facts, fail_label):
        try:
            ann = self._validate(call(), source=source)
            self.last_source = ann["source"]
            return ann
        except Exception as exc:  # noqa: BLE001 — degrade, never crash
            ann = self._validate(self._local(facts),
                                 source="local_deterministic")
            ann["notes"] = ann["notes"] + [
                "%s (%s); using local reasoning" % (fail_label, exc)]
            self.last_source = ann["source"]
            return ann

    def _facts(self, proposal):
        """The exact fact set the LLM may reason over — the deterministic
        proposal and its context, nothing else."""
        ctx = proposal.get("supporting_context", {})
        return {
            "instrument": proposal.get("instrument"),
            "direction": proposal.get("direction"),
            "combined_strength": proposal.get("combined_strength"),
            "regime": proposal.get("regime"),
            "trend_reasoning": proposal.get("reasoning"),
            "structure": ctx.get("structure"),
            "rsi": ctx.get("rsi"),
            "candlestick_flags": ctx.get("candlestick_flags"),
            "session_context": ctx.get("session_context", {}).get("label"),
            "premium_discount": ctx.get("premium_discount"),
            "suggested_stop_loss": proposal.get("suggested_stop_loss"),
            "suggested_take_profit": proposal.get("suggested_take_profit"),
        }

    def _call_api(self, facts):
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts)},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.load(resp)
        return json.loads(body["choices"][0]["message"]["content"])

    def _call_ollama(self, facts):
        """Native Ollama /api/chat (documented endpoint) with JSON mode:
        payload {"model", "messages", "stream": false, "format": "json"} ->
        response {"message": {"content": "<json>"}}. No API key."""
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(facts)},
            ],
            "stream": False,
            "format": "json",          # documented Ollama JSON mode
            "options": {"temperature": 0.2},
        }
        req = urllib.request.Request(
            self.ollama_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.load(resp)
        return json.loads(body["message"]["content"])

    def _local(self, facts):
        """Deterministic offline reasoner: annotates from the proposal's own
        facts. Agreement checks internal consistency of the deterministic
        signal, not any new judgment."""
        notes = []
        direction = facts.get("direction")
        strength = facts.get("combined_strength") or 0.0
        structure = facts.get("structure")
        rsi = facts.get("rsi") or {}
        # CONSERVATIVE DEFAULT: ambiguous or marginal cases read as
        # "questions" (visible doubt), never a silent "supports".
        # A clean, aligned, strong signal is the only case that supports.
        agreement = "supports"
        if strength < 0.7:  # anything below the HIGH conviction threshold
            agreement = "questions"
            notes.append("combined strength %.3f is below the HIGH "
                         "conviction threshold; posture is cautious"
                         % strength)
        if structure in ("HH-HL", "LH-LL") and \
                ((structure == "HH-HL") == (direction == "long")):
            notes.append("swing structure %s is consistent with a %s signal"
                         % (structure, direction))
        elif structure in ("HH-HL", "LH-LL"):
            agreement = "questions"
            notes.append("swing structure %s conflicts with the %s signal"
                         % (structure, direction))
        if rsi.get("state") == "overbought" and direction == "long":
            agreement = "questions"
            notes.append("RSI is overbought while the signal is long — "
                         "entry context is stretched, a fact worth noting")
        if rsi.get("state") == "oversold" and direction == "short":
            agreement = "questions"
            notes.append("RSI is oversold while the signal is short — "
                         "entry context is stretched, a fact worth noting")
        notes.append("combined trend strength %.3f from the deterministic "
                     "engine" % strength)
        summary = ("Local deterministic reasoning: the %s proposal has "
                   "combined strength %.3f in a %s regime; %s."
                   % (direction, strength, facts.get("regime"),
                      "; ".join(notes[:2]) or "no contradictions in the facts"))
        return {"summary": summary, "agreement": agreement, "notes": notes}

    def _validate(self, ann, source):
        if not isinstance(ann, dict):
            return {"summary": "annotation unreadable", "agreement":
                    "questions", "notes": [], "source": source,
                    "advisory": ADVISORY_NOTE}
        agreement = ann.get("agreement")
        if agreement not in ALLOWED_AGREEMENT:
            agreement = "questions"
        return {
            "summary": str(ann.get("summary", ""))[:2000],
            "agreement": agreement,
            "notes": [str(n)[:500] for n in ann.get("notes", [])][:10],
            "source": source,
            "advisory": ADVISORY_NOTE,
        }
