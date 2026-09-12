"""RegimeDesk technical agent.

Fact-based per-instrument trend/structure output written to
briefs/technical_YYYYMMDD.md. NO trade recommendations — every section is a
labeled fact. ICT-derived fields (FVG, killzones, premium/discount) are
explicitly labeled as coming from an unverified heuristic framework,
consistent with how this project treats other unverified sources.

trend_state shape (headline, per timeframe):
{"trend": "bullish"|"bearish"|"neutral",
 "ema_slope": {"20": {"value", "slope"}, "50": ..., "200": ...},
 "rsi": {"value", "state"},
 "structure": "HH-HL"|"LH-LL"|"range"|"mixed",
 "confidence": 0..1}

confidence is deterministic: |sum of votes| / 4 where votes come from the
three EMA slopes (rising +1, falling -1, flat 0) and swing structure
(HH-HL +1, LH-LL -1, else 0). trend is bullish at sum >= 2, bearish at
sum <= -2, otherwise neutral.
"""
import json
import os
from datetime import datetime, timezone

from core import candlestick_patterns as cp
from core import indicators as ind
from core.data_pipeline import DataPipeline, resample_h1_to_h4
from core.regime_engine import classify_regime
from core.structure import (detect_fvg, premium_discount,
                            swing_structure_label, support_resistance_levels)

ICT_DISCLAIMER = ("FVG / killzone / premium-discount fields are derived from "
                  "an unverified heuristic framework (ICT), not "
                  "institutionally documented.")

SLOPE_VOTE = {"rising": 1, "falling": -1, "flat": 0}
STRUCTURE_VOTE = {"HH-HL": 1, "LH-LL": -1}


def load_watchlist(path="watchlist.yaml"):
    if not os.path.exists(path):
        raise FileNotFoundError("watchlist.yaml not found")
    from core.config_loader import parse_simple_yaml
    data = parse_simple_yaml(open(path).read())
    instruments = data.get("instruments", "")
    return [s.strip() for s in str(instruments).split(",") if s.strip()]


def analyze_timeframe(bars, pip_size=0.0001, min_spacing_pct=0.25):
    """All technical facts for one timeframe. Requires >= 210 bars
    (EMA200 + slope lookback)."""
    if len(bars) < 210:
        raise ValueError("need at least 210 bars (got %d)" % len(bars))
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    ema_slope = {}
    for period in (20, 50, 200):
        series = ind.ema_series(closes, period)
        ema_slope[str(period)] = {
            "value": round(series[-1], 8),
            "slope": ind.classify_slope(series, lookback=10),
        }

    rsi_value = ind.rsi(closes, period=14)
    rsi = {"value": round(rsi_value, 2),
           "state": ind.rsi_state(rsi_value)}  # overbought >70 / oversold <30

    pivots = ind.swing_pivots(bars, k=2)
    structure_raw = swing_structure_label(pivots)
    structure = structure_raw if structure_raw in ("HH-HL", "LH-LL", "mixed") \
        else "range" if structure_raw == "insufficient_data" else structure_raw
    structure = "range" if structure == "insufficient_data" else structure

    sr = support_resistance_levels(pivots, closes[-1],
                                   min_spacing_pct=min_spacing_pct)

    fvg_zones = detect_fvg(bars)
    premium = premium_discount(bars, window=20)
    session = None  # filled by caller (depends on the bar's timestamp)
    candle_flags = cp.scan_patterns(bars)
    atr = ind.atr(highs, lows, closes, period=14)

    # deterministic confidence / trend combination
    votes = [SLOPE_VOTE.get(ema_slope[k]["slope"], 0) for k in ("20", "50", "200")]
    votes.append(STRUCTURE_VOTE.get(structure_raw, 0))
    score = sum(votes)
    confidence = round(abs(score) / len(votes), 4)
    trend = "bullish" if score >= 2 else ("bearish" if score <= -2 else "neutral")

    trend_state = {
        "trend": trend,
        "ema_slope": ema_slope,
        "rsi": rsi,
        "structure": structure,
        "confidence": confidence,
    }
    regime = classify_regime({
        "ema_slopes": {k: v["slope"] for k, v in ema_slope.items()},
        "trend_strength": {"r2": ind.linear_regression(closes[-50:])[1]},
        "donchian": {"direction": "inside"},
    })
    return {
        "trend_state": trend_state,
        "swing_structure_raw": structure_raw,
        "support_resistance": sr,
        "fvg_zones": fvg_zones,
        "premium_discount": round(premium, 4),
        "candlestick_flags": candle_flags,
        "atr": round(atr, 8),
        "last_close": closes[-1],
        "last_bar_timestamp": bars[-1]["timestamp"],
        "regime": regime,
        "pivots": pivots,
    }


def analyze_instrument(instrument, pipeline, pip_size=0.0001,
                       min_spacing_pct=0.25):
    h1 = pipeline.fetch(instrument, "H1", count=300)
    h1_feats = analyze_timeframe(h1, pip_size, min_spacing_pct)
    h4 = resample_h1_to_h4(h1)
    h4_feats = None
    if len(h4) >= 210:
        h4_feats = analyze_timeframe(h4, pip_size, min_spacing_pct)
    from core.sessions import session_context
    session = session_context(h1[-1]["timestamp"])
    h1_feats["session_context"] = session
    if h4_feats:
        h4_feats["session_context"] = session
    return {"instrument": instrument, "H1": h1_feats, "H4": h4_feats,
            "session_context": session}


def _fmt_sr(levels):
    if not levels:
        return "none detected"
    return "; ".join("%s %s (%d touches)"
                     % (l["role"], round(l["price"], 6), l["touches"])
                     for l in levels)


def write_brief(results, out_dir="briefs", date=None):
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "technical_%s.md" % date)
    lines = [
        "# Technical Brief — %s" % date,
        "",
        "Fact-based market-structure output. No trade recommendations.",
        "",
        "> %s" % ICT_DISCLAIMER,
        "",
    ]
    for res in results:
        lines.append("## %s" % res["instrument"])
        for tf in ("H1", "H4"):
            feats = res.get(tf)
            if feats is None:
                lines.append("_%s: insufficient data (needs 210 bars)_" % tf)
                continue
            ts = feats["trend_state"]
            lines.append("")
            lines.append("### %s" % tf)
            lines.append("trend_state: `%s`" % json.dumps(ts, sort_keys=True))
            for p in ("20", "50", "200"):
                e = ts["ema_slope"][p]
                lines.append("- EMA%s: %.8g (%s)"
                             % (p, e["value"], e["slope"]))
            lines.append("- RSI(14): %.2f — %s"
                         % (ts["rsi"]["value"], ts["rsi"]["state"]))
            lines.append("- swing structure: %s" % ts["structure"])
            lines.append("- support/resistance: %s"
                         % _fmt_sr(feats["support_resistance"]))
            lines.append("- last close: %.8g at %s"
                         % (feats["last_close"], feats["last_bar_timestamp"]))
            if tf == "H1":
                if feats.get("fvg_zones"):
                    fz = feats["fvg_zones"][-3:]
                    lines.append("- FVG zones (recent): %s"
                                 % "; ".join("%s %s-%s formed %s"
                                             % (z["type"],
                                                round(z["bottom"], 6),
                                                round(z["top"], 6),
                                                z["formed"]) for z in fz))
                else:
                    lines.append("- FVG zones: none detected")
                sc = feats["session_context"]
                lines.append("- session context: %s (%s NY time)"
                             % (sc["label"], sc["ny_time"][11:16]))
                lines.append("- premium/discount: %.3f "
                             "(0=at 20-bar low, 1=at 20-bar high)"
                             % feats["premium_discount"])
                flags = feats["candlestick_flags"]
                lines.append("- candlestick flags: %s"
                             % ("; ".join("%s (%s) on %s"
                                          % (c["pattern"], c["direction"],
                                             c["bar"]) for c in flags)
                                or "none"))
                lines.append("- regime: %s"
                             % feats["regime"]["regime"])
        lines.append("")
    lines.append("---")
    lines.append("All sections are facts, not signals. Nothing in this brief "
                 "is a trade recommendation or an expectation of gains.")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def run(pipeline=None, date=None, watchlist_path="watchlist.yaml",
        out_dir="briefs"):
    """Run the technical agent over the watchlist and write the daily brief.
    Returns (brief_path, results)."""
    pipeline = pipeline or DataPipeline(live=False, source="synthetic")
    instruments = load_watchlist(watchlist_path)
    results = [analyze_instrument(i, pipeline) for i in instruments]
    path = write_brief(results, out_dir=out_dir, date=date)
    return path, results
