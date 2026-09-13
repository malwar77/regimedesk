"""Web UI tests — stdlib dashboard, design system, read-only stance.

Hand-verified expectations:
- build_status reads recorded facts (regime snapshot, today's signals,
  journal orders, kill-switch distance) and always carries the
  honesty disclaimer
- the page ships the terminal design system (black bg, green/red/blue
  palette, pulse animation, 5s auto-refresh)
- GET / and GET /api/status work; unknown paths 404; nothing exposes
  trade controls
- lan_ip() never raises and returns a non-empty string
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui


def _make_env():
    """Temporary account/logs/state tree with known content."""
    tmp = tempfile.mkdtemp()
    acc = os.path.join(tmp, "accounts"); os.makedirs(acc)
    logs = os.path.join(tmp, "logs"); os.makedirs(logs)
    state = os.path.join(tmp, "state"); os.makedirs(state)
    with open(os.path.join(acc, "dash.yaml"), "w") as f:
        f.write("user_id: dash\nmode: paper\n")
    with open(os.path.join(logs, "regime_latest.json"), "w") as f:
        json.dump({"regime": "BULL_TREND", "timestamp": "2026-09-13T08:00:00"}, f)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    with open(os.path.join(logs, "live_signals_%s.jsonl" % today), "w") as f:
        f.write(json.dumps({"timestamp": "2026-09-13T08:05:00Z",
                            "direction": "long", "instrument": "BTC_USD",
                            "combined_strength": 0.42,
                            "executed": False}) + "\n")
    with open(os.path.join(state, "chief_latest.json"), "w") as f:
        json.dump({"rank": "HIGH", "note": "two agents agree"}, f)
    return tmp, acc, logs, state


class BuildStatusTests(unittest.TestCase):
    def test_payload_shape_and_disclaimer(self):
        tmp, acc, logs, state = _make_env()
        s = webui.build_status("dash", accounts_dir=acc, logs_dir=logs,
                               state_dir=state)
        self.assertEqual(s["user"], "dash")
        self.assertEqual(s["mode"], "paper")
        self.assertEqual(s["regime"]["regime"], "BULL_TREND")
        self.assertEqual(s["signals_today"], 1)
        self.assertEqual(s["recent_signals"][0]["instrument"], "BTC_USD")
        self.assertEqual(s["chief"]["rank"], "HIGH")
        self.assertIn("including losses", s["disclaimer"])

    def test_missing_everything_degrades_gracefully(self):
        tmp = tempfile.mkdtemp()
        s = webui.build_status("ghost", accounts_dir=os.path.join(tmp, "acc"),
                              logs_dir=os.path.join(tmp, "logs"),
                              state_dir=os.path.join(tmp, "state"))
        self.assertEqual(s["mode"], "unknown")
        self.assertIn("account_error", s)
        self.assertEqual(s["signals_today"], 0)
        self.assertIsNone(s["regime"])


class PageDesignTests(unittest.TestCase):
    def test_design_tokens(self):
        for token in ("--bg:#04070a", "--green:#00e68a", "--red:#ff4d5e",
                      "--blue:#4da3ff"):
            self.assertIn(token, webui.PAGE)
        self.assertIn("@keyframes pulse", webui.PAGE)
        self.assertIn("setInterval(refresh, 5000)", webui.PAGE)

    def test_execution_boundary_stated(self):
        self.assertIn("execution stays behind the RiskManager", webui.PAGE)
        self.assertNotIn("/api/trade", webui.PAGE)

    def test_live_market_layer_present(self):
        self.assertIn("lightweight-charts.standalone.production.js",
                      webui.PAGE)
        self.assertIn("addCandlestickSeries", webui.PAGE)
        self.assertIn("/api/candles", webui.PAGE)


class LiveDataTests(unittest.TestCase):
    """Live market layer: keyless sources, TTL cache, honest errors,
    fallback chain. All network calls are mocked — no real requests
    in the test suite."""

    def setUp(self):
        webui._market_cache.clear()

    def test_binance_first(self):
        from unittest.mock import patch

        def fake_binance(base, quote, tf, limit):
            return [{"time": 1, "open": 1, "high": 1, "low": 1,
                     "close": 2, "volume": 5}]
        with patch.object(webui, "_binance_candles", fake_binance), \
                patch.object(webui, "_kraken_candles",
                             side_effect=AssertionError("kraken used")):
            out = webui.fetch_candles("BTC_USD", "1h", 10)
            self.assertEqual(out["source"], "binance")
            self.assertEqual(out["candles"][0]["close"], 2)

    def test_kraken_fallback(self):
        from unittest.mock import patch

        def boom(base, quote, tf, limit):
            raise RuntimeError("451 geo-blocked")
        fake = [{"time": 1, "open": 1, "high": 1, "low": 1,
                 "close": 3, "volume": 5}]
        with patch.object(webui, "_binance_candles", boom), \
                patch.object(webui, "_kraken_candles",
                             lambda b, q, tf, limit: fake):
            out = webui.fetch_candles("BTC_USD", "1h", 10)
            self.assertEqual(out["source"], "kraken")
            self.assertEqual(out["candles"][0]["close"], 3)

    def test_all_sources_down_raises(self):
        from unittest.mock import patch

        def boom(base, quote, tf, limit):
            raise RuntimeError("down")
        with patch.object(webui, "_binance_candles", boom), \
                patch.object(webui, "_kraken_candles", boom):
            with self.assertRaises(RuntimeError) as ctx:
                webui.fetch_candles("BTC_USD", "1h", 10)
            # honest error naming the sources
            self.assertIn("binance", str(ctx.exception))
            self.assertIn("kraken", str(ctx.exception))

    def test_ttl_cache_no_refetch(self):
        from unittest.mock import patch
        calls = []

        def fake_binance(base, quote, tf, limit):
            calls.append(1)
            return [{"time": 1, "open": 1, "high": 1, "low": 1,
                     "close": 2, "volume": 5}]
        with patch.object(webui, "_binance_candles", fake_binance):
            webui.fetch_candles("BTC_USD", "1h", 10)
            webui.fetch_candles("BTC_USD", "1h", 10)
        self.assertEqual(len(calls), 1)

    def test_kraken_pair_mapping(self):
        # BTC maps to Kraken's XBT naming
        import inspect
        self.assertIn("XBTUSD", inspect.getsource(webui._kraken_candles))


class LiveEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = webui.ThreadingHTTPServer(("127.0.0.1", 8893),
                                               webui._Handler)
        webui._Handler.user_id = "testuser"
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_chart_library_served(self):
        js = urllib.request.urlopen(
            "http://127.0.0.1:8893/static/"
            "lightweight-charts.standalone.production.js").read()
        self.assertGreater(len(js), 100000)
        self.assertIn(b"TradingView", js)

    def test_candles_endpoint_honest_503_when_offline(self):
        from unittest.mock import patch

        def boom(base, quote, tf, limit):
            raise RuntimeError("down")
        with patch.object(webui, "_binance_candles", boom), \
                patch.object(webui, "_kraken_candles", boom):
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:8893/api/candles")
                self.fail("expected HTTP 503")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 503)
                body = json.loads(e.read().decode())
                self.assertIn("unavailable", body["error"])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.acc, cls.logs, cls.state = _make_env()
        # build_status inside the handler uses default dirs; for the
        # smoke test we accept the real repo's (empty) data — the
        # handler only reads, never writes.
        import webui as w
        w._Handler.user_id = "testuser"
        cls.server = w.ThreadingHTTPServer(("127.0.0.1", 8891), w._Handler)
        cls.port = 8891
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_index_served(self):
        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % self.port).read().decode()
        self.assertIn("REGIME", html)
        self.assertIn("setInterval(refresh, 5000)", html)

    def test_status_json(self):
        data = json.load(urllib.request.urlopen(
            "http://127.0.0.1:%d/api/status" % self.port))
        self.assertIn("disclaimer", data)
        self.assertIn("generated_at", data)

    def test_404_for_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                "http://127.0.0.1:%d/api/trade" % self.port)
        self.assertEqual(ctx.exception.code, 404)


class LanIpTests(unittest.TestCase):
    def test_never_raises(self):
        for _ in range(3):
            self.assertTrue(len(webui.lan_ip()) > 0)


if __name__ == "__main__":
    unittest.main()
