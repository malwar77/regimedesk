"""Simple cost-aware backtest with MANDATORY benchmark comparison.
Every backtest run must report the strategy against a naive buy-and-hold
and a simple MA-crossover benchmark after realistic costs. If the strategy
does not beat both, the report says so plainly — absolute returns alone
are never the headline.
"""


def _sma_end(prices, period, end):
    window = prices[max(0, end - period):end]
    if len(window) < period:
        return None
    return sum(window) / period


def close_returns(closes):
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


def backtest(closes, positions, cost_bps=2.0):
    """Equity metrics for a position series (1.0 long, 0 flat, -1.0 short),
    with a cost of `cost_bps` applied to each unit of position turnover.
    positions[i] is the position HELD from close i to close i+1 (no lookahead).
    """
    if len(positions) < len(closes) - 1:
        raise ValueError("positions too short")
    if cost_bps < 0:
        raise ValueError("cost_bps must be >= 0")
    rets = close_returns(closes)
    curve = [1.0]
    prev_pos = 0.0
    for i, r in enumerate(rets):
        pos = positions[i]
        gross = 1.0 + pos * r
        turnover = abs(pos - prev_pos)
        gross *= 1.0 - turnover * cost_bps / 10000.0
        curve.append(curve[-1] * gross)
        prev_pos = pos
    total = curve[-1] - 1.0
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1.0)
    strat_rets = [curve[i + 1] / curve[i] - 1.0 for i in range(len(curve) - 1)]
    mean = sum(strat_rets) / len(strat_rets) if strat_rets else 0.0
    var = (sum((x - mean) ** 2 for x in strat_rets) / (len(strat_rets) - 1)
           if len(strat_rets) > 1 else 0.0)
    sd = var ** 0.5
    sharpe = (mean / sd * (252 ** 0.5)) if sd > 0 else 0.0
    return {"total_return": round(total, 6),
            "max_drawdown": round(max_dd, 6),
            "sharpe": round(sharpe, 4),
            "final_equity": round(curve[-1], 6)}


def buy_and_hold_positions(n):
    return [1.0] * n


def ma_crossover_positions(closes, fast=50, slow=200):
    """Long when SMA(fast) > SMA(slow), else flat. Flat before both SMAs
    exist."""
    if fast >= slow:
        raise ValueError("fast window must be < slow window")
    out = []
    for i in range(len(closes) - 1):
        f = _sma_end(closes, fast, i + 1)
        s = _sma_end(closes, slow, i + 1)
        out.append(1.0 if (f is not None and s is not None and f > s) else 0.0)
    return out


def compare_with_benchmarks(closes, strategy_positions, cost_bps=2.0,
                            label="strategy"):
    """Full report: strategy vs buy-and-hold vs MA-crossover, after costs.
    `beats_*` flags are stated plainly either way."""
    strat = backtest(closes, strategy_positions, cost_bps)
    bh = backtest(closes, buy_and_hold_positions(len(closes) - 1), cost_bps)
    ma = backtest(closes, ma_crossover_positions(closes), cost_bps)
    beats_bh = strat["total_return"] > bh["total_return"]
    beats_ma = strat["total_return"] > ma["total_return"]
    verdict = "beats both benchmarks after costs" if (beats_bh and beats_ma) \
        else "does NOT beat both benchmarks after costs"
    summary = ("%s: total return %.2f%% vs buy-and-hold %.2f%% vs "
               "MA-crossover %.2f%% (after %.0f bps costs) — %s"
               % (label, strat["total_return"] * 100, bh["total_return"] * 100,
                  ma["total_return"] * 100, cost_bps, verdict))
    return {"strategy": strat, "buy_and_hold": bh, "ma_crossover": ma,
            "beats_buy_and_hold": beats_bh,
            "beats_ma_crossover": beats_ma,
            "summary": summary}


# ---------------------------------------------------------------------------
# Engine replay: turn the deterministic signal engine into a position series
# ---------------------------------------------------------------------------
def signal_positions(bars, instrument="BTC_USD", timeframe="H1",
                     generator=None):
    """Replay the deterministic signal engine over historical bars.

    Returns positions[i] for i in range(len(bars)-1): the position HELD
    from close i to close i+1. The signal for slot i is computed from
    bars[:i+1] ONLY (bar i's close included, nothing later) — no
    lookahead by construction.

    Mapping follows the live contract exactly: the engine is the trigger.
      direction "long"  ->  1.0
      direction "short" -> -1.0
      no proposal       ->  0.0 (flat; the engine did not fire)

    `generator` is injectable for tests; defaults to the real engine.
    """
    if generator is None:
        from .signal_engine import generate as generator  # local: no cycle
    positions = []
    for i in range(len(bars) - 1):
        proposal = generator(instrument, bars[:i + 1], timeframe=timeframe)
        if proposal is None:
            positions.append(0.0)
        else:
            positions.append(
                1.0 if proposal["direction"] == "long" else -1.0)
    return positions
