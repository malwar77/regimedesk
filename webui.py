"""Read-only web dashboard — stdlib only, no extra dependencies.

Serves a terminal-style dashboard on your LAN (default 0.0.0.0:8787)
so you can watch RegimeDesk from any device on the same network:
phone, laptop, tablet. It is a WATCHING surface: no trade controls
live here by design — execution stays in the CLI behind the
RiskManager and the manual live gates.

Run with: python main.py web --user testuser
Open:    http://<your-local-ip>:8787 (the command prints the URL)
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _today() -> datetime:
    return datetime.now(timezone.utc)


# ---- live market data (stdlib only, keyless public APIs) ----------
import urllib.request  # noqa: E402 (stdlib, kept local to this feature)

_market_cache: dict[str, tuple[float, object]] = {}
_MARKET_TTL = 60.0

_BINANCE_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
_KRAKEN_INTERVALS = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _http_get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": "RegimeDesk/1.0 (dashboard)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _binance_candles(base: str, quote: str, tf: str, limit: int):
    sym = base + ("USDT" if quote in ("USD", "USDT") else quote.upper())
    rows = _http_get_json(
        "https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d"
        % (sym, _BINANCE_INTERVALS[tf], limit))
    return [{"time": int(r[0] / 1000), "open": float(r[1]),
             "high": float(r[2]), "low": float(r[3]),
             "close": float(r[4]), "volume": float(r[5])} for r in rows]


def _kraken_candles(base: str, quote: str, tf: str, limit: int):
    pair = base.upper() + ("" if quote != "USD" else "")  # XBTUSD-style
    if base.upper() == "BTC":
        pair = "XBTUSD" if quote.upper() == "USD" else "XBT" + quote.upper()
    else:
        pair = base.upper() + quote.upper()
    data = _http_get_json(
        "https://api.kraken.com/0/public/OHLC?pair=%s&interval=%d"
        % (pair, _KRAKEN_INTERVALS[tf]))
    result = data.get("result", {})
    rows = result.get(pair) or (list(result.values())[0]
                               if result else [])
    return [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]),
             "volume": float(r[6])} for r in rows[-limit:]]


def fetch_candles(instrument: str = "BTC_USD", tf: str = "1h",
                  limit: int = 200) -> dict:
    """Live OHLCV from keyless public APIs (Binance first, Kraken
    fallback), TTL-cached so dashboard polls stay polite. Real data
    only: every source failure raises — the chart shows an honest
    error, never synthetic candles."""
    import time as _time
    key = ("candles", instrument, tf, limit)
    now = _time.time()
    hit = _market_cache.get(key)
    if hit and now - hit[0] < _MARKET_TTL:
        return hit[1]
    base, _, quote = instrument.partition("_")
    errors = []
    for name, fn in (("binance", _binance_candles),
                     ("kraken", _kraken_candles)):
        try:
            candles = fn(base or "BTC", quote or "USD", tf, limit)
            if candles:
                out = {"instrument": instrument, "timeframe": tf,
                       "source": name, "candles": candles}
                _market_cache[key] = (now, out)
                return out
        except Exception as exc:  # try next source
            errors.append("%s: %s" % (name, exc))
    raise RuntimeError("no live source available — " + "; ".join(errors))


def fetch_market_stats(symbols: tuple = ("BTC_USD", "ETH_USD",
                                         "SOL_USD")) -> dict:
    """24h-ish ticker stats per symbol (last price + change % since
    the day's open), TTL-cached, from the same keyless sources."""
    import time as _time
    key = ("stats", symbols)
    now = _time.time()
    hit = _market_cache.get(key)
    if hit and now - hit[0] < _MARKET_TTL:
        return hit[1]
    out = {}
    for sym in symbols:
        try:
            data = fetch_candles(sym, "1h", 25)  # ~24h of hourly bars
            closes = [c["close"] for c in data["candles"]]
            if closes:
                first, last = closes[0], closes[-1]
                out[sym] = {
                    "last": last,
                    "change_pct": (last / first - 1) * 100,
                    "high": max(c["high"] for c in data["candles"]),
                    "low": min(c["low"] for c in data["candles"]),
                    "source": data["source"],
                }
        except Exception:
            continue  # other symbols still serve
    if not out:
        raise RuntimeError("no live market source available")
    _market_cache[key] = (now, out)
    return out


def lan_ip() -> str:
    """Best-effort LAN IP of this machine. Never raises."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # routing only; no packets sent
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:  # pragma: no cover
        return "127.0.0.1"


def build_status(user_id: str = "testuser",
                 accounts_dir: str = "config/accounts",
                 logs_dir: str = "logs",
                 state_dir: str = "state") -> dict:
    """Assemble the dashboard payload from recorded facts only.
    Real numbers, including losses. Nothing here can trade."""
    from core.config_loader import load_account
    from core.kill_switch import AccountKillSwitch, Journal

    payload = {
        "user": user_id,
        "generated_at": _today().isoformat(timespec="seconds"),
        "disclaimer": "Recorded facts only — real numbers, including "
                      "losses. No gain is guaranteed or implied. This "
                      "dashboard is read-only.",
    }
    try:
        account = load_account(user_id, accounts_dir)
        payload["mode"] = account.mode  # paper | live (human-set only)
    except Exception as exc:  # account missing -> show, don't crash
        payload["mode"] = "unknown"
        payload["account_error"] = str(exc)

    # current regime (latest snapshot)
    regime_path = os.path.join(logs_dir, "regime_latest.json")
    if os.path.exists(regime_path):
        with open(regime_path) as f:
            payload["regime"] = json.load(f)
    else:
        payload["regime"] = None

    # signals generated today
    sig_path = os.path.join(
        logs_dir, "live_signals_%s.jsonl" % _today().strftime("%Y%m%d"))
    signals = []
    if os.path.exists(sig_path):
        with open(sig_path) as f:
            signals = [json.loads(l) for l in f if l.strip()]
    payload["signals_today"] = len(signals)
    payload["recent_signals"] = [
        {k: s.get(k) for k in ("timestamp", "direction", "instrument",
                               "combined_strength", "executed")}
        for s in signals[-10:]]

    # journal: orders, recorded equity, kill-switch distance
    try:
        journal = Journal(user_id)
        entries = journal.entries()
        orders = [e for e in entries if e.get("type") == "order"]
        payload["orders_all_time"] = len(orders)
        today = _today().date().isoformat()
        payload["orders_today"] = sum(
            1 for e in orders if e.get("ts", "")[:10] == today)
        if orders:
            equity = orders[-1].get("equity_after")
            payload["recorded_equity"] = equity
            ks = AccountKillSwitch(load_account(user_id, accounts_dir))
            check = ks.check(equity)
            payload["kill_switch"] = {
                "daily_drawdown_pct": check["daily_drawdown_pct"],
                "daily_limit_pct": ks.account.daily_drawdown_limit_pct,
                "weekly_drawdown_pct": check["weekly_drawdown_pct"],
                "weekly_limit_pct": ks.account.weekly_drawdown_limit_pct,
                "blocked": check["blocked"],
                "reasons": check["reasons"],
            }
    except Exception:  # pragma: no cover — journal optional
        payload["orders_all_time"] = 0

    # research layer latest verdicts (state/*.json), if present
    chief_path = os.path.join(state_dir, "chief_latest.json")
    if os.path.exists(chief_path):
        try:
            with open(chief_path) as f:
                payload["chief"] = json.load(f)
        except (ValueError, OSError):
            payload["chief"] = None
    return payload


PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RegimeDesk — Live Terminal</title>
<script src="/static/lightweight-charts.standalone.production.js"></script>
<style>
#tickers { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.tick { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:8px; padding:8px 12px;
  font-size:12px; min-width:130px; }
.tick .p { color:var(--blue); font-weight:700; }
.tick .px { font-size:13px; }
#chartwrap { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:10px; padding:10px;
  margin-bottom:14px; }
#chart { width:100%; height:380px; }
.chart-head { display:flex; justify-content:space-between; align-items:center;
  padding:0 4px 8px; flex-wrap:wrap; gap:8px; }
button { font-family:var(--mono); font-size:12px; cursor:pointer;
  background:var(--panel2); color:var(--text); border:1px solid var(--border);
  border-radius:6px; padding:5px 10px; }
button.active { color:var(--green); border-color:var(--green); }
:root { --bg:#04070a; --panel:#0a0f14; --panel2:#0c1218;
  --green:#00e68a; --red:#ff4d5e; --blue:#4da3ff;
  --blue-dim:#071a2e; --green-dim:#06231a; --red-dim:#26060d;
  --text:#c8d6e0; --muted:#5f7180; --border:#14212c;
  --mono:ui-monospace,'JetBrains Mono','Fira Code','SF Mono',Consolas,monospace; }
* { box-sizing:border-box; }
body { font-family:var(--mono); background:var(--bg); color:var(--text);
  margin:0; padding:20px; max-width:1100px; margin-inline:auto;
  background-image:
    radial-gradient(ellipse 800px 300px at 50% -10%, rgba(77,163,255,.06), transparent),
    radial-gradient(ellipse 600px 200px at 90% 110%, rgba(0,230,138,.05), transparent); }
header { border-bottom:1px solid var(--border); padding-bottom:14px;
  margin-bottom:20px; display:flex; align-items:baseline;
  justify-content:space-between; flex-wrap:wrap; gap:8px; }
h1 { font-size:17px; margin:0; letter-spacing:.08em; color:var(--blue);
  text-shadow:0 0 14px rgba(77,163,255,.35); }
h1 .desk { color:var(--green); text-shadow:0 0 14px rgba(0,230,138,.35); }
.live-dot { display:inline-block; width:9px; height:9px; border-radius:50%;
  background:var(--green); margin-right:6px; box-shadow:0 0 8px var(--green);
  animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
.sub { color:var(--muted); font-size:12px; }
.cards { display:grid; gap:14px; grid-template-columns:
  repeat(auto-fit,minmax(200px,1fr)); margin-bottom:20px; }
.card { background:linear-gradient(180deg,var(--panel2),var(--panel));
  border:1px solid var(--border); border-radius:10px; padding:16px;
  position:relative; overflow:hidden; }
.card::before { content:""; position:absolute; inset:0 auto auto 0;
  width:3px; height:100%; background:var(--blue); opacity:.8; }
.card.g::before { background:var(--green); } .card.r::before { background:var(--red); }
.label { font-size:11px; letter-spacing:.12em; color:var(--muted);
  text-transform:uppercase; margin-bottom:8px; }
.value { font-size:20px; font-weight:700; }
.pos { color:var(--green); } .neg { color:var(--red); } .blue { color:var(--blue); }
table { width:100%; border-collapse:collapse; font-size:13px;
  background:var(--panel); border:1px solid var(--border);
  border-radius:10px; overflow:hidden; }
th { text-align:left; padding:10px 12px; color:var(--blue);
  background:var(--blue-dim); font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; }
td { padding:9px 12px; border-top:1px solid var(--border); }
tr:hover td { background:rgba(77,163,255,.04); }
.banner { border:1px solid var(--border); border-left:3px solid var(--red);
  background:var(--red-dim); color:#ff8a95; padding:10px 14px;
  border-radius:8px; font-size:12px; margin-bottom:18px; display:none; }
.note { color:var(--muted); font-size:12px; margin-top:16px; line-height:1.6; }
h2 { font-size:13px; letter-spacing:.1em; color:var(--blue); }
select { font-family:var(--mono); font-size:13px; background:var(--panel2);
  color:var(--text); border:1px solid var(--border); border-radius:6px;
  padding:5px 8px; }
@media (prefers-reduced-motion:reduce) { .live-dot { animation:none; } }
</style></head><body>
<div id="seasons" class="sub" style="padding:6px 14px">loading seasons…</div>
<header><div>
  <h1><span class="live-dot"></span>REGIME<span class="desk">DESK</span> <span class="sub">live terminal</span></h1>
  <div class="sub">live candles from keyless public APIs &middot; charts: TradingView Lightweight Charts (Apache-2.0, vendored) &middot; execution stays behind the RiskManager</div>
</div><div class="sub">user: <span id="user" class="blue">...</span>
  &middot; mode: <span id="mode" class="blue">...</span>
  &middot; updated <span id="updated" class="blue">...</span></div></header>
<div id="ks-banner" class="banner"></div>
<div id="tickers" class="sub">loading live market data...</div>
<div id="chartwrap"><div class="chart-head">
  <span>
    <span class="sub">instrument</span>
    <select id="instrument" onchange="loadChart()">
      <option>BTC_USD</option><option>ETH_USD</option><option>SOL_USD</option>
    </select>
    <span id="tf" style="margin-left:10px"></span>
  </span>
  <span class="sub" id="chart-note">candles: live, never synthetic</span>
</div><div id="chart"></div></div>
<div class="cards">
  <div class="card g"><div class="label">Regime</div>
    <div class="value pos" id="regime">...</div></div>
  <div class="card"><div class="label">Signals today</div>
    <div class="value" id="signals">...</div></div>
  <div class="card"><div class="label">Orders (all time)</div>
    <div class="value" id="orders">...</div></div>
  <div class="card"><div class="label">Recorded equity</div>
    <div class="value" id="equity">...</div></div>
  <div class="card r"><div class="label">Drawdown vs limit</div>
    <div class="value" id="ks">...</div></div>
</div>
<h2>RECENT SIGNALS (TODAY)</h2>
<table><thead><tr><th>Time</th><th>Direction</th><th>Instrument</th>
  <th>Strength</th><th>Executed</th></tr></thead>
  <tbody id="sig-body"><tr><td colspan="5" class="sub">...</td></tr></tbody></table>
<div class="note" id="disclaimer"></div>
<script>
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function cls(v){return v>=0?"pos":"neg";}
function sgn(v){return (v>=0?"+":"")+Number(v).toFixed(2);}
let chart=null, candles=null;
const TFS=["15m","1h","4h","1d"]; let activeTf="1h";
function ensureChart(){
  if (chart) return;
  chart = LightweightCharts.createChart(document.getElementById("chart"),
    {layout:{background:{color:"transparent"},textColor:"#5f7180"},
     grid:{vertLines:{color:"#14212c"},horzLines:{color:"#14212c"}},
     timeScale:{timeVisible:true,secondsVisible:false},
     autoSize:true, height:380});
  candles = chart.addCandlestickSeries({upColor:"#00e68a",downColor:"#ff4d5e",
    borderUpColor:"#00e68a",borderDownColor:"#ff4d5e",
    wickUpColor:"#00e68a",wickDownColor:"#ff4d5e"});
  const tf=document.getElementById("tf");
  TFS.forEach(t=>{const b=document.createElement("button");b.textContent=t;
    if(t===activeTf)b.className="active";
    b.onclick=()=>{activeTf=t;tf.querySelectorAll("button").forEach(x=>x.className="");
      b.className="active";loadChart();};tf.appendChild(b);});
}
async function loadChart(){
  ensureChart();
  const inst=document.getElementById("instrument").value;
  try{
    const r=await fetch("/api/candles?instrument="+encodeURIComponent(inst)+"&tf="+activeTf);
    const d=await r.json();
    if(!r.ok){candles.setData([]);candles.applyOptions({title:inst+" — "+d.error.slice(0,60)});return;}
    candles.setData(d.candles);
    candles.applyOptions({title:inst+" "+d.timeframe+" ["+d.source+"]"});
    document.getElementById("chart-note").textContent=
      "live candles from "+d.source+" — never synthetic";
    chart.timeScale().fitContent();
  }catch(e){/* keep last chart */}
}
async function loadTickers(){
  try{
    const r=await fetch("/api/market_stats");
    const d=await r.json();
    if(!r.ok){document.getElementById("tickers").textContent=
      "live market data unavailable (offline?)";return;}
    document.getElementById("tickers").innerHTML=Object.entries(d).map(([sym,t])=>
      `<span class="tick"><span class="p">${esc(sym.replace("_","/"))}</span><br>
       <span class="px">${Number(t.last).toPrecision(6)}</span>
       <span class="${cls(t.change_pct)}">${sgn(t.change_pct)}%</span></span>`).join("");
  }catch(e){/* keep old */}
}
async function refresh(){
  try{
    const s = await (await fetch("/api/status")).json();
    document.getElementById("user").textContent = s.user || "?";
    document.getElementById("mode").textContent = s.mode || "?";
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    const r = document.getElementById("regime");
    r.textContent = (s.regime && s.regime.regime) ? s.regime.regime : "no data yet";
    document.getElementById("signals").textContent = s.signals_today ?? 0;
    document.getElementById("orders").textContent = (s.orders_all_time ?? 0)
      + " (today: " + (s.orders_today ?? 0) + ")";
    document.getElementById("equity").textContent =
      s.recorded_equity != null ? s.recorded_equity : "no orders yet";
    const ks = document.getElementById("ks");
    if (s.kill_switch){
      ks.textContent = s.kill_switch.daily_drawdown_pct.toFixed(2) + "% / "
        + s.kill_switch.daily_limit_pct.toFixed(2) + "%";
      ks.className = "value " + (s.kill_switch.blocked ? "neg" : "pos");
      const b = document.getElementById("ks-banner");
      if (s.kill_switch.blocked){ b.style.display = "block";
        b.textContent = "KILL SWITCH ENGAGED: " + (s.kill_switch.reasons||[]).join("; "); }
      else { b.style.display = "none"; }
    } else { ks.textContent = "n/a"; }
    const body = document.getElementById("sig-body");
    if ((s.recent_signals||[]).length){
      body.innerHTML = s.recent_signals.map(x =>
        `<tr><td class="sub">${esc((x.timestamp||"?").slice(0,19))}</td>
        <td class="${x.direction==="long"?"pos":"neg"}">${esc(x.direction)}</td>
        <td class="blue">${esc(x.instrument)}</td>
        <td>${(x.combined_strength??0).toFixed(3)}</td>
        <td class="${x.executed?"pos":"sub"}">${x.executed?"yes":"no"}</td></tr>`).join("");
    } else { body.innerHTML = `<tr><td colspan="5" class="sub">no signals recorded today</td></tr>`; }
    if (s.disclaimer) document.getElementById("disclaimer").textContent = s.disclaimer;
  }catch(e){ document.getElementById("updated").textContent = "connection lost"; }
}
refresh(); loadChart(); loadTickers();
setInterval(refresh, 5000);
setInterval(loadTickers, 30000);
setInterval(loadChart, 60000);
</script><script>
async function loadSeasons(){
  try{
    const el = document.getElementById("seasons");
    if (!el) return;
    const d = await (await fetch("/api/seasons")).json();
    if (!(d.seasons||[]).length){ el.textContent =
      "seasons: none yet — run paper mode to start a DEMO season"; return; }
    el.innerHTML = "seasons: " + d.seasons.map(s =>
      `<span style="margin-right:14px">` +
      (s.label==="REAL MONEY" ? `<span class="neg">` : ``) +
      `#${s.season} ${s.label} ${s.start.slice(0,10)}&rarr;${s.end.slice(0,10)} &middot; ${s.orders} orders &middot; span ${s.span_days.toFixed(0)}d` +
      (s.label==="REAL MONEY" ? `</span>` : ``) + `</span>`).join("");
  }catch(e){ /* keep old text */ }
}
loadSeasons(); setInterval(loadSeasons, 60000);
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    user_id = "testuser"

    def do_GET(self):  # noqa: N802 (stdlib naming)
        path = self.path.split("?")[0]
        query = self.path.split("?")[1] if "?" in self.path else ""
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            payload = build_status(self.user_id)
            body = json.dumps(payload, default=str).encode()
            self._json(body)
        elif path == "/api/seasons":
            try:
                from core.kill_switch import Journal
                from core.seasons import compute_seasons
                seasons = compute_seasons(
                    Journal(self.user_id).entries())
                body = json.dumps({"seasons": seasons},
                                  default=str).encode()
            except Exception as exc:
                body = json.dumps({"seasons": [], "error": str(exc)}).encode()
            self._json(body)
        elif path == "/api/candles":
            params = dict(p.split("=", 1) for p in query.split("&")
                          if "=" in p)
            try:
                body = json.dumps(fetch_candles(
                    params.get("instrument", "BTC_USD"),
                    params.get("tf", "1h"),
                    int(params.get("limit", 200)))).encode()
                self._json(body)
            except Exception as exc:
                self._json(json.dumps(
                    {"error": "live market data unavailable: %s" % exc}
                ).encode(), status=503)
        elif self.path == "/api/market_stats":
            try:
                body = json.dumps(fetch_market_stats()).encode()
                self._json(body)
            except Exception as exc:
                self._json(json.dumps(
                    {"error": "market stats unavailable: %s" % exc}
                ).encode(), status=503)
        elif path == "/static/lightweight-charts.standalone.production.js":
            self._js()
        else:
            self.send_error(404)

    def _json(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _js(self):
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, "static",
                         "lightweight-charts.standalone.production.js")
        try:
            with open(p, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404, "chart library missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet
        pass


def serve(user_id: str = "testuser", host: str = "0.0.0.0",
          port: int = 8787) -> None:
    """Run the dashboard until interrupted. Read-only by construction."""
    _Handler.user_id = user_id
    server = ThreadingHTTPServer((host, port), _Handler)
    print("RegimeDesk dashboard (read-only — no trade controls)")
    print("  this machine:  http://127.0.0.1:%d" % port)
    if host not in ("127.0.0.1", "localhost"):
        print("  on your LAN:   http://%s:%d" % (lan_ip(), port))
        print("  read-only: anyone on your network can VIEW it, "
              "nobody can trade from it")
    print("  stop with Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--user", default="testuser")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    a = p.parse_args()
    serve(a.user, a.host, a.port)
