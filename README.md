# RegimeDesk

[![tests](https://github.com/malwar77/regimedesk/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/malwar77/regimedesk/actions/workflows/tests.yml)

> ⚠️ **EXPERIMENTAL — educational prototype.** Young project, zero
> real-money track record, no published walk-forward results yet.
> ICT/candlestick/S-R context modules are UNVALIDATED ideas included
> for study — no trading edge is claimed for them. The safety gates
> are a design contract, not proven safety. Do not risk money you
> cannot lose.

A market regime / technical analysis desk with a trend-following
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
python main.py web --user example_user        # live LAN dashboard (data-only)

The dashboard is a live data-only terminal:

- candlestick charts (TradingView Lightweight Charts, Apache-2.0,
  vendored into the repo) for BTC/ETH/SOL with timeframe buttons
- live candles come from keyless public APIs — Binance first, Kraken
  fallback (stdlib urllib, no API keys, no third-party deps in the
  core); if both fail you get an honest error, never synthetic data
- live ticker strip with 24h change per instrument
- there are NO order buttons: execution stays behind the RiskManager
```

## Web dashboard on your LAN

`python main.py web --user <id>` serves a terminal-style dashboard —
black background, green/red/blue palette, live status dot, 5-second
auto-refresh — from a stdlib-only server (no extra dependencies).
The command prints the URL to open:

```
this machine:  http://127.0.0.1:8787
on your LAN:   http://192.168.x.x:8787    <- open from your phone
```

It shows the current regime, today's signals, journal orders,
recorded equity, and your kill-switch distance — recorded facts
only, including losses. It is READ-ONLY by design: execution stays
behind the RiskManager and the manual live gates; nobody can trade
from the dashboard. To bind to this machine only, pass
`--host 127.0.0.1`.

The optional Streamlit research dashboard (`pip install streamlit`,
`python main.py dashboard`) uses the same dark terminal theme.
```

## New here? Start here

You found this repo and want to run it yourself. Here is the honest,
from-zero path. RegimeDesk is local-first: no cloud account, no signup,
no API keys needed for the core engine.

**What you get:** a deterministic trend-following signal engine with
regime classification, cost-aware backtesting, per-account paper
trading, and a RiskManager that vetoes every order in code. Optional
LLM annotations run fully local via Ollama (no API keys). Optional
live MT5 execution exists but is locked behind manual opt-in.

**What you need:** Python 3.11+ and git. The core is stdlib-only;
`pip install -r requirements.txt` adds pandas/numpy/pyyaml for the
research package. ccxt / MetaTrader5 are only needed for live data
feeds and live MT5 execution.

1. Clone and set up:
   ```
   git clone https://github.com/malwar77/regimedesk.git
   cd regimedesk
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python3 -m pytest tests/ -q        # all green = healthy clone
   ```
2. Create your account: copy `config/accounts/example_user.yaml` to
   `config/accounts/<your_name>.yaml`. It starts in *paper* mode —
   leave it that way while you learn.
3. Learn the desk (all read-only or paper):
   ```
   python main.py doctor                        # setup sanity check
   python main.py technical                     # daily technical brief
   python main.py signal --user <your_name>     # proposals only
   python main.py status --user <your_name>     # account state
   python main.py backtest --instrument BTC_USD
   ```
4. Run the paper loop: `python main.py auto --user <your_name>`
5. Read **Safety model** above before touching anything else.

**Stay in paper until you can explain the safety model to someone
else.** Going live is deliberately hard: `python main.py go-live
--account <name>` audits your account and tells you exactly what you
must edit by hand — no command ever flips the switch for you.

## Windows installation

RegimeDesk runs natively on Windows — no WSL needed. Open PowerShell:

1. Install Python 3.11+ and git:
   ```
   winget install -e Python.Python.3.12
   winget install -e Git.Git
   ```
   (or download from https://www.python.org/downloads and tick
   "Add python.exe to PATH"). Start a fresh PowerShell afterwards.
2. Clone and set up:
   ```
   git clone https://github.com/malwar77/regimedesk.git
   cd regimedesk
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m pytest tests/ -q
   ```
   If Activate.ps1 is blocked, run once:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
   (or use `.venv\Scripts\activate.bat` in cmd).
3. Everywhere the docs say `python3`, use `python` on Windows.
   Everything else is identical. Create your account the same way:
   copy `config\accounts\example_user.yaml` to
   `config\accounts\<your_name>.yaml`.
4. Morning report beacon (optional): set `AGENT_API_BASE` and
   `AGENT_API_KEY` in your environment, then schedule with Task
   Scheduler instead of cron:
   ```
   schtasks /Create /SC DAILY /ST 07:15 /TN "RegimeDeskReport" /TR "cmd /c cd /d C:\path\to\regimedesk && .venv\Scripts\python.exe main.py report --account <your_name>"
   ```
5. MT5 note: live MT5 execution actually works *best* on Windows —
   the MetaTrader5 package is Windows-native and needs a running
   MT5 terminal. Live still requires the manual per-account opt-in
   described in the safety model; the install does not change that.

## Connecting your API keys and the LLM brain

Two knobs make auto-trading smooth for any user, and neither can
relax a single safety gate:

**Broker credentials (optional — paper mode needs none).** Put them
in your account file (`config/accounts/<you>.yaml`):

```yaml
mt5_login: "your_login"
mt5_password: "your_password"
mt5_server: "your_broker_server"
```

…or leave `REPLACE_ME` in the file and export environment variables
instead (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`; the file value
wins, env is the fallback). For live OANDA market data export
`OANDA_API_KEY` (optional). Credentials are never logged, and
`python main.py doctor` shows only *presence* (e.g. `mt5_login=set
(9 chars)`), never values.

**LLM brain (advisory layer).** The default is Ollama — fully local,
free, no API keys:

```
ollama pull llama3.2
python main.py doctor        # confirms server + model
```

Per account you can choose a provider in the same YAML (all optional):

```yaml
llm_provider: ollama   # ollama (default) | openai | off
llm_model: llama3.2
llm_url: http://localhost:11434
```

`openai` uses `OPENAI_API_KEY` from your environment (optional; if the
key is missing it degrades to local reasoning, never crashes). `off`
forces the deterministic local reasoner only. Whichever provider you
pick, the LLM only annotates proposals and can only *skip* a signal
("against") — it can never create, resize or approve a trade, and the
RiskManager veto chain is unchanged.

**Smooth auto-trading** then runs exactly as designed: set
`auto_trade: paper` by hand in your account file, then

```
python main.py auto --user <you>            # one cycle
# or loop it from cron:  */5 * * * * cd /path/to/regimedesk && .venv/bin/python main.py auto --user <you>
```

None of this touches live mode: `auto_trade: live` additionally
requires `mode: live` + `risk_disclosure_accepted: true` in the same
file, and the executor enforces both independently.

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

## Backtesting (honest replay)

`python main.py backtest --count 300` replays the deterministic signal
engine over historical bars with **no lookahead** (the signal for each
slot is computed only from bars up to and including that close) and
compares the result, after costs, against buy-and-hold and an
MA-crossover benchmark. If the engine does not beat both, the report
says so plainly — absolute returns alone are never the headline. A
backtest is a fact record of the past, not a prediction: live results
can and do differ.

## Research swarm (ported from the standalone RegimeDesk build)

`python main.py research` runs the overnight research agents in
schedule order: macro/news (48h economic calendar, historical movers),
regime (5-state sticky HMM-style classifier: Crash / Bear / Neutral /
Bull / Euphoria), on-chain flow (netflow / funding / OI / whale
prints, 3-sigma vs 30-day baseline), sentiment (mention-volume spikes
vs baseline), technical (HH-HL / LH-LL structure, S/R, equal
highs-lows), and a Chief of Staff that ranks findings — HIGH only when
>=2 agents agree AND the regime aligns. It never invents symbols.

All of it lives in the `research/` package and writes markdown briefs
to `briefs/` and machine state to `state/`. By default the agents run
on deterministic synthetic/proxy data from `research/data_pipeline.py`
(paper-mode development data, labeled as such). Output is research
facts only — never a trade proposal, and the RiskManager gates in
`core/` remain absolute. The hypothesis log
(`research/hypothesis_log.py`) records preregistered expectations
before results exist, so a backtest claim can be checked against what
was predicted in advance.

`python main.py dashboard` launches the optional Streamlit desk UI
(read-only view over briefs/ and state/; `pip install streamlit`).
`research/freqtrade_sync.py` regenerates the Freqtrade dry-run config
from `config/risk_limits.yaml` — `dry_run` is hardcoded to true and
the config is a generated artifact (gitignored).

## Going live (real money) — read this first

Live mode is supported but deliberately hard, because losing real
money is easy and the gates exist to make it harder:

1. `python main.py go-live --account <name>` prints the full risk
   disclosure and audits every live gate (mode, disclosure acceptance
   + timestamp, drawdown kill-switch sanity, MT5 credentials,
   auto_trade setting).
2. If — and only if — you accept the risks, you enable live mode by
   editing the account YAML BY HAND: `mode: live`,
   `risk_disclosure_accepted: true`, `risk_disclosure_accepted_at:
   <date>`, and `auto_trade: live` for automated execution. These are
   protected fields: no command, agent or automation can set them.
3. Re-run `go-live` until all gates pass. Every live order then
   prints AND journals an explicit `!! LIVE ORDER — REAL MONEY !!`
   risk warning; the per-account drawdown kill switch stays armed,
   and the RiskManager veto chain is unchanged for live trades.

Risks you accept by going live: total loss of capital; leverage
magnifies losses; slippage and gaps make live fills worse than
paper, and stops can be gapped through; broker/VPS/network outages
can leave positions unmanaged; the kill switch limits but does not
prevent losses; the LLM component is advisory-only and can never
create, resize or approve a trade. Backtest and paper results do
not predict live performance.

## Morning WhatsApp report (status beacon)

The agent (Base44 Superagent) sends a morning WhatsApp report with
balance, open positions, kill-switch distance and a watchdog alert if
a bot stops checking in. The bot feeds it via the agent's external
API (the exact curl examples are in the agent editor's Developer /
API Docs panel):

1. Set env vars (or pass flags): `AGENT_API_BASE` (the agent's API
   root, e.g. https://<host>/api/agents/<agent_id>) and
   `AGENT_API_KEY` (the agent API key, from the editor's Developer
   panel).
2. Run `python main.py report --account <name>` — prints the status
   snapshot; with AGENT_API_BASE/AGENT_API_KEY set it also sends a
   STATUS BEACON message to the agent, which stores it.
3. Schedule it before the agent's 7:30am ET run, e.g. cron at 07:15
   America/New_York:
   `15 7 * * * cd /path/to/regimedesk && python main.py report --account <name>`

The beacon and report are strictly advisory and read-only: they
never place, approve or alter trades. The RiskManager and kill switch
on this host stay authoritative.

## Testing## Testing

Tests cover the EMA/RSI/ATR math against hand-calculated reference values,
every candlestick pattern against built OHLC cases, chandelier/SAR/fib
reference levels, position sizing, trend functions, the risk-disclosure
gate (false blocks all live orders even with `mode: live`), per-account
kill-switch isolation, no-lookahead replay on a recorded live session, and
feature-validation flagging of a synthetic noise feature.
## Third-party software

- TradingView Lightweight Charts v4.2.3 (Apache-2.0) — vendored
  unmodified alongside the dashboard assets. Charts render locally
  in your browser; no TradingView servers are contacted.
