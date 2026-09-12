"""Signal engine — generates PROPOSALS, never trades.

Primary signal basis: core/trend_following.py (time-series momentum,
MA position, Donchian breakout, strength gate, volatility scaling).
ICT features (FVG, killzone, premium/discount), candlestick patterns and
S/R levels are supporting CONTEXT in the reasoning field only — never the
sole basis for a proposal.

Every generated signal is logged (whether or not it is ever executed) to
logs/live_signals_YYYYMMDD.jsonl with its full reasoning trace.

Suggested stop-loss / take-profit levels (chandelier/SAR, fib/swing) are
PROPOSALS ONLY: the RiskManager's own stop-loss and position-sizing rules
keep final veto, unchanged.
"""
import json
import os
from datetime import datetime, timezone

from . import candlestick_patterns as cp
from . import exit_strategy as ex
from . import indicators as ind
from . import position_sizing as psz
from . import trend_following as tf
from .regime_engine import classify_regime
from .sessions import session_context
from .structure import (detect_fvg, premium_discount, swing_structure_label,
                        support_resistance_levels)

ICT_DISCLAIMER = ("FVG/killzone/premium-discount context is derived from an "
                  "unverified heuristic framework (ICT), not institutionally "
                  "documented")

# instrument meta for position sizing: sizes are in LOTS (FX) or UNITS (BTC)
FX_META = {"pip_size": 0.0001, "pip_value_per_lot": 10.0}   # USD per pip per standard lot
BTC_META = {"pip_size": 1.0, "pip_value_per_lot": 1.0}      # 1 USD per $1 move per unit

# signal weights — explicit and deterministic
W_MA, W_TSMOM, W_DONCHIAN = 0.5, 0.3, 0.2
STRENGTH_THRESHOLD = 0.4       # |combined| must reach this for a proposal
STRENGTH_R2_GATE = 0.30       # trend-strength gate, same as regime engine
DEFAULT_RISK_PCT = 1.0        # proposal only; RiskManager validates limits
TARGET_VOL_PER_BAR = 0.01


def _meta(instrument):
    return BTC_META if instrument.startswith("BTC") else FX_META


def build_features(bars):
    """All trend/context features for the latest bar, computed strictly from
    the supplied bars (no lookahead)."""
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    ema_slopes = {}
    for period in (20, 50, 200):
        series = ind.ema_series(closes, period)
        ema_slopes[str(period)] = {
            "value": round(series[-1], 8),
            "slope": ind.classify_slope(series, lookback=10),
        }
    rsi_value = ind.rsi(closes, 14)
    atr_value = ind.atr(highs, lows, closes, 14)
    pivots = ind.swing_pivots(bars, k=2)
    structure = swing_structure_label(pivots)
    sr = support_resistance_levels(pivots, closes[-1], min_spacing_pct=0.25)
    fvg = detect_fvg(bars)
    premium = premium_discount(bars, window=20)
    session = session_context(bars[-1]["timestamp"])
    candle_flags = cp.scan_patterns(bars)
    # trend-following primaries
    returns = [closes[i] / closes[i - 1] - 1.0
               for i in range(1, len(closes))]
    ma_sig = tf.ma_signal(closes, windows=(50, 100, 200))
    tsmom = tf.time_series_momentum(returns, lookback_months=(24, 72, 168)) \
        if len(returns) >= 24 else 0.0
    don = tf.donchian_breakout(closes, window=20)
    strength = tf.trend_strength(closes, window=50)
    vol = tf.realized_volatility(returns, window=24) if len(returns) >= 24 \
        else 0.0
    regime = classify_regime({
        "ema_slopes": {k: v["slope"] for k, v in ema_slopes.items()},
        "trend_strength": strength,
        "donchian": don,
    })
    return {
        "ema_slopes": ema_slopes,
        "rsi": {"value": round(rsi_value, 2), "state": ind.rsi_state(rsi_value)},
        "atr": atr_value,
        "pivots": pivots,
        "structure": structure,
        "support_resistance": sr,
        "fvg_zones": fvg,
        "premium_discount": round(premium, 4),
        "session_context": session,
        "candlestick_flags": candle_flags,
        "ma_signal": ma_sig,
        "tsmom": round(tsmom, 4),
        "donchian": don,
        "trend_strength": strength,
        "realized_vol": vol,
        "regime": regime,
        "last_close": closes[-1],
        "highs": highs,
        "lows": lows,
        "closes": closes,
    }


def generate(instrument, bars, account_balance=None,
             risk_pct=DEFAULT_RISK_PCT, timeframe="H1"):
    """Evaluate one instrument. Returns a proposal dict, or None when the
    trend rules produce no signal. Proposals are never executed here."""
    if len(bars) < 210:
        return None  # need EMA200 + slope lookback of valid values
    f = build_features(bars)
    entry = f["last_close"]
    don_sign = {"up": 1.0, "down": -1.0, "inside": 0.0}[f["donchian"]["direction"]]
    combined = (W_MA * f["ma_signal"]["signal"] + W_TSMOM * f["tsmom"]
                 + W_DONCHIAN * don_sign)
    if f["trend_strength"]["r2"] < STRENGTH_R2_GATE:
        return None
    if combined >= STRENGTH_THRESHOLD:
        direction = "long"
    elif combined <= -STRENGTH_THRESHOLD:
        direction = "short"
    else:
        return None

    meta = _meta(instrument)
    atr = f["atr"]

    # ---- suggested stop-loss: tighter of chandelier / parabolic SAR (proposals only)
    sar_series = ex.parabolic_sar(bars)
    sar = sar_series[-1]
    if direction == "long":
        chandelier = ex.chandelier_stop(f["highs"], atr, period=22, multiplier=3.0)
        candidates = [c for c in (chandelier, sar) if c < entry]
        sl = max(candidates) if candidates else None
        sl_basis = "chandelier_stop" if (candidates and sl == chandelier and sar < chandelier) else \
            ("parabolic_sar" if candidates else None)
    else:
        chandelier = ex.chandelier_stop_short(f["lows"], atr, period=22, multiplier=3.0)
        candidates = [c for c in (chandelier, sar) if c > entry]
        sl = min(candidates) if candidates else None
        sl_basis = "chandelier_stop" if (candidates and sl == chandelier and sar > chandelier) else \
            ("parabolic_sar" if candidates else None)

    # ---- suggested take-profit: fib extension of the last confirmed swings
    piv_highs = [p for p in f["pivots"] if p["kind"] == "high"]
    piv_lows = [p for p in f["pivots"] if p["kind"] == "low"]
    tp = None
    tp_basis = None
    if piv_highs and piv_lows:
        swing_high = piv_highs[-1]["price"]
        swing_low = piv_lows[-1]["price"]
        if direction == "long" and swing_low < swing_high:
            for ratio in (0.618, 1.0):
                cand = ex.fib_extension_target(swing_low, swing_high, ratio)
                if cand > entry:
                    tp = cand
                    tp_basis = "fib_extension_%.3f" % ratio
                    break
        elif direction == "short" and swing_high < swing_low:
            for ratio in (0.618, 1.0):
                cand = ex.fib_extension_target(swing_low, swing_high, ratio)
                if cand < entry:
                    tp = cand
                    tp_basis = "fib_extension_%.3f" % ratio
                    break

    # ---- suggested position size (pure calculator; RiskManager still validates)
    size = None
    risk_amount = None
    if account_balance and sl is not None:
        stop_distance = abs(entry - sl)
        if stop_distance > 0:
            sl_pips = stop_distance / meta["pip_size"]
            size = psz.calculate_position_size(
                account_balance, risk_pct, sl_pips, meta["pip_value_per_lot"])
            risk_amount = account_balance * risk_pct / 100.0

    weight = tf.volatility_scaled_position(
        1.0 if direction == "long" else -1.0,
        f["realized_vol"], TARGET_VOL_PER_BAR)

    proposal = {
        "signal_id": "%s-%s-%s" % (instrument, direction,
                                   bars[-1]["timestamp"]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instrument": instrument,
        "timeframe": timeframe,
        "direction": direction,
        "entry_price": entry,
        "combined_strength": round(combined, 4),
        "vol_scaled_weight": round(weight, 4),
        "suggested_stop_loss": sl,
        "suggested_stop_basis": sl_basis,
        "suggested_take_profit": tp,
        "suggested_take_profit_basis": tp_basis,
        "suggested_position_size": size,
        "risk_amount": risk_amount,
        "primary_signal": "trend_following",
        "regime": f["regime"]["regime"],
        "supporting_context": {
            "ema_slopes": f["ema_slopes"],
            "rsi": f["rsi"],
            "structure": f["structure"],
            "support_resistance": f["support_resistance"],
            "candlestick_flags": f["candlestick_flags"],
            "fvg_zones": f["fvg_zones"],
            "premium_discount": f["premium_discount"],
            "session_context": f["session_context"],
        },
        "reasoning": [
            "primary: trend_following (ma=%.3f, tsmom=%.3f, donchian=%s) -> "
            "combined %.3f" % (f["ma_signal"]["signal"], f["tsmom"],
                              f["donchian"]["direction"], combined),
            "trend strength r2=%.3f (gate %.2f)" % (
                f["trend_strength"]["r2"], STRENGTH_R2_GATE),
            "regime: %s" % f["regime"]["regime"],
            "context (supporting only): structure=%s, rsi=%.1f (%s), "
            "candlesticks=%s" % (
                f["structure"], f["rsi"]["value"], f["rsi"]["state"],
                [c["pattern"] for c in f["candlestick_flags"]] or "none"),
            "session context: %s" % f["session_context"]["label"],
        ],
        "disclaimers": [
            "Proposal only — not a trade recommendation.",
            ICT_DISCLAIMER,
            "Suggested SL/TP/size are proposals; the RiskManager has final "
            "veto on stop-loss and position size.",
        ],
        "executed": False,
    }
    return proposal


def log_signal(proposal, log_dir="logs"):
    """Append the full signal JSON (with reasoning trace) to the live-signal
    log. Called for EVERY generated signal, executed or not."""
    os.makedirs(log_dir, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = os.path.join(log_dir, "live_signals_%s.jsonl" % day)
    with open(path, "a") as fh:
        fh.write(json.dumps(proposal) + "\n")
    return path


def regime_snapshot(f, path="logs/regime_latest.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(),
                   **f["regime"]}, fh, indent=1)
