"""Regime classification — deterministic, fact-based rules over trend
features. No discretionary judgment; every rule is explicit below."""
import json


def classify_regime(features):
    """features: {
        "ema_slopes": {"20": "rising"|"falling"|"flat", "50": ..., "200": ...},
        "trend_strength": {"r2": float},        # regression R^2
        "donchian": {"direction": "up"|"down"|"inside"}
    }
    Rules (explicit, deterministic):
    - strength gate: r2 < 0.30 -> "range" (no trend worth the name)
    - all three EMA slopes rising -> "bullish_trend"
    - all three EMA slopes falling -> "bearish_trend"
    - otherwise (mixed slopes with enough strength) -> "transition"
    """
    slopes = features["ema_slopes"]
    r2 = float(features["trend_strength"].get("r2", 0.0))
    donchian = features.get("donchian", {}).get("direction", "inside")
    vals = [slopes.get(k, "flat") for k in ("20", "50", "200")]
    reasoning = [
        "EMA slopes: 20=%s, 50=%s, 200=%s" % tuple(vals),
        "trend strength r2=%.3f (gate 0.30)" % r2,
        "donchian channel: %s" % donchian,
    ]
    if r2 < 0.30:
        regime = "range"
        reasoning.append("r2 below gate -> range")
    elif all(v == "rising" for v in vals):
        regime = "bullish_trend"
        reasoning.append("all EMAs rising and r2 above gate -> bullish_trend")
    elif all(v == "falling" for v in vals):
        regime = "bearish_trend"
        reasoning.append("all EMAs falling and r2 above gate -> bearish_trend")
    else:
        regime = "transition"
        reasoning.append("mixed EMA slopes with r2 above gate -> transition")
    return {
        "regime": regime,
        "inputs": features,
        "reasoning": reasoning,
    }


def regime_snapshot_path(regime_result, path="logs/regime_latest.json"):
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(regime_result, f, indent=1)
