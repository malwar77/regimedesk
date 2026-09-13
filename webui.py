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
<title>RegimeDesk — Terminal</title>
<style>
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
@media (prefers-reduced-motion:reduce) { .live-dot { animation:none; } }
</style></head><body>
<header><div>
  <h1><span class="live-dot"></span>REGIME<span class="desk">DESK</span></h1>
  <div class="sub">read-only terminal &middot; execution stays behind the RiskManager</div>
</div><div class="sub">user: <span id="user" class="blue">...</span>
  &middot; mode: <span id="mode" class="blue">...</span>
  &middot; updated <span id="updated" class="blue">...</span></div></header>
<div id="ks-banner" class="banner"></div>
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
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    user_id = "testuser"

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            payload = build_status(self.user_id)
            body = json.dumps(payload, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

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
