"""
Technical / Structure Agent

Responsibility:
    Nightly agent that, for every instrument on the watchlist, identifies:
        - key daily / weekly support & resistance
        - liquidity pools / equal highs-lows
        - market structure (HH-HL / LH-LL / range)
        - proximity to major levels
        - EMA slope (20, 50, 200) and RSI(14) on H1 data

    Writes briefs/technical_YYYYMMDD.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.agents.common import finding, md_table, utc_now_iso, utc_stamp, write_brief, write_state
from research.data_pipeline import DataPipeline, load_watchlist

logger = logging.getLogger(__name__)

NEAR_PCT = {"forex": 0.004, "crypto": 0.015}


def _ema(series: pd.Series, length: int) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Compute Relative Strength Index."""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(com=length - 1, adjust=False).mean()
    ma_down = down.ewm(com=length - 1, adjust=False).mean()
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def run(
    data_dir: Path,
    watchlist_path: Path,
    output_dir: Path,
    config: Optional[Dict[str, Any]] = None,
    state_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute the Technical / Structure research pass."""
    logger.info("Technical Agent started")
    root = Path(data_dir)
    cfg = config or {"data": {"source": "synthetic", "lookback_days": 90}}
    pipeline = DataPipeline(cfg, root=root)
    watchlist = load_watchlist(Path(watchlist_path))

    by_symbol: Dict[str, Dict[str, Any]] = {}
    findings: List[Dict[str, Any]] = []
    for row in watchlist:
        ticker = row["ticker"]
        h1 = pipeline.get_ohlcv(ticker, timeframe="H1")
        h4 = pipeline.get_ohlcv(ticker, timeframe="H4")
        d1 = pipeline.get_ohlcv(ticker, timeframe="D1")
        analysis = analyze_ohlcv(h1, h4, d1, asset_class=row["asset_class"])
        analysis["ticker"] = ticker
        analysis["asset_class"] = row["asset_class"]
        by_symbol[ticker] = analysis
        findings.extend(_findings_for(ticker, analysis))

    stamp = utc_stamp()
    brief_path = write_brief(output_dir, "technical", _render_brief(by_symbol, stamp), stamp=stamp)
    payload = {
        "agent": "technical",
        "stamp": stamp,
        "as_of": utc_now_iso(),
        "by_symbol": by_symbol,
        "findings": findings,
        "brief_path": str(brief_path),
        "disclaimer": "Structure description only. No trading advice.",
    }
    write_state(state_dir or (root / "state"), "technical", payload)
    logger.info("Technical Agent wrote %s", brief_path)
    return payload


def analyze_ohlcv(
    h1: pd.DataFrame,
    h4: Optional[pd.DataFrame] = None,
    d1: Optional[pd.DataFrame] = None,
    asset_class: str = "forex",
) -> Dict[str, Any]:
    """Pure structure/S-R analysis used by the agent and unit tests.

    Backwards-compatible signature: callers may pass (d1, d1) as h1/h4; if
    `d1` is not supplied we will attempt to use `h4` as `d1`, and fall back
    to `h1` when necessary.
    """
    # Backwards compat: if d1 not provided, treat h4 as d1 when appropriate
    if d1 is None and h4 is not None:
        d1 = h4
    if h4 is None:
        h4 = h1
    """Pure structure/S-R analysis used by the agent and unit tests."""
    last = float(h1["close"].iloc[-1]) if h1 is not None and not h1.empty else float("nan")
    frame = d1 if d1 is not None and not d1.empty else h4 if h4 is not None and not h4.empty else h1
    if frame is None or frame.empty or not np.isfinite(last):
        return {
            "last": last,
            "structure": "range",
            "direction": "none",
            "support": [],
            "resistance": [],
            "liquidity_pools": [],
            "near_level": None,
            "pivots": {},
            "trend_state": None,
        }

    daily_high = float(frame["high"].tail(20).max())
    daily_low = float(frame["low"].tail(20).min())
    weekly_high = float(frame["high"].tail(5).max())
    weekly_low = float(frame["low"].tail(5).min())

    pivots: Dict[str, float] = {}
    if len(frame) >= 2:
        prev = frame.iloc[-2]
        p = float((prev.high + prev.low + prev.close) / 3.0)
        pivots = {
            "P": p,
            "R1": 2.0 * p - float(prev.low),
            "S1": 2.0 * p - float(prev.high),
            "R2": p + (float(prev.high) - float(prev.low)),
            "S2": p - (float(prev.high) - float(prev.low)),
        }

    swings_h, swings_l = _fractals(frame)
    structure = _structure(frame, swings_h, swings_l)
    eq_highs = _clusters([price for _, price in swings_h])
    eq_lows = _clusters([price for _, price in swings_l])

    support = _unique_sorted(
        [daily_low, weekly_low, pivots.get("S1"), pivots.get("S2"), *[c["price"] for c in eq_lows]],
        last,
        side="support",
    )
    resistance = _unique_sorted(
        [daily_high, weekly_high, pivots.get("R1"), pivots.get("R2"), *[c["price"] for c in eq_highs]],
        last,
        side="resistance",
    )

    pools = []
    for cluster in eq_highs:
        if cluster["count"] >= 2:
            pools.append({"side": "equal_highs", "price": cluster["price"], "touches": cluster["count"]})
    for cluster in eq_lows:
        if cluster["count"] >= 2:
            pools.append({"side": "equal_lows", "price": cluster["price"], "touches": cluster["count"]})

    threshold = NEAR_PCT.get(asset_class, 0.006)
    near = None
    candidates = [{"kind": "support", **s} for s in support] + [{"kind": "resistance", **r} for r in resistance]
    best = 1e9
    for lvl in candidates:
        dist = abs(last - lvl["price"]) / max(abs(last), 1e-12)
        if dist < threshold and dist < best:
            best = dist
            near = {**lvl, "distance_pct": round(dist * 100.0, 3)}

    direction = "bullish" if structure == "HH-HL" else "bearish" if structure == "LH-LL" else "none"

    # ---- EMA and RSI calculations ----
    # Compute EMA20/50/200 and RSI(14) on both H1 and H4 series when available.
    close_h1 = h1["close"] if h1 is not None and not h1.empty else pd.Series([], dtype=float)
    close_h4 = h4["close"] if h4 is not None and not h4.empty else pd.Series([], dtype=float)

    ema_h1 = {
        "EMA20": _ema(close_h1, 20) if len(close_h1) >= 20 else pd.Series([np.nan] * len(close_h1)),
        "EMA50": _ema(close_h1, 50) if len(close_h1) >= 50 else pd.Series([np.nan] * len(close_h1)),
        "EMA200": _ema(close_h1, 200) if len(close_h1) >= 200 else pd.Series([np.nan] * len(close_h1)),
    }
    ema_h4 = {
        "EMA20": _ema(close_h4, 20) if len(close_h4) >= 20 else pd.Series([np.nan] * len(close_h4)),
        "EMA50": _ema(close_h4, 50) if len(close_h4) >= 50 else pd.Series([np.nan] * len(close_h4)),
        "EMA200": _ema(close_h4, 200) if len(close_h4) >= 200 else pd.Series([np.nan] * len(close_h4)),
    }

    rsi_h1 = _rsi(close_h1, 14) if len(close_h1) >= 14 else pd.Series([np.nan] * len(close_h1))
    rsi_h4 = _rsi(close_h4, 14) if len(close_h4) >= 14 else pd.Series([np.nan] * len(close_h4))

    # Get the last values
    def _last_or_none(s: pd.Series) -> Optional[float]:
        if s is None or s.empty:
            return None
        v = s.iloc[-1]
        if np.isnan(v):
            return None
        return float(v)

    ema20_h1_last = _last_or_none(ema_h1["EMA20"])
    ema50_h1_last = _last_or_none(ema_h1["EMA50"])
    ema200_h1_last = _last_or_none(ema_h1["EMA200"])
    rsi_h1_last = _last_or_none(rsi_h1)

    ema20_h4_last = _last_or_none(ema_h4["EMA20"]) 
    ema50_h4_last = _last_or_none(ema_h4["EMA50"]) 
    ema200_h4_last = _last_or_none(ema_h4["EMA200"]) 
    rsi_h4_last = _last_or_none(rsi_h4)

    # Compute slope over the last 3 periods (if available) for each EMA
    def _slope_classification(ema_series: pd.Series, last_price: float, periods: int = 3) -> Optional[str]:
        """Classify slope using `periods` lookback (default 3)."""
        if ema_series is None or ema_series.empty or len(ema_series) < periods:
            return None
        vals = ema_series.iloc[-periods:].tolist()
        if any(np.isnan(v) for v in vals):
            return None
        # Slope as (current - oldest) / (periods-1)
        denom = max((periods - 1), 1)
        slope = (vals[-1] - vals[0]) / float(denom)
        if last == 0:
            return None
        rel_slope = slope / last
        epsilon = 1e-5
        if rel_slope > epsilon:
            return "rising"
        elif rel_slope < -epsilon:
            return "falling"
        else:
            return "flat"

    # Classify slopes on both H1 and H4
    ema20_slope_h1 = _slope_classification(ema_h1["EMA20"], last, periods=3) if ema20_h1_last is not None else None
    ema50_slope_h1 = _slope_classification(ema_h1["EMA50"], last, periods=3) if ema50_h1_last is not None else None
    ema200_slope_h1 = _slope_classification(ema_h1["EMA200"], last, periods=3) if ema200_h1_last is not None else None

    ema20_slope_h4 = _slope_classification(ema_h4["EMA20"], last, periods=3) if ema20_h4_last is not None else None
    ema50_slope_h4 = _slope_classification(ema_h4["EMA50"], last, periods=3) if ema50_h4_last is not None else None
    ema200_slope_h4 = _slope_classification(ema_h4["EMA200"], last, periods=3) if ema200_h4_last is not None else None

    # RSI classification
    rsi_class_h1: Optional[str] = None
    if rsi_h1_last is not None:
        if rsi_h1_last > 70:
            rsi_class_h1 = "overbought"
        elif rsi_h1_last < 30:
            rsi_class_h1 = "oversold"
        else:
            rsi_class_h1 = "neutral"

    rsi_class_h4: Optional[str] = None
    if rsi_h4_last is not None:
        if rsi_h4_last > 70:
            rsi_class_h4 = "overbought"
        elif rsi_h4_last < 30:
            rsi_class_h4 = "oversold"
        else:
            rsi_class_h4 = "neutral"

    # Compute trend and confidence from multiple timeframe EMA slopes, RSI, and structure.
    bullish_signals = 0
    bearish_signals = 0
    total_signals = 0

    # Count H1 slopes
    for slope in [ema20_slope_h1, ema50_slope_h1, ema200_slope_h1]:
        if slope is not None:
            total_signals += 1
            if slope == "rising":
                bullish_signals += 1
            elif slope == "falling":
                bearish_signals += 1

    # Count H4 slopes with less weight (half)
    for slope in [ema20_slope_h4, ema50_slope_h4, ema200_slope_h4]:
        if slope is not None:
            total_signals += 0.5
            if slope == "rising":
                bullish_signals += 0.5
            elif slope == "falling":
                bearish_signals += 0.5

    # RSI (H1 prioritized)
    if rsi_h1_last is not None:
        total_signals += 1
        if rsi_h1_last > 50:
            bullish_signals += 1
        elif rsi_h1_last < 50:
            bearish_signals += 1
    elif rsi_h4_last is not None:
        total_signals += 0.5
        if rsi_h4_last > 50:
            bullish_signals += 0.5
        elif rsi_h4_last < 50:
            bearish_signals += 0.5

    # Structure: HH-HL -> bullish, LH-LL -> bearish, range -> neutral
    if structure == "HH-HL":
        total_signals += 1
        bullish_signals += 1
    elif structure == "LH-LL":
        total_signals += 1
        bearish_signals += 1

    # Avoid division by zero
    if total_signals == 0:
        trend = "neutral"
        confidence = 0.5
    else:
        if bullish_signals > bearish_signals:
            trend = "bullish"
            confidence = float(bullish_signals / total_signals)
        elif bearish_signals > bullish_signals:
            trend = "bearish"
            confidence = float(bearish_signals / total_signals)
        else:
            trend = "neutral"
            confidence = 0.5

    trend_state = {
        "trend": trend,
        "ema_slope": {
            "H1": {"EMA20": ema20_slope_h1, "EMA50": ema50_slope_h1, "EMA200": ema200_slope_h1},
            "H4": {"EMA20": ema20_slope_h4, "EMA50": ema50_slope_h4, "EMA200": ema200_slope_h4},
        },
        "rsi": {
            "H1": {"value": rsi_h1_last, "classification": rsi_class_h1},
            "H4": {"value": rsi_h4_last, "classification": rsi_class_h4},
        },
        "structure": structure,
        "confidence": confidence,
    }

    return {
        "last": last,
        "structure": structure,
        "direction": direction,
        "support": support[:4],
        "resistance": resistance[:4],
        "liquidity_pools": pools[:4],
        "near_level": near,
        "pivots": {k: round(v, 6) for k, v in pivots.items()},
        "daily_high": daily_high,
        "daily_low": daily_low,
        "weekly_high": weekly_high,
        "weekly_low": weekly_low,
        "trend_state": trend_state,
    }


def _fractals(frame: pd.DataFrame, left: int = 2, right: int = 2) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    sh: List[Tuple[int, float]] = []
    sl: List[Tuple[int, float]] = []
    end = len(frame) - right
    for i in range(left, max(left, end)):
        window_h = high[i - left : i + right + 1]
        window_l = low[i - left : i + right + 1]
        if window_h.size and high[i] >= window_h.max():
            sh.append((i, float(high[i])))
        if window_l.size and low[i] <= window_l.min():
            sl.append((i, float(low[i])))
    return sh, sl


def _structure(
    frame: pd.DataFrame,
    highs: Sequence[Tuple[int, float]],
    lows: Sequence[Tuple[int, float]],
) -> str:
    """Prefer a two-window Donchian read; fall back to fractal swings."""
    if len(frame) >= 24:
        tail = frame.tail(40) if len(frame) >= 40 else frame
        split = len(tail) // 2
        prior = tail.iloc[:split]
        recent = tail.iloc[split:]
        hh = float(recent["high"].max()) > float(prior["high"].max())
        hl = float(recent["low"].min()) > float(prior["low"].min())
        lh = float(recent["high"].max()) < float(prior["high"].max())
        ll = float(recent["low"].min()) < float(prior["low"].min())
        if hh and hl:
            return "HH-HL"
        if lh and ll:
            return "LH-LL"
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]
    hh = h2 > h1 * 1.0002
    hl = l2 > l1 * 1.0002
    lh = h2 < h1 * 0.9998
    ll = l2 < l1 * 0.9998
    if hh and hl:
        return "HH-HL"
    if lh and ll:
        return "LH-LL"
    return "range"


def _clusters(values: Sequence[float], rel_tol: float = 0.0015) -> List[Dict[str, Any]]:
    if not values:
        return []
    ordered = sorted(float(v) for v in values)
    groups: List[List[float]] = [[ordered[0]]]
    for price in ordered[1:]:
        anchor = groups[-1][-1]
        if abs(price - anchor) / max(abs(anchor), 1e-12) <= rel_tol:
            groups[-1].append(price)
        else:
            groups.append([price])
    out = []
    for g in groups:
        out.append({"price": float(np.mean(g)), "count": len(g)})
    return out


def _unique_sorted(values: Sequence[Optional[float]], last: float, side: str) -> List[Dict[str, Any]]:
    cleaned = [float(v) for v in values if v is not None and np.isfinite(v)]
    if side == "support":
        cleaned = [v for v in cleaned if v <= last * 1.0001]
        cleaned.sort(reverse=True)
    else:
        cleaned = [v for v in cleaned if v >= last * 0.9999]
        cleaned.sort()
    seen: List[float] = []
    out: List[Dict[str, Any]] = []
    for price in cleaned:
        if any(abs(price - s) / max(abs(s), 1e-12) < 0.0008 for s in seen):
            continue
        seen.append(price)
        dist = abs(last - price) / max(abs(last), 1e-12)
        out.append({"price": round(price, 6), "distance_pct": round(dist * 100.0, 3)})
    return out


def _findings_for(ticker: str, analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if analysis["structure"] in {"HH-HL", "LH-LL"}:
        out.append(
            finding(
                agent="technical",
                symbol=ticker,
                direction=analysis["direction"],
                kind="info",
                summary=f"{ticker} structure {analysis['structure']}",
                facts=[
                    f"last={analysis['last']}",
                    f"daily_high={analysis.get('daily_high')}",
                    f"daily_low={analysis.get('daily_low')}",
                ],
            )
        )
    near = analysis.get("near_level")
    if near:
        out.append(
            finding(
                agent="technical",
                symbol=ticker,
                direction="caution",
                kind="flag",
                summary=(
                    f"{ticker} within {near['distance_pct']:.2f}% of {near['kind']} {near['price']}"
                ),
                facts=[f"kind={near['kind']}", f"price={near['price']}"],
            )
        )
    return out


def _fmt_px(n: float) -> str:
    if n >= 1000:
        return f"{n:,.1f}"
    if n >= 10:
        return f"{n:.3f}"
    return f"{n:.5f}"


def _render_brief(by_symbol: Dict[str, Dict[str, Any]], stamp: str) -> str:
    lines = [
        f"# Technical brief {stamp}",
        "",
        "Daily/weekly structure, S/R, equal highs-lows. Description only — no trading advice.",
        "",
    ]
    rows = []
    for ticker, a in by_symbol.items():
        sup = _fmt_px(a["support"][0]["price"]) if a["support"] else "—"
        res = _fmt_px(a["resistance"][0]["price"]) if a["resistance"] else "—"
        near = (
            f"{a['near_level']['kind']} {_fmt_px(a['near_level']['price'])}"
            if a.get("near_level")
            else "—"
        )
        pools = str(len(a.get("liquidity_pools") or []))
        # Format trend_state
        trend_state = a.get("trend_state")
        if trend_state:
            trend = trend_state["trend"]
            # Support both legacy flat shape and new multi-timeframe shape
            ema_slope = trend_state.get("ema_slope", {})
            if "H1" in ema_slope:
                ema20_slope = ema_slope["H1"].get("EMA20")
            else:
                ema20_slope = ema_slope.get("EMA20")
            ema20_slope_str = ema20_slope if ema20_slope is not None else "n/a"
            # RSI value may be nested per timeframe
            rsi_field = trend_state.get("rsi", {})
            if isinstance(rsi_field, dict) and "H1" in rsi_field:
                rsi_val = rsi_field["H1"].get("value")
            else:
                rsi_val = rsi_field.get("value") if isinstance(rsi_field, dict) else None
            rsi_str = f"{rsi_val:.1f}" if rsi_val is not None else "n/a"
            trend_str = f"{trend} (EMA20:{ema20_slope_str}, RSI:{rsi_str})"
        else:
            trend_str = "n/a"
        rows.append([ticker, a["structure"], sup, res, near, pools, trend_str])
    lines.append(md_table(["Symbol", "Structure", "Support", "Resistance", "Near", "Pools", "TrendState"], rows))
    lines.append("")
    return "\n".join(lines)
