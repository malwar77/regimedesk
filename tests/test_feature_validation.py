"""Feature validation: ablation importance flags a synthetic 'useless'
(noise) feature as non-contributory in a controlled test case."""
import os

import pytest

from core.feature_validation import (FLAG_TEXT, KEEP_TEXT,
                                      ablation_importance, write_feature_brief)


def controlled_evaluate(active_groups):
    """Controlled test case: ONLY the trend group carries any value.
    ict / candlestick / sr / pure_noise change nothing when removed —
    exactly the 'useless feature' scenario."""
    metric = 0.010
    if "trend" in active_groups:
        metric += 0.030
    return metric


def test_noise_feature_flagged_as_non_contributory(tmp_path):
    groups = ["trend", "ict", "candlestick", "sr", "pure_noise"]
    results = ablation_importance(controlled_evaluate, groups,
                                  cost_threshold=0.0001)
    assert results["baseline_metric"] == pytest.approx(0.040)
    g = results["groups"]
    assert g["trend"]["verdict"] == KEEP_TEXT
    assert g["trend"]["improvement"] == pytest.approx(0.030)
    for noise in ("ict", "candlestick", "sr", "pure_noise"):
        assert g[noise]["verdict"] == FLAG_TEXT
        assert g[noise]["improvement"] == 0.0
    # a random-noise feature group must never look better than trend here
    assert g["trend"]["improvement"] > g["pure_noise"]["improvement"]


def test_brief_written_and_flags_stated(tmp_path):
    groups = ["trend", "pure_noise"]
    results = ablation_importance(controlled_evaluate, groups,
                                   cost_threshold=0.0001)
    path = write_feature_brief(results, out_dir=str(tmp_path),
                               windows_note="single controlled window")
    assert os.path.exists(path)
    text = open(path).read()
    assert FLAG_TEXT in text
    assert "pure_noise" in text
    assert "candidate for removal" in text


def test_empty_groups_rejected():
    with pytest.raises(ValueError):
        ablation_importance(lambda s: 0.0, [])
