"""`calendar list` must not mangle date-only starts (M15).

All-day events arrive as ``YYYY-MM-DD`` with no time half. The old slice at
a fixed offset had a length guard, but a string that was long enough without
being a datetime was still cut at a position that meant nothing. The clock is
now taken only when the character at the date/time boundary is the separator
a datetime actually has.
"""

from __future__ import annotations

from baton.cli.cmd_calendar import _clock_of


def test_rfc3339_datetime_yields_its_clock():
    assert _clock_of("2026-08-26T09:30:00+07:00") == "09:30"


def test_space_separated_datetime_yields_its_clock():
    assert _clock_of("2026-08-26 09:30") == "09:30"


def test_date_only_passes_through_whole():
    """An all-day event shows its date, not a slice of nothing."""
    assert _clock_of("2026-08-26") == "2026-08-26"


def test_long_string_without_a_datetime_separator_is_not_sliced():
    """Long enough to trip a naive length check, but position 10 is a digit,
    not a separator: there is no clock to take."""
    assert _clock_of("20260826093000+0700") == "20260826093000+0700"
