"""Session/killzone context (facts only — never a trigger).

NOTE (project-wide labeling): "killzone" / "session" concepts used here are
derived from the ICT methodology, an UNVERIFIED HEURISTIC FRAMEWORK, not
institutionally documented. They are output as labeled facts only.

All conversions are pure-python (US DST rule) — no tz database dependency.
"""
from datetime import datetime, timedelta, timezone


def _dst_start_utc(year):
    """US DST begins 2nd Sunday of March at 02:00 EST == 07:00 UTC."""
    mar1 = datetime(year, 3, 1)
    second_sunday = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    return second_sunday.replace(hour=7, tzinfo=timezone.utc)


def _dst_end_utc(year):
    """US DST ends 1st Sunday of November at 02:00 EDT == 06:00 UTC."""
    nov1 = datetime(year, 11, 1)
    first_sunday = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return first_sunday.replace(hour=6, tzinfo=timezone.utc)


def is_dst_us(dt_utc):
    return _dst_start_utc(dt_utc.year) <= dt_utc < _dst_end_utc(dt_utc.year)


def to_ny(dt_utc):
    """Convert an aware UTC datetime to New York local (naive) time."""
    offset = -4 if is_dst_us(dt_utc) else -5
    return dt_utc + timedelta(hours=offset)


def parse_ts(ts):
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(ts))


# Killzones expressed in New York local time (facts only).
KILLZONES = [
    ("london_kz", 2, 5, "London killzone 02:00-05:00 NY"),
    ("ny_kz", 7, 10, "New York killzone 07:00-10:00 NY"),
    ("asian_range", 19, 24, "Asian range 19:00-00:00 NY"),
    ("ny_lunch", 12, 13, "NY lunch 12:00-13:00 NY"),
]


def session_context(ts):
    """Which killzone (if any) the timestamp falls in. Output is a fact,
    never a trading trigger."""
    dt_utc = parse_ts(ts)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    ny = to_ny(dt_utc)
    hour = ny.hour + ny.minute / 60.0
    for name, start, end, label in KILLZONES:
        if start <= hour < end:
            return {
                "session": name,
                "label": label,
                "ny_time": ny.isoformat(),
                "utc_time": dt_utc.isoformat(),
                "source": "ict_unverified",
                "note": ("session/killzone classification is derived from an "
                         "unverified heuristic framework, not institutionally "
                         "documented"),
            }
    return {
        "session": "off_session",
        "label": "no defined killzone",
        "ny_time": ny.isoformat(),
        "utc_time": dt_utc.isoformat(),
        "source": "ict_unverified",
        "note": ("session/killzone classification is derived from an "
                 "unverified heuristic framework, not institutionally "
                 "documented"),
    }
