"""Sessions (killzones) and market-structure features — including the
ICT unverified-framework labeling."""
import pytest

from core.sessions import session_context, to_ny
from core.structure import (detect_fvg, premium_discount,
                            swing_structure_label, support_resistance_levels)
from datetime import datetime, timezone


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class TestNYTime:
    def test_edt_summer(self):
        ny = to_ny(utc(2026, 7, 1, 12))
        assert (ny.hour, ny.minute) == (8, 0)  # EDT = UTC-4

    def test_est_winter(self):
        ny = to_ny(utc(2026, 1, 15, 12))
        assert (ny.hour, ny.minute) == (7, 0)  # EST = UTC-5

    def test_dst_spring_forward(self):
        # DST starts 2026-03-08 (2nd Sunday) at 02:00 EST == 07:00 UTC
        assert to_ny(utc(2026, 3, 8, 6, 30)).hour == 1   # still EST
        assert to_ny(utc(2026, 3, 8, 7, 30)).hour == 3   # jumped to 03:00 EDT

    def test_dst_fall_back(self):
        # DST ends 2026-11-01 (1st Sunday) at 02:00 EDT == 06:00 UTC
        assert to_ny(utc(2026, 11, 1, 5, 30)).hour == 1  # still EDT
        assert to_ny(utc(2026, 11, 1, 6, 30)).hour == 1  # back to 01:00 EST


class TestSessions:
    def test_london_killzone(self):
        # 02:00-05:00 NY: July 1, 07:00 UTC = 03:00 NY
        assert session_context(utc(2026, 7, 1, 7))["session"] == "london_kz"

    def test_ny_killzone(self):
        # 07:00-10:00 NY: July 1, 12:00 UTC = 08:00 NY
        assert session_context(utc(2026, 7, 1, 12))["session"] == "ny_kz"

    def test_asian_range(self):
        # 19:00-00:00 NY: Jan 15, 03:30 UTC = 22:30 NY
        assert session_context(utc(2026, 1, 15, 3, 30))["session"] \
            == "asian_range"

    def test_ny_lunch(self):
        # 12:00-13:00 NY: July 1, 16:30 UTC = 12:30 NY
        assert session_context(utc(2026, 7, 1, 16, 30))["session"] == "ny_lunch"

    def test_off_session(self):
        assert session_context(utc(2026, 9, 12, 9, 30))["session"] \
            == "off_session"

    def test_ict_labeling_present(self):
        sc = session_context(utc(2026, 7, 1, 12))
        assert sc["source"] == "ict_unverified"
        assert "unverified heuristic framework" in sc["note"]


def bars3(bars_spec):
    return [{"timestamp": "2026-09-12T%02d:00:00+00:00" % (i + 1),
             "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
            for i, (o, h, lo, c) in enumerate(bars_spec)]


class TestFVG:
    def test_bullish_fvg(self):
        bars = bars3([(99, 100, 98, 99.5), (99.2, 99.5, 99, 99.3),
                      (101, 102, 101, 101.5)])
        zones = detect_fvg(bars)
        assert len(zones) == 1
        z = zones[0]
        assert z["type"] == "bullish"
        assert z["top"] == 101 and z["bottom"] == 100
        assert z["source"] == "ict_unverified"

    def test_bearish_fvg(self):
        bars = bars3([(99, 99, 98, 98.5), (98.3, 98.6, 98, 98.2),
                      (97, 97, 96, 96.5)])
        zones = detect_fvg(bars)
        assert len(zones) == 1
        z = zones[0]
        assert z["type"] == "bearish"
        assert z["top"] == 98 and z["bottom"] == 97

    def test_no_gap(self):
        bars = bars3([(99, 100, 98, 99.5)] * 3)
        assert detect_fvg(bars) == []


class TestPremiumDiscount:
    def test_mid_low_high_ratio(self):
        bars = [{"high": 110.0, "low": 90.0, "close": c}
                for c in (95, 100, 105, 90, 110)][:20]
        bars = [{"high": 110.0, "low": 90.0, "close": 100.0}] * 19 + \
               [{"high": 110.0, "low": 90.0, "close": 100.0}]
        assert premium_discount(bars, window=20) == pytest.approx(0.5)

    def test_at_high_and_low(self):
        bars = [{"high": 110.0, "low": 90.0, "close": 110.0}] * 20
        assert premium_discount(bars, window=20) == pytest.approx(1.0)
        bars = [{"high": 110.0, "low": 90.0, "close": 90.0}] * 20
        assert premium_discount(bars, window=20) == pytest.approx(0.0)


class TestSwingStructureLabel:
    def pivots(self, highs, lows):
        out = []
        for h in highs:
            out.append({"kind": "high", "price": h})
        for l in lows:
            out.append({"kind": "low", "price": l})
        return out

    def test_uptrend_and_downtrend(self):
        assert swing_structure_label(
            self.pivots([100, 105], [90, 92])) == "HH-HL"
        assert swing_structure_label(
            self.pivots([105, 100], [92, 90])) == "LH-LL"

    def test_mixed(self):
        assert swing_structure_label(self.pivots([100, 105], [92, 90])) \
            == "mixed"

    def test_insufficient(self):
        assert swing_structure_label(self.pivots([100], [90])) \
            == "insufficient_data"


class TestSupportResistance:
    def test_clustering_merge_and_roles(self):
        pivots = [{"price": p} for p in (110.0, 105.0, 100.1, 100.05, 100.0)]
        levels = support_resistance_levels(pivots, ref_price=104.0,
                                           min_spacing_pct=0.25)
        # 100/100.05/100.1 merge (within 0.25%); 105 and 110 stay separate
        assert [l["price"] for l in levels] == [110.0, 105.0,
                                                pytest.approx(100.05)]
        assert [l["touches"] for l in levels] == [1, 1, 3]
        roles = {l["price"]: l["role"] for l in levels}
        assert roles[105.0] == "resistance"
        assert roles[levels[2]["price"]] == "support"

    def test_rejects_nonpositive_spacing(self):
        with pytest.raises(ValueError):
            support_resistance_levels([], ref_price=100, min_spacing_pct=0)
