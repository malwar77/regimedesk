"""Tests: hypothesis log — preregistration discipline (ported module).

A hypothesis MUST be preregistered before its result exists, results
can never be written for unknown entries, and duplicate testing of the
same variant+regime is detectable. Hand-verified expectations."""
import os
import sys

import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.hypothesis_log import HypothesisLog


def test_preregister_then_record_roundtrip(tmp_path):
    log = HypothesisLog(tmp_path / "hyp.jsonl")
    entry_id = log.preregister(
        "ema_cross_v1", {"expectation": "beats buy-and-hold after costs"},
        regime="Bull")
    assert entry_id.startswith("ema_cross_v1")
    log.record_result(entry_id, {"total_return": -0.02, "beats": False})
    entries = log._read_all_entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "completed"
    assert entries[0]["actual_result"]["beats"] is False


def test_record_unknown_entry_rejected(tmp_path):
    log = HypothesisLog(tmp_path / "hyp.jsonl")
    with pytest.raises(Exception):
        log.record_result("nonexistent_entry",
                          {"anything": True})


def test_was_already_tested(tmp_path):
    log = HypothesisLog(tmp_path / "hyp.jsonl")
    eid = log.preregister("v2", {"e": 1}, regime="Bear")
    log.record_result(eid, {"ok": True})
    assert log.was_already_tested("v2", "Bear") is True
    assert log.was_already_tested("v2", "Bull") is False    # regime differs
    assert log.was_already_tested("v3", "Bear") is False


def test_get_statistics_counts(tmp_path):
    log = HypothesisLog(tmp_path / "hyp.jsonl")
    a = log.preregister("va", {"e": 1}, regime="Bull")
    b = log.preregister("vb", {"e": 1}, regime="Bull")
    # success metric is a positive `return` in the recorded result
    log.record_result(a, {"return": 0.03})
    log.record_result(b, {"return": -0.01})
    stats = log.get_statistics()
    # hand check: 2 completed entries, 1 positive return, 1 negative
    assert stats["total_entries"] == 2
    assert stats["completed"] == 2
    assert stats["preregistered"] == 0
    assert stats["measurable_outcomes"] == 2
    assert stats["successful_outcomes"] == 1
    assert stats["success_rate"] == 0.5
