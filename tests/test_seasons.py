"""Season tests — hand-verified, derived from journal facts only.

Seasons are an HONEST track-record unit: nothing invented, no mode
switching, REAL MONEY seasons only exist if a human set mode: live.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.kill_switch import Journal
from core.seasons import compute_seasons, paper_track_record


def _entries(journal_dir, tmp_user, orders):
    """orders: list of (hours_offset, mode). Returns journal entries."""
    j = Journal(tmp_user, journal_dir)
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for h, mode in orders:
        j.append({"type": "order", "mode": mode,
                  "ts": (t0 + timedelta(hours=h)).isoformat()})
    return j.entries()


def test_empty_journal_no_seasons(tmp_path):
    assert compute_seasons([]) == []


def test_contiguous_paper_orders_are_one_season(tmp_path):
    e = _entries(str(tmp_path), "u1",
                 [(0, "paper"), (2, "paper"), (5, "paper")])
    s = compute_seasons(e)
    assert len(s) == 1
    assert s[0]["label"] == "DEMO"
    assert s[0]["orders"] == 3
    assert s[0]["span_days"] == 0.21  # 5 hours


def test_gap_over_7_days_splits_seasons(tmp_path):
    e = _entries(str(tmp_path), "u2",
                 [(0, "paper"), (24 * 8, "paper")])  # 8-day gap
    s = compute_seasons(e)
    assert len(s) == 2
    assert all(x["label"] == "DEMO" for x in s)


def test_mode_change_starts_new_season(tmp_path):
    e = _entries(str(tmp_path), "u3",
                 [(0, "paper"), (1, "paper"), (2, "live")])
    s = compute_seasons(e)
    assert len(s) == 2
    assert s[0]["label"] == "DEMO"
    assert s[1]["label"] == "REAL MONEY"


def test_gap_just_under_7_days_stays_one_season(tmp_path):
    e = _entries(str(tmp_path), "u4",
                 [(0, "paper"), (24 * 7 - 1, "paper")])
    assert len(compute_seasons(e)) == 1


def test_non_order_entries_ignored(tmp_path):
    j = Journal("u5", str(tmp_path))
    j.append({"type": "refusal", "stage": "risk_manager", "reasons": []})
    assert compute_seasons(j.entries()) == []


def test_paper_track_record(tmp_path):
    e = _entries(str(tmp_path), "u6",
                 [(0, "paper"), (24 * 10, "paper"), (3, "paper"),
                  (5, "live")])
    n, days = paper_track_record(e)
    assert n == 3
    assert days == pytest.approx(10.0, abs=0.01)


def test_paper_track_record_empty(tmp_path):
    assert paper_track_record([]) == (0, 0.0)
