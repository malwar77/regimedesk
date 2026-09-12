"""Shared test helpers."""
from datetime import datetime, timedelta, timezone


def make_bars(closes, start=None):
    """Build OHLC bar dicts from a close series (hourly, from 2026-01-01)."""
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * 1.0005
        lo = min(o, c) * 0.9995
        bars.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": round(o, 8), "high": round(hi, 8),
            "low": round(lo, 8), "close": round(c, 8),
            "volume": 100.0,
        })
        prev = c
    return bars


def uptrend_closes(n=300, rate=0.0015, start=100.0):
    return [start * ((1 + rate) ** i) for i in range(n)]


def downtrend_closes(n=300, rate=0.0015, start=100.0):
    return [start * ((1 - rate) ** i) for i in range(n)]
