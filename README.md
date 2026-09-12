# RegimeDesk

[![tests](https://github.com/malwar77/regimedesk/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/malwar77/regimedesk/actions/workflows/tests.yml)

A fact-based market regime / technical analysis desk with a trend-following
signal core, strict risk management in code, and OPTIONAL per-user MT5
execution behind an explicit, user-controlled live opt-in.

**Nothing in this project is trading advice. Output is labeled facts and
proposals only; no gain is guaranteed or implied anywhere.**

## Architecture

```
core/
  indicators.py          EMA / RSI / ATR / slope / regression / swing pivots
  sessions.py            NY session + killzone facts (ICT-labeled, see below)
  candlestick_patterns.py pin bar, engulfing, doji, marubozu, morning/evening star
  structure.py           FVG, premium/discount, swing structure, S/R levels
  exit_strategy.py       chandelier stop, parabolic SAR, fib extension, pivot targets
  position_sizing.py     pure position-size calculator
  trend_following.py     TSMOM, MA signal, Donchian, trend strength, vol scaling
  data_pipeline.py       historical + live OHLCV (OANDA v3 REST / ccxt / recorded)
  regime_engine.py       deterministic regime classification
  signal_engine.py       proposals (never trades), live signal JSONL log
  risk_manager.py        THE veto — every order passes it (paper or live)
  kill_switch.py         per-account daily/weekly drawdown blocking
  executor.py            the only route to any broker (gate chain)
  backtest.py            cost-aware backtests + mandatory benchmark comparison
  feature_validation.py  ablation importance; flags no-value feature groups
brokers/
  base_broker.py oanda_paper.py ccxt_crypto.py mt5_adapter.py
agents/
  technical.py           daily brief -> briefs/technical_YYYYMMDD.md
config/accounts/*.yaml   per-user mode/disclosure/credentials
logs/                    live_signals_*.jsonl, journal_*.jsonl, regime_latest.json
```

## Safety model (non-negotiable)

- **Risk lives in code, never in prompts.** `risk_limits.yaml` +
  `RiskManager.veto()` decide; no LLM or agent input can relax them.
- **Paper/demo is the hard default.** `mode: paper` unless the account owner
  manually edits their own config file.
- **Live requires explicit per-account opt-in**: `mode: live` set manually by
  that user — never by the LLM, an agent, a default, or any code path
  (`config_loader.load_account` refuses programmatic overrides of these
  fields).
- **Risk disclosure gate**: `risk_disclosure_accepted: true` with a
  timestamp is required. If false, **every live order is refused regardless
  of any other setting** (enforced in both the executor and the MT5 adapter).
- **Every order — paper or live — passes the unchanged RiskManager veto**
  before any broker call. `executor.py` is the only route to a broker.
- **Per-account kill switch**: before every order, that account's own
  daily/weekly drawdown limit is checked; a breach blocks that account's
  live orders (and logs why), never touching other accounts.
- **No silent switching**: broker/mode consistency is enforced; a paper
  account can never route to a live broker and vice versa.
- P&L displays show **real recorded numbers only, including losses**.

## Quickstart

```
cd regimedesk
python main.py technical                      # write today's technical brief
python main.py signal --user example_user     # generate + log proposals (no execution)
python main.py status --user example_user     # read-only status view
python main.py execute --user example_user --signal-id <id>   # explicit execution attempt
python -m pytest tests/ -q                    # full test suite
```

## Live market data

`DataPipeline(live=True, source="oanda")` uses the OANDA v3 REST candles
endpoint (needs `OANDA_API_KEY`, optional `OANDA_ENV=practice|live`).
`source="ccxt"` uses the ccxt package for crypto (optional dependency).
`source="recorded"` replays a recorded live session — this is what the
live-mode integration test uses, together with a no-lookahead guarantee:
signals at bar *i* are computed only from bars ≤ *i*.

## Connecting a live MT5 account — REAL FINANCIAL RISK

The MT5 adapter (`brokers/mt5_adapter.py`) uses the MetaTrader5 Python
package per its documented API and trades **real money** when enabled.
**Connecting a live MT5 account carries real financial risk, including the
loss of your entire account balance.** Trading leveraged instruments can
result in losses far greater than amounts committed. Nothing here is
investment advice, and no performance is promised or implied.

**Regulatory notice:** requirements for managing trades on behalf of other
people vary by jurisdiction (licensing, registration, disclosure duties).
Verify them independently — with a qualified professional — before offering
live execution to anyone other than the account owner themselves. RegimeDesk
is designed for the account owner's own use.

## ICT disclaimer

Fair-value-gap, killzone/session, and premium/discount features are derived
from the ICT methodology — an **unverified heuristic framework, not
institutionally documented**. Every ICT-derived field is labeled as such in
code and in briefs. They are analysis context only — never an auto-trigger,
and never the sole basis for a signal proposal (trend-following is the
primary signal; ICT/candlestick/S-R features are supporting context).

## LLM brain (advisory only) and auto-trading

`core/llm_brain.py` adds an LLM reasoning layer: every generated signal is
annotated with a structured reasoning summary (agreement: supports /
questions / against) that is written into the signal log. **No API key is
needed** — the default backend is Ollama (a local model server): install
Ollama, pull a model (e.g. `ollama pull llama3.2`), and the brain calls
`OLLAMA_URL` (default http://localhost:11434) with `OLLAMA_MODEL`
(default llama3.2). If no server is reachable, a deterministic local
reasoner annotates from the proposal's own facts — conservative on
ambiguous cases. Setting `OPENAI_API_KEY` is optional and takes precedence
if present. All backends return the identical validated annotation shape
and have the same advisory-only contract.

**The contract, enforced in code and tests:**
- The LLM cannot create, resize, or approve orders — the deterministic
  trend engine is the only trigger. Adversarial LLM output attempting to
  rewrite direction/size/mode is ignored by construction.
- Its only power is negative: `agreement: "against"` makes the auto-trader
  skip that signal. It can reduce trading, never increase it.
- `core/auto_trader.py` runs the loop (signal -> LLM annotation -> log ->
  optional execution) with every existing gate unchanged.

Per-account `auto_trade:` flag (in the account YAML, default `off`,
protected from programmatic changes like mode/disclosure):
- `off` — signals generated, annotated and logged only
- `paper` — approved proposals execute on the paper broker
- `live` — approved proposals execute on MT5 **only if** the account also
  has `mode: live` AND `risk_disclosure_accepted: true` (manual edits),
  and every order still passes the RiskManager veto and kill switch.

`python main.py auto --user example_user` runs one explicit cycle.

## Chief of Staff brief and the LLM Commentary section

`python main.py chief` writes `briefs/chief_of_staff_YYYYMMDD.md`: ranked
HIGH/MEDIUM deterministic signals and a regime summary, followed by a
visually distinct **"LLM Commentary (non-authoritative)"** section.

- The commentary section is the model's *interpretive read* on a signal —
  it is layered ON TOP of the deterministic facts, never merged into them.
  Every entry is tagged `[LLM READ -- not a fact]`.
- Commentary is appended read-only, AFTER ranking is finalized. It cannot
  influence signal ranking, HIGH/MEDIUM conviction labels, or ordering —
  those are computed exactly as before, by the deterministic engine.
- Only the negative **against** verdict has any functional effect
  (skipping a signal in the auto-trader). **questions and supports are
  commentary for the human reader — no effect on execution, sizing, or
  ranking.** The brief header states this.
- Without `OPENAI_API_KEY`, the deterministic local reasoner annotates
  with a deliberately conservative posture: ambiguous cases (conflicting
  structure, stretched RSI, marginal strength) read as "questions", never
  silently as agreement.
- The brief renders annotations from a fixed whitelist of fields
  (summary/agreement/notes/source). No annotation content can alter
  stop-loss, take-profit, risk, position size, or mode — display only.

## Testing

Tests cover the EMA/RSI/ATR math against hand-calculated reference values,
every candlestick pattern against built OHLC cases, chandelier/SAR/fib
reference levels, position sizing, trend functions, the risk-disclosure
gate (false blocks all live orders even with `mode: live`), per-account
kill-switch isolation, no-lookahead replay on a recorded live session, and
feature-validation flagging of a synthetic noise feature.