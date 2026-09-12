"""Feature-value validation.

After each walk-forward backtest run, compute simple ablation importance
(full-strategy performance vs performance with each feature group removed)
for trend vs ICT vs candlestick vs S/R features. Results are written to
briefs/feature_validation_YYYYMMDD.md.

A feature group that shows no out-of-sample improvement after costs across
multiple backtest windows is flagged in the brief as
"no demonstrated value, candidate for removal."
"""
import os
from datetime import datetime, timezone

FLAG_TEXT = "no demonstrated value, candidate for removal"
KEEP_TEXT = "contributing out-of-sample"


def ablation_importance(evaluate, feature_groups, cost_threshold=0.0):
    """`evaluate(active_groups: set) -> float` returns the out-of-sample
    performance metric (after costs) of the strategy using only the given
    feature groups. `cost_threshold` is the minimum improvement a group must
    add to be counted as contributing (e.g. one round-trip cost)."""
    groups = list(feature_groups)
    if not groups:
        raise ValueError("no feature groups supplied")
    baseline = evaluate(set(groups))
    results = {}
    for g in groups:
        without = set(groups) - {g}
        metric_without = evaluate(without)
        improvement = baseline - metric_without
        verdict = KEEP_TEXT if improvement > cost_threshold else FLAG_TEXT
        results[g] = {
            "metric_full": round(baseline, 6),
            "metric_without": round(metric_without, 6),
            "improvement": round(improvement, 6),
            "verdict": verdict,
        }
    return {"baseline_metric": round(baseline, 6), "groups": results}


def write_feature_brief(results, out_dir="briefs",
                        date=None, windows_note=None):
    """Write the ablation results as a brief. Groups flagged as
    no-demonstrated-value are stated explicitly."""
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "feature_validation_%s.md" % date)
    lines = [
        "# Feature Validation — %s" % date,
        "",
        "Ablation importance after costs. Baseline metric: %.6f" %
        results["baseline_metric"],
        "",
    ]
    for g, r in results["groups"].items():
        lines.append("- **%s**: full=%.6f, without=%.6f, improvement=%.6f"
                     " — %s" % (g, r["metric_full"], r["metric_without"],
                                r["improvement"], r["verdict"]))
    flagged = [g for g, r in results["groups"].items()
               if r["verdict"] == FLAG_TEXT]
    lines.append("")
    if flagged:
        lines.append("Flagged: %s — %s. If this persists across multiple "
                     "backtest windows, remove these feature groups."
                     % (", ".join(flagged), FLAG_TEXT))
    else:
        lines.append("No feature group flagged in this run.")
    if windows_note:
        lines.append("")
        lines.append(windows_note)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path
