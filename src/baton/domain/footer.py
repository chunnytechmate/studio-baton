"""The line that says a summary was written by a machine.

Every summary the studio's previous pipeline published carried a dated
disclosure at the bottom — who wrote it, when, and that it came from an
assistant rather than the teacher's own hand. Parents read these pages, and a
summary that quietly stops saying where it came from is a different document
than the one they have been reading all year.

Nothing here talks to a network. Given the lines, a clock reading, and a
locale's month names it returns strings, which is what makes the convention
testable rather than merely documented.

Month names are configured rather than taken from ``%B`` on purpose: a
container almost never has the studio's locale installed, so ``%B`` would
render English month names into a Thai disclosure and no one would notice
until a parent did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .archive import ERAS


class FooterError(ValueError):
    """The footer configuration cannot produce a line."""


@dataclass(frozen=True)
class Footer:
    """Lines appended to every published summary.

    Attributes:
        lines: The text, one entry per line. ``{date}`` and ``{time}`` are
            substituted. An empty list means no footer, which is the default:
            a studio that has not asked for a disclosure does not get one.
        date_format: strftime for ``{date}``. ``{month}`` inside it is replaced
            by the configured month name before strftime ever sees the string.
        time_format: strftime for ``{time}``.
        era: Key into :data:`baton.domain.archive.ERAS`, applied to the year
            only, so a studio that writes 2569 for 2026 gets the year its
            parents recognise.
        months: Twelve month names, January first. Empty falls back to ``%B``.
    """

    lines: tuple[str, ...] = ()
    date_format: str = "%-d %B %Y"
    time_format: str = "%H:%M"
    era: str = "gregorian"
    months: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.era not in ERAS:
            raise FooterError(f"Unknown era `{self.era}`. Use one of: {', '.join(sorted(ERAS))}.")
        if self.months and len(self.months) != 12:
            raise FooterError(
                f"`summary.footer.months` needs 12 names or none, not {len(self.months)}."
            )

    @classmethod
    def from_config(cls, section: dict[str, Any] | None) -> Footer:
        """Build from the ``summary.footer`` section."""
        data = section or {}
        return cls(
            lines=tuple(str(line) for line in data.get("lines", []) or []),
            date_format=str(data.get("date_format", "%-d %B %Y")),
            time_format=str(data.get("time_format", "%H:%M")),
            era=str(data.get("era", "gregorian")),
            months=tuple(str(name) for name in data.get("months", []) or []),
        )

    def __bool__(self) -> bool:
        return bool(self.lines)

    def _date(self, moment: datetime) -> str:
        pattern = self.date_format
        if "{month}" in pattern:
            if not self.months:
                raise FooterError(
                    "`summary.footer.date_format` uses {month} but no month names are configured.",
                )
            pattern = pattern.replace("{month}", self.months[moment.month - 1])
        # strftime owns the year's shape but not its value: an era offset can
        # push it past what a real date can hold, so the year is formatted from
        # a stand-in that exists only to carry it. Same trick as SpanFormat.
        offset = ERAS[self.era]
        stamp = moment.replace(year=moment.year + offset) if offset else moment
        return stamp.strftime(pattern)

    def pattern(self) -> re.Pattern[str] | None:
        """A regex matching what :meth:`render` writes, whatever the clock said.

        Prep reads a previous session's page to gather context, and the
        disclosure is about the tool rather than the lesson — it has to come
        back out. A hand-written regex in configuration drifts away from the
        lines it is supposed to match the first time either is edited; deriving
        it from the lines themselves cannot.

        ``{date}`` and ``{time}`` become wildcards, everything else is literal,
        and ``*emphasis*`` markers are dropped because a document stores the
        emphasis as formatting, not as asterisks.
        """
        if not self.lines:
            return None
        parts = []
        for line in self.lines:
            literal = line.replace("*", "")
            escaped = re.escape(literal)
            # re.escape leaves the braces alone, so the placeholders survive
            # escaping intact and can be swapped for wildcards afterwards.
            escaped = escaped.replace(re.escape("{date}"), ".+?")
            escaped = escaped.replace(re.escape("{time}"), ".+?")
            parts.append(escaped)
        return re.compile(r"\s*".join(parts), re.DOTALL)

    def render(self, moment: datetime) -> list[str]:
        """The footer lines as of ``moment``, ready to append."""
        if not self.lines:
            return []
        values = {"date": self._date(moment), "time": moment.strftime(self.time_format)}
        rendered: list[str] = []
        for line in self.lines:
            try:
                rendered.append(line.format(**values))
            except (KeyError, IndexError) as exc:
                raise FooterError(
                    f"`summary.footer.lines` uses an unknown placeholder: {exc}",
                ) from exc
        return rendered


@dataclass(frozen=True)
class Segment:
    """One run of footer text, italic or not."""

    text: str
    italic: bool = False


def emphasis(line: str) -> list[Segment]:
    """Split ``*emphasised*`` runs out of a footer line.

    The disclosure the studio has always published is italic, and italics are
    the one piece of inline formatting these lines use. Parsing just this much
    keeps the published page looking like the hundreds that came before it
    without pulling a Markdown parser into a one-line disclaimer.

    An unpaired ``*`` is literal text: a stray asterisk should print, not
    swallow the rest of the sentence.
    """
    parts = line.split("*")
    if len(parts) % 2 == 0:
        # An even number of pieces means an odd number of asterisks: one of
        # them has no partner, so none of them are markup.
        return [Segment(line)] if line else []
    return [Segment(text, italic=bool(index % 2)) for index, text in enumerate(parts) if text]
