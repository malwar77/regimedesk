"""Data pipeline: historical/backtest and live OHLCV access behind one interface.

Mode flag:
- live=False  -> historical/backtest data (synthetic deterministic series,
                 or a recorded fixture)
- live=True   -> real-time data from configured feeds:
                 source="oanda" (REST v3, forex) or source="ccxt" (crypto),
                 or source="recorded" (replay of a recorded live session —
                 used by the live-mode integration test)

Live feeds are fetched lazily and require credentials via environment
variables (OANDA_API_KEY / OANDA_ENV, or the ccxt exchange's own env keys).
Nothing here is needed for the unit tests, which run on recorded/synthetic
data only.

Bar format (normalized everywhere):
{"timestamp": ISO-8601 UTC string, "open", "high", "low", "close", "volume"}
"""
import json
import os
import random
import hashlib
from datetime import datetime, timedelta, timezone

TIMEFRAMES = {"M15": 15 * 60, "H1": 3600, "H4": 4 * 3600, "D1": 86400}

BASE_PRICES = {"EUR_USD": 1.0850, "GBP_USD": 1.2700, "BTC_USD": 77000.0}


class PipelineError(Exception):
    pass


def _iso(epoch_seconds):
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _seed_for(instrument):
    return int(hashlib.md5(instrument.encode("utf-8")).hexdigest()[:12], 16)


class DataPipeline:
    def __init__(self, live=False, source="synthetic", path=None,
                 poll_seconds=60):
        if not live and source in ("oanda", "ccxt"):
            raise PipelineError(
                "source %r requires live=True (real-time feed)" % source)
        if live and source not in ("oanda", "ccxt", "recorded"):
            raise PipelineError("unknown live source %r" % source)
        self.live = live
        self.source = source
        self.path = path
        self.poll_seconds = poll_seconds

    # ---------------- historical / backtest ----------------

    def fetch(self, instrument, timeframe="H1", count=300):
        """Return the most recent `count` bars, oldest first."""
        if timeframe not in TIMEFRAMES:
            raise PipelineError("unsupported timeframe %r" % timeframe)
        if self.source == "synthetic":
            return self._synthetic(instrument, timeframe, count)
        if self.source == "recorded":
            return self._recorded(instrument, timeframe, count)
        if self.source == "oanda":
            return self._oanda_rest(instrument, timeframe, count)
        if self.source == "ccxt":
            return self._ccxt_ohlcv(instrument, timeframe, count)
        raise PipelineError("unknown source %r" % self.source)

    def stream(self, instrument, timeframe, on_bar, max_bars=None):
        """Deliver bars one at a time, oldest first.

        recorded: replays the session bar by bar (each delivery contains
        ONLY bars up to the current one — the basis of the no-lookahead
        guarantee). Live feeds: polls the latest candle and delivers only
        bars not yet seen. Synthetic: yields the series."""
        bars = self.fetch(instrument, timeframe, count=10 ** 6
                          if self.source == "recorded" else
                          (max_bars or 300))
        delivered = 0
        for i, bar in enumerate(bars):
            on_bar(bars[: i + 1])  # callback sees the full prefix, nothing more
            delivered += 1
            if max_bars and delivered >= max_bars:
                return

    # ---------------- sources ----------------

    def _synthetic(self, instrument, timeframe, count):
        """Deterministic pseudo-random walk (seeded per instrument) for
        backtest/development use. NOT live data."""
        rng = random.Random(_seed_for(instrument))
        step = TIMEFRAMES[timeframe]
        price = BASE_PRICES.get(instrument, 100.0)
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        bars = []
        for i in range(count):
            drift = 0.00004
            ret = rng.gauss(drift, 0.0015)
            o = price
            c = o * (1 + ret)
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0006)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0006)))
            bars.append({
                "timestamp": (start + timedelta(seconds=step * i)).isoformat(),
                "open": round(o, 6 if price < 10 else 2),
                "high": round(hi, 6 if price < 10 else 2),
                "low": round(lo, 6 if price < 10 else 2),
                "close": round(c, 6 if price < 10 else 2),
                "volume": float(int(abs(rng.gauss(500, 120)))),
            })
            price = c
        return bars

    def _recorded(self, instrument, timeframe, count):
        with open(self.path) as f:
            data = json.load(f)
        bars = []
        for b in data["bars"]:
            ts = b["timestamp"]
            if isinstance(ts, (int, float)):
                ts = _iso(ts)
            bars.append({"timestamp": ts, "open": float(b["open"]),
                         "high": float(b["high"]), "low": float(b["low"]),
                         "close": float(b["close"]),
                         "volume": float(b.get("volume", 0.0))})
        if timeframe == "H4":
            bars = resample_h1_to_h4(bars)
        return bars[-count:]

    def _oanda_rest(self, instrument, timeframe, count):
        """OANDA v3 REST candles (documented endpoint). The API key comes
        from the OANDA_API_KEY env var and is never logged."""
        import urllib.request

        api_key = os.environ.get("OANDA_API_KEY")
        if not api_key:
            raise PipelineError("OANDA_API_KEY not set")
        env = os.environ.get("OANDA_ENV", "practice")
        host = ("api-fxpractice.oanda.com" if env == "practice"
                else "api-fxtrade.oanda.com")
        symbol = instrument.replace("_", "-")
        granularity = {"M15": "M15", "H1": "H1", "H4": "H4", "D1": "D"}[timeframe]
        url = ("https://%s/v3/instruments/%s/candles?granularity=%s"
               "&price=M&count=%d" % (host, symbol, granularity, count))
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + api_key})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        bars = []
        for c in payload.get("candles", []):
            if not c.get("complete", False):
                continue
            mid = c["mid"]
            bars.append({"timestamp": c["time"], "open": float(mid["o"]),
                         "high": float(mid["h"]), "low": float(mid["l"]),
                         "close": float(mid["c"]), "volume": float(c["volume"])})
        return bars

    def _ccxt_ohlcv(self, instrument, timeframe, count):
        """Crypto OHLCV via ccxt (lazy import; exchange from CCXT_EXCHANGE,
        default 'coinbase')."""
        try:
            import ccxt
        except ImportError:
            raise PipelineError("ccxt is not installed (pip install ccxt)")
        exchange_id = os.environ.get("CCXT_EXCHANGE", "coinbase")
        exchange = getattr(ccxt, exchange_id)()
        tf = {"M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}[timeframe]
        symbol = instrument.replace("_", "/")
        raw = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=count)
        return [{"timestamp": _iso(r[0] / 1000), "open": float(r[1]),
                 "high": float(r[2]), "low": float(r[3]),
                 "close": float(r[4]), "volume": float(r[5])} for r in raw]


def resample_h1_to_h4(bars):
    """Aggregate H1 bars into H4 bars on 4-hour boundaries (00,04,08,...)."""
    out = []
    chunk = []
    for bar in bars:
        hour = datetime.fromisoformat(bar["timestamp"]).hour
        if chunk and hour % 4 == 0:
            out.append(_aggregate(chunk))
            chunk = []
        chunk.append(bar)
    if chunk:
        out.append(_aggregate(chunk))
    return out


def _aggregate(chunk):
    return {
        "timestamp": chunk[0]["timestamp"],
        "open": chunk[0]["open"],
        "high": max(b["high"] for b in chunk),
        "low": min(b["low"] for b in chunk),
        "close": chunk[-1]["close"],
        "volume": sum(b.get("volume", 0.0) for b in chunk),
    }
