"""Candlestick pattern recognition — standard public-domain TA definitions.

Every function takes raw OHLC values (dicts with open/high/low/close, or a
candle tuple) and returns a structured result. A detected pattern is a
LABELED FACT only — no pattern here implies or recommends a trade.
"""
PIN_BODY_RATIO_MAX = 0.33
DOJI_BODY_RATIO_MAX = 0.10
MARUBOZU_WICK_RATIO_MAX = 0.02


def _ohlc(candle):
    if isinstance(candle, dict):
        return (float(candle["open"]), float(candle["high"]),
                float(candle["low"]), float(candle["close"]))
    o, h, l, c = candle
    return float(o), float(h), float(l), float(c)


def _result(pattern, detected, direction=None, **extra):
    r = {"pattern": pattern, "detected": bool(detected), "direction": direction}
    r.update(extra)
    return r


def is_pin_bar(candle, body_ratio_max=PIN_BODY_RATIO_MAX):
    """Pin bar: body <= body_ratio_max of total range, with the long wick
    opposite the trend side (bullish pin = long lower wick, bearish = long
    upper wick; 'minimal wick on the trend side' expressed as the dominant
    wick being at least twice the other)."""
    o, h, l, c = _ohlc(candle)
    rng = h - l
    if rng <= 0:
        return _result("pin_bar", False)
    body = abs(c - o)
    if body > body_ratio_max * rng:
        return _result("pin_bar", False)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if lower >= 2 * upper and lower > 0:
        return _result("pin_bar", True, "bullish", body=body, range=rng,
                       upper_wick=upper, lower_wick=lower)
    if upper >= 2 * lower and upper > 0:
        return _result("pin_bar", True, "bearish", body=body, range=rng,
                       upper_wick=upper, lower_wick=lower)
    return _result("pin_bar", False)


def is_engulfing(prev_candle, curr_candle):
    """Bullish engulfing: prev is bearish, curr is bullish, curr body engulfs
    the prev body (curr.open <= prev.close and curr.close >= prev.open, with
    curr body strictly larger). Mirrored for bearish."""
    po, ph, pl, pc = _ohlc(prev_candle)
    co, ch, cl, cc = _ohlc(curr_candle)
    prev_body = abs(pc - po)
    curr_body = abs(cc - co)
    if prev_body == 0 or curr_body <= prev_body:
        return _result("engulfing", False)
    if pc < po and cc > co and co <= pc and cc >= po:
        return _result("engulfing", True, "bullish",
                       prev_body=prev_body, curr_body=curr_body)
    if pc > po and cc < co and co >= pc and cc <= po:
        return _result("engulfing", True, "bearish",
                       prev_body=prev_body, curr_body=curr_body)
    return _result("engulfing", False)


def is_doji(candle, body_ratio_max=DOJI_BODY_RATIO_MAX):
    o, h, l, c = _ohlc(candle)
    rng = h - l
    if rng <= 0:
        return _result("doji", False)
    body = abs(c - o)
    return _result("doji", body <= body_ratio_max * rng, None,
                   body=body, range=rng)


def is_marubozu(candle, wick_ratio_max=MARUBOZU_WICK_RATIO_MAX):
    """Marubozu: body fills essentially the whole range; both wicks <=
    wick_ratio_max of the range. Direction from close vs open."""
    o, h, l, c = _ohlc(candle)
    rng = h - l
    if rng <= 0:
        return _result("marubozu", False)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if upper > wick_ratio_max * rng or lower > wick_ratio_max * rng:
        return _result("marubozu", False)
    direction = "bullish" if c > o else ("bearish" if c < o else None)
    return _result("marubozu", direction is not None, direction,
                   upper_wick=upper, lower_wick=lower)


def _mid_body(c):
    o, _, _, cl = _ohlc(c)
    return (o + cl) / 2.0


def morning_star(candle_sequence):
    """Three-candle morning star:
    1) bearish candle with a real body,
    2) small-bodied candle (body <= half of candle 1's body),
    3) bullish candle closing above the midpoint of candle 1's body."""
    if len(candle_sequence) < 3:
        return _result("morning_star", False)
    c0, c1, c2 = (_ohlc(x) for x in candle_sequence[-3:])
    o0, _, _, cl0 = c0
    o1, _, _, cl1 = c1
    o2, _, _, cl2 = c2
    body0 = abs(cl0 - o0)
    body1 = abs(cl1 - o1)
    if body0 == 0 or body1 > 0.5 * body0:
        return _result("morning_star", False)
    bullish_last = cl2 > o2
    closes_above_mid = cl2 > _mid_body(candle_sequence[-3])
    detected = (cl0 < o0) and bullish_last and closes_above_mid
    return _result("morning_star", detected, "bullish" if detected else None)


def evening_star(candle_sequence):
    """Mirror of morning star: bullish candle, small body, bearish candle
    closing below the midpoint of candle 1's body."""
    if len(candle_sequence) < 3:
        return _result("evening_star", False)
    o0, _, _, cl0 = _ohlc(candle_sequence[-3])
    o1, _, _, cl1 = _ohlc(candle_sequence[-2])
    o2, _, _, cl2 = _ohlc(candle_sequence[-1])
    body0 = abs(cl0 - o0)
    body1 = abs(cl1 - o1)
    if body0 == 0 or body1 > 0.5 * body0:
        return _result("evening_star", False)
    bearish_last = cl2 < o2
    closes_below_mid = cl2 < _mid_body(candle_sequence[-3])
    detected = (cl0 > o0) and bearish_last and closes_below_mid
    return _result("evening_star", detected, "bearish" if detected else None)


def scan_patterns(bars):
    """Run all detectors on the current and previous bar. Returns a list of
    detected-pattern facts (empty patterns excluded)."""
    if len(bars) < 3:
        return []
    found = []
    curr, prev = bars[-1], bars[-2]
    checks = [
        ("current_bar", is_pin_bar(curr)),
        ("current_bar", is_doji(curr)),
        ("current_bar", is_marubozu(curr)),
        ("previous_bar", is_pin_bar(prev)),
        ("previous_bar", is_doji(prev)),
        ("previous_bar", is_marubozu(prev)),
        ("current_bar", is_engulfing(prev, curr)),
    ]
    for bar_label, res in checks:
        if res["detected"]:
            found.append({"bar": bar_label, "pattern": res["pattern"],
                          "direction": res["direction"]})
    for name, fn in (("morning_star", morning_star),
                     ("evening_star", evening_star)):
        res = fn(bars[-3:])
        if res["detected"]:
            found.append({"bar": "last_three", "pattern": name,
                          "direction": res["direction"]})
    return found
