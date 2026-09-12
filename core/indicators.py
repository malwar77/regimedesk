"""Core indicator math. Pure functions, stdlib only, independently unit-testable.

Conventions:
- price series are plain lists of floats, oldest first
- EMA is seeded with the SMA of the first `period` values (standard)
- RSI uses Wilder's smoothing
- ATR uses Wilder's smoothing
"""
import math


def sma(values, period):
    if period < 1 or len(values) < period:
        raise ValueError("not enough data for SMA")
    return sum(values[-period:]) / period


def ema_series(values, period):
    """EMA seeded with SMA of the first `period` values. Returns a list aligned
    with `values`; entries before the seed index are None."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(values) < period:
        raise ValueError("not enough data for EMA of period %d" % period)
    k = 2.0 / (period + 1)
    out = [None] * len(values)
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def ema(values, period):
    return ema_series(values, period)[-1]


def rsi(closes, period=14):
    """Wilder's RSI. Requires at least period+1 closes."""
    if period < 1 or len(closes) < period + 1:
        raise ValueError("not enough data for RSI(%d)" % period)
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0  # no movement at all -> neutral by convention
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 * rs / (1.0 + rs)


def rsi_state(value, overbought=70.0, oversold=30.0):
    if value > overbought:
        return "overbought"
    if value < oversold:
        return "oversold"
    return "neutral"


def atr(highs, lows, closes, period=14):
    """Wilder's ATR. Requires at least period+1 bars."""
    if len(closes) < period + 1:
        raise ValueError("not enough data for ATR(%d)" % period)
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def classify_slope(series, lookback=10, rel_threshold=0.001):
    """Classify a numeric series' tail as rising/falling/flat.

    Compares the last value with the value `lookback` points earlier.
    `rel_threshold` is a relative dead-band so micro-noise reads as flat.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < lookback + 1:
        raise ValueError("not enough data to classify slope over %d periods" % lookback)
    a, b = vals[-1], vals[-1 - lookback]
    if b > 0:
        if a > b * (1 + rel_threshold):
            return "rising"
        if a < b * (1 - rel_threshold):
            return "falling"
        return "flat"
    return "rising" if a > b else ("falling" if a < b else "flat")


def linear_regression(values):
    """OLS of values against their index. Returns (slope, r_squared)."""
    n = len(values)
    if n < 2:
        raise ValueError("need at least 2 points")
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in values)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    if sxx == 0:
        raise ValueError("degenerate x")
    slope = sxy / sxx
    if syy == 0:
        # zero variance: perfectly flat, no trend information
        return 0.0, 0.0
    r2 = (sxy * sxy) / (sxx * syy)
    return slope, r2


def swing_pivots(bars, k=2):
    """Fractal swing pivots. A pivot high at i has high[i] strictly greater than
    the highs of the k bars on each side (same rule, mirrored, for lows).

    `bars` are dicts with "high", "low", "timestamp".
    Returns [{"index", "kind": "high"|"low", "price", "timestamp"}] in order.
    """
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    out = []
    for i in range(k, len(bars) - k):
        window = list(range(i - k, i)) + list(range(i + 1, i + k + 1))
        if all(highs[i] > highs[j] for j in window):
            out.append({"index": i, "kind": "high", "price": highs[i],
                        "timestamp": bars[i]["timestamp"]})
        if all(lows[i] < lows[j] for j in window):
            out.append({"index": i, "kind": "low", "price": lows[i],
                        "timestamp": bars[i]["timestamp"]})
    return out
