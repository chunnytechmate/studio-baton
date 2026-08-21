"""Naming an archived course.

A finished course is kept as a copy of its page, named for the span it covered:
``Course 12 (16/05 - 07/08/69)``. Studios have been doing this by hand for
years, so the shape is not ours to invent — it is a convention to reproduce
exactly, including the two details a person applies without thinking:

The year is written once, on the closing end. It appears on the opening end too
only when the two ends fall in different years, because that is the only time
the reader needs it.

And a course page that already carries a span in its title (a studio that
renames the live page when the course starts) must not end up with two. The
old span is stripped and the real one recomputed from the sessions, so the
name always describes what actually happened rather than what was planned.

Nothing here talks to a network or a clock: given two dates it returns a
string, which is what makes the convention testable rather than merely
documented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: Era offsets, in years, from the Gregorian calendar.
ERAS: dict[str, int] = {
    "gregorian": 0,
    "buddhist": 543,
}

#: strftime tokens a span format may use, and what each matches when read back.
_TOKEN_PATTERNS: dict[str, str] = {
    "%d": r"\d{1,2}",
    "%m": r"\d{1,2}",
    "%y": r"\d{2}",
    "%Y": r"\d{4}",
    "%b": r"[^\s()]+",
    "%B": r"[^\s()]+",
}


class SpanError(ValueError):
    """The span configuration cannot produce or read back a name."""


@dataclass(frozen=True)
class SpanFormat:
    """How a studio writes the two ends of a course.

    Attributes:
        date_format: strftime for one end without its year, e.g. ``%d/%m``.
        year_format: strftime for the year alone, e.g. ``%y``.
        era: Key into :data:`ERAS`. The offset is applied to the year only.
        separator: What sits between the two ends.
        joiner: What attaches a year to an end.
    """

    date_format: str = "%d/%m"
    year_format: str = "%y"
    era: str = "gregorian"
    separator: str = " - "
    joiner: str = "/"

    def __post_init__(self) -> None:
        if self.era not in ERAS:
            raise SpanError(
                f"Unknown era `{self.era}`. Use one of: {', '.join(sorted(ERAS))}."
            )

    def year_of(self, day: date) -> int:
        """The year as this studio counts it."""
        return day.year + ERAS[self.era]

    def end(self, day: date, *, with_year: bool) -> str:
        text = day.strftime(self.date_format)
        if not with_year:
            return text
        # strftime owns the year's shape (%y zero-pads, %Y does not truncate),
        # but not its value — an era offset can push it past what a real date
        # can hold, so the year is formatted from a date that only exists to
        # carry it.
        stand_in = date(2000, 1, 1).replace(year=self.year_of(day))
        return f"{text}{self.joiner}{stand_in.strftime(self.year_format)}"

    def span(self, first: date, last: date) -> str:
        """``16/05 - 07/08/69``, with the opening year only when it differs."""
        if last < first:
            first, last = last, first
        opens_elsewhere = self.year_of(first) != self.year_of(last)
        return self.separator.join(
            (self.end(first, with_year=opens_elsewhere), self.end(last, with_year=True))
        )

    def pattern(self) -> re.Pattern[str]:
        """Matches a parenthesised span written in this format, at a title's end.

        Derived from the format rather than hard-coded so a studio that writes
        its dates differently still gets old spans stripped. Anything that is
        not a span — ``(Drum)``, ``(Worth It)`` — cannot match, and stays.
        """
        end = f"{_as_pattern(self.date_format)}(?:{re.escape(self.joiner)}{_as_pattern(self.year_format)})?"
        separator = re.escape(self.separator.strip())
        return re.compile(
            rf"\s*\(\s*{end}\s*{separator}\s*{end}\s*\)\s*$",
        )


def _as_pattern(fmt: str) -> str:
    """A strftime format as a regular expression matching what it produces."""
    out: list[str] = []
    index = 0
    while index < len(fmt):
        token = fmt[index : index + 2]
        if token in _TOKEN_PATTERNS:
            out.append(_TOKEN_PATTERNS[token])
            index += 2
            continue
        if token.startswith("%"):
            raise SpanError(f"Unsupported date token `{token}` in `{fmt}`.")
        out.append(re.escape(fmt[index]))
        index += 1
    return "".join(out)


def strip_span(title: str, span_format: SpanFormat) -> str:
    """The title without any span it already carries.

    Repeated because a page renamed twice carries two.
    """
    pattern = span_format.pattern()
    previous = None
    current = title.strip()
    while previous != current:
        previous = current
        current = pattern.sub("", current).strip()
    return current


def archive_title(
    course_title: str,
    first: date,
    last: date,
    *,
    span_format: SpanFormat,
    template: str = "{course} ({span})",
    label: str | None = None,
) -> str:
    """What the archived copy of a course page is called.

    Args:
        course_title: The live page's title. Any span it carries is dropped.
        first: The earliest session date in the course.
        last: The latest.
        span_format: How this studio writes the two ends.
        template: ``{course}`` and ``{span}``, plus ``{label}`` when one is given.
        label: An optional note — a piece the course was about, say — placed
            between the course name and the span.
    """
    course = strip_span(course_title, span_format)
    if label:
        course = f"{course} ({label.strip()})"
    return template.format(course=course, span=span_format.span(first, last)).strip()
