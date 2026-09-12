"""Market-structure features shared by the technical agent and signal engine.

IMPORTANT LABELING (project-wide): the FVG (fair value gap), killzone/session,
and premium/discount concepts below are derived from the ICT methodology —
an UNVERIFIED HEURISTIC FRAMEWORK, not institutionally documented. Every
ICT-derived output carries a "source": "ict_unverified" label, and briefs
repeat the disclaimer. These are facts for analysis output only, never
auto-triggers.
"""


def detect_fvg(bars, max_zones=10):
    """3-candle fair value gaps.

    Bullish FVG at bar i: low[i] > high[i-2] -> gap zone (high[i-2], low[i])
    Bearish FVG at bar i: high[i] < low[i-2] -> gap zone (high[i], low[i-2])
    Returns the most recent `max_zones` zones, oldest first."""
    zones = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        if c["low"] > a["high"]:
            zones.append({
                "type": "bullish", "top": c["low"], "bottom": a["high"],
                "formed": c["timestamp"], "source": "ict_unverified",
            })
        elif c["high"] < a["low"]:
            zones.append({
                "type": "bearish", "top": a["low"], "bottom": c["high"],
                "formed": c["timestamp"], "source": "ict_unverified",
            })
    return zones[-max_zones:]


def premium_discount(bars, window=20):
    """Position of the latest close within the recent high/low range,
    expressed as a 0-1 ratio (0 = at the low, 1 = at the high)."""
    if window < 2 or len(bars) < window:
        raise ValueError("not enough bars for window %d" % window)
    window_bars = bars[-window:]
    hi = max(b["high"] for b in window_bars)
    lo = min(b["low"] for b in window_bars)
    if hi == lo:
        return 0.5
    return (window_bars[-1]["close"] - lo) / (hi - lo)


def swing_structure_label(pivots):
    """Classify the last swing points: HH-HL (uptrend), LH-LL (downtrend),
    mixed, or insufficient_data when fewer than 2 highs / 2 lows exist."""
    highs = [p["price"] for p in pivots if p["kind"] == "high"][-2:]
    lows = [p["price"] for p in pivots if p["kind"] == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient_data"
    hh = highs[1] > highs[0]
    hl = lows[1] > lows[0]
    if hh and hl:
        return "HH-HL"
    if (not hh) and (not hl):
        return "LH-LL"
    return "mixed"


def support_resistance_levels(pivots, ref_price, min_spacing_pct=0.25):
    """Key levels from swing pivots with a minimum-spacing filter: levels
    closer than `min_spacing_pct` percent to an already-kept level are merged
    (running mean) so clusters do not stack up. Returns levels sorted high
    to low, each {"price", "touches", "role"} where role is
    "resistance" (above current price) or "support" (below)."""
    if min_spacing_pct <= 0:
        raise ValueError("min_spacing_pct must be positive")
    prices = sorted((p["price"] for p in pivots), reverse=True)
    merged = []
    for price in prices:
        if merged and abs(merged[-1]["_sum"] / merged[-1]["touches"] - price) \
                <= price * min_spacing_pct / 100.0:
            m = merged[-1]
            m["_sum"] += price
            m["touches"] += 1
            m["price"] = m["_sum"] / m["touches"]
        else:
            merged.append({"price": price, "touches": 1, "_sum": price})
    out = []
    for m in merged:
        out.append({
            "price": round(m["price"], 8),
            "touches": m["touches"],
            "role": "resistance" if m["price"] > ref_price else "support",
        })
    return out
