"""Exit-strategy math: chandelier stop, parabolic SAR, Fibonacci extension
targets, swing-pivot targets. Pure deterministic functions; unit-tested
against hand-calculated reference values.

These produce PRICE LEVELS for analysis output and signal proposals only.
The RiskManager's own stop-loss and position-sizing rules always keep final
veto — nothing here overrides them.
"""


def chandelier_stop(highs, atr, period=22, multiplier=3.0):
    """Long chandelier exit: highest high of the last `period` bars minus
    multiplier * atr. `atr` is passed in (already computed) so the ATR period
    stays explicit and independently testable."""
    if period < 1:
        raise ValueError("period must be >= 1")
    window = highs[-period:]
    if len(window) < period:
        raise ValueError("not enough bars for chandelier period %d" % period)
    if atr <= 0:
        raise ValueError("atr must be positive")
    return max(window) - multiplier * atr


def chandelier_stop_short(lows, atr, period=22, multiplier=3.0):
    """Short chandelier exit: lowest low of the last `period` bars plus
    multiplier * atr."""
    if period < 1:
        raise ValueError("period must be >= 1")
    window = lows[-period:]
    if len(window) < period:
        raise ValueError("not enough bars for chandelier period %d" % period)
    if atr <= 0:
        raise ValueError("atr must be positive")
    return min(window) + multiplier * atr


def parabolic_sar(ohlcv, af_start=0.02, af_step=0.02, af_max=0.2):
    """Textbook parabolic SAR (Wilder).

    `ohlcv` is a list of dicts with high/low/close (open unused).
    Returns a list of stop levels, one per bar: sar[i] is the stop ACTIVE
    DURING bar i (computed strictly from bars before i, so it is usable
    without lookahead).

    Initialization: trend up if close[1] >= close[0]; SAR starts at the
    opposite extreme of bar 0; EP at bar 0's extreme; AF at af_start.
    Clamping (SAR may not sit above/below the prior two bars' lows/highs)
    is applied from i >= 2 onward.
    """
    if len(ohlcv) < 2:
        raise ValueError("need at least 2 bars for parabolic SAR")
    highs = [b["high"] for b in ohlcv]
    lows = [b["low"] for b in ohlcv]
    closes = [b["close"] for b in ohlcv]

    up = closes[1] >= closes[0]
    sar = lows[0] if up else highs[0]
    ep = highs[0] if up else lows[0]
    af = af_start
    out = [sar]

    for i in range(1, len(ohlcv)):
        sar = sar + af * (ep - sar)
        if i >= 2:  # clamp: never inside the prior two bars' extremes
            if up:
                sar = min(sar, lows[i - 1], lows[i - 2])
            else:
                sar = max(sar, highs[i - 1], highs[i - 2])
        reversed_ = False
        if up and lows[i] < sar:
            sar, up, af = ep, False, af_start
            ep = lows[i]
            reversed_ = True
        elif not up and highs[i] > sar:
            sar, up, af = ep, True, af_start
            ep = highs[i]
            reversed_ = True
        if not reversed_:
            if up and highs[i] > ep:
                ep = highs[i]
                af = min(af + af_step, af_max)
            elif not up and lows[i] < ep:
                ep = lows[i]
                af = min(af + af_step, af_max)
        out.append(sar)
    return out


FIB_RATIOS = (0.382, 0.618, 1.0)


def fib_extension_target(swing_low, swing_high, ratio):
    """Fibonacci extension of an impulse, from EXPLICIT swing points.

    The swing points are required inputs — this function never auto-selects
    an impulse wave; callers must supply pivots chosen by an explicit rule.

    Direction is inferred from the price relationship:
    - swing_high > swing_low: bullish impulse (low -> high);
      target = swing_high + ratio * (high - low)
    - swing_high < swing_low: bearish impulse (high -> low);
      target = swing_high - ratio * (low - high)
    """
    if ratio <= 0:
        raise ValueError("ratio must be positive (standard: 0.382/0.618/1.0)")
    distance = abs(swing_high - swing_low)
    if distance == 0:
        raise ValueError("swing points must differ")
    if swing_high > swing_low:
        return swing_high + ratio * distance
    return swing_high - ratio * distance


def swing_pivot_targets(price_history, lookback):
    """Candidate levels from confirmed swing pivots within the last
    `lookback` bars. `price_history` is a bar list (highs/lows/timestamps).
    Returns pivot prices, highest first. Requires the caller to supply an
    explicit lookback; nothing is auto-selected."""
    from core.indicators import swing_pivots

    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    window = price_history[-lookback:]
    if len(window) < lookback:
        raise ValueError("not enough bars for lookback %d" % lookback)
    pivots = swing_pivots(window)
    return sorted((p["price"] for p in pivots), reverse=True)
