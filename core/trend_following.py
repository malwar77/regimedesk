"""Trend-following core — the PRIMARY signal basis of RegimeDesk.

Evidence-backed, multi-market trend following (time-series momentum, price
vs moving averages, Donchian breakout, trend-strength gating, and
volatility-scaled position sizing). Pure functions, unit-tested against
hand-calculated reference values.

ICT features, candlestick patterns and S/R levels are INPUT CONTEXT only —
never the sole basis for a signal proposal.
"""
import math


def time_series_momentum(returns, lookback_months=(1, 3, 6, 12)):
    """Average sign of the cumulative return over each lookback.

    `returns` is a series of period returns at whatever frequency the caller
    supplies; `lookback_months` is interpreted in units of that same series
    (supply monthly returns for the classic 1/3/6/12-month formulation).
    Returns a signal strength in [-1, 1]. Raises if no lookback is testable.
    """
    if not returns:
        raise ValueError("returns must be non-empty")
    sigs = []
    for lb in lookback_months:
        if lb <= 0:
            raise ValueError("lookbacks must be positive")
        if len(returns) < lb:
            continue
        cum = sum(returns[-lb:])
        sigs.append(1.0 if cum > 0 else (-1.0 if cum < 0 else 0.0))
    if not sigs:
        raise ValueError("not enough data for any lookback")
    return sum(sigs) / len(sigs)


def _sma_at(prices, period, end):
    window = prices[end - period:end]
    if len(window) < period:
        return None
    return sum(window) / period


def ma_signal(prices, windows=(50, 100, 200)):
    """Price vs simple moving averages. Returns
    {"signal": mean of +1/-1 per window (above/below), in [-1,1],
     "windows": {window: +1/-1}} for windows with enough data."""
    if len(prices) < 2:
        raise ValueError("need at least 2 prices")
    last = prices[-1]
    detail = {}
    for w in windows:
        if w <= 0:
            raise ValueError("windows must be positive")
        if len(prices) < w:
            continue
        ma = _sma_at(prices, w, len(prices))
        detail[w] = 1 if last > ma else (-1 if last < ma else 0)
    if not detail:
        raise ValueError("not enough data for any MA window")
    return {"signal": sum(detail.values()) / len(detail), "windows": detail}


def donchian_breakout(prices, window=20):
    """Breakout of the PRIOR `window`-bar channel (channel excludes the
    current bar — no lookahead). Returns
    {"direction": "up"|"down"|"inside", "distance": abs price distance}."""
    if window < 1:
        raise ValueError("window must be >= 1")
    if len(prices) < window + 1:
        raise ValueError("need at least window+1 prices")
    channel = prices[-(window + 1):-1]
    upper, lower = max(channel), min(channel)
    last = prices[-1]
    if last > upper:
        return {"direction": "up", "distance": last - upper}
    if last < lower:
        return {"direction": "down", "distance": lower - last}
    return {"direction": "inside", "distance": 0.0}


def trend_strength(prices, window):
    """R-squared of an OLS regression of the last `window` prices on time.
    Returns {"slope": ..., "r2": ...}. r2 in [0,1]; flat series -> 0.0."""
    if window < 2:
        raise ValueError("window must be >= 2")
    if len(prices) < window:
        raise ValueError("not enough data for window %d" % window)
    from core.indicators import linear_regression
    slope, r2 = linear_regression(prices[-window:])
    return {"slope": slope, "r2": r2}


def realized_volatility(returns, window=24):
    """Standard deviation of the last `window` returns (sample stdev)."""
    if window < 2 or len(returns) < window:
        raise ValueError("not enough returns")
    r = returns[-window:]
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var)


def volatility_scaled_position(signal, realized_vol, target_vol, cap=1.0):
    """Volatility-targeted position weight.

    weight = signal * (target_vol / realized_vol), clamped to [-cap, cap].
    A non-positive realized_vol returns 0.0 (cannot scale safely).
    """
    if realized_vol <= 0:
        return 0.0
    weight = signal * (target_vol / realized_vol)
    return max(-cap, min(cap, weight))
