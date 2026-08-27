"""Writing a date the way the studio's families read it.

A lesson's date lives on the session document, and the document's property is
deliberately flexible: a studio may keep a real date there, or type
"สัปดาห์ที่แล้ว" by hand. So this formats what it can parse and returns
everything else untouched — a message with an odd date in it is a small
problem, and a message that failed to send because a date could not be parsed
is a large one.

Month names are configured rather than left to ``%B`` for the same reason the
footer configures them: a container almost never has the studio's locale
installed, so ``%B`` renders English into a Thai message and nobody notices
until a parent does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .archive import ERAS


class DateFormatError(ValueError):
    """The date configuration cannot produce a date."""


@dataclass(frozen=True)
class DateFormat:
    """How one studio writes a date for the people it teaches.

    Attributes:
        format: strftime, with ``{month}`` replaced from ``months`` before
            strftime sees it. Empty means "leave the value exactly as it is",
            which is the default: a studio that has not asked for a format
            keeps whatever its documents hold.
        era: Key into :data:`baton.domain.archive.ERAS`, applied to the year
            only, so a studio that writes 2569 for 2026 gets the year its
            families recognise.
        months: Twelve names, January first. Empty falls back to ``%B``.
    """

    format: str = ""
    era: str = "gregorian"
    months: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.era not in ERAS:
            raise DateFormatError(
                f"Unknown era `{self.era}`. Use one of: {', '.join(sorted(ERAS))}."
            )
        if self.months and len(self.months) != 12:
            raise DateFormatError(
                f"Twelve month names are needed, or none, not {len(self.months)}."
            )

    @classmethod
    def from_config(cls, section: dict[str, Any] | None, *, default_format: str = "") -> DateFormat:
        """Build from a config section holding ``format``, ``era``, ``months``."""
        data = section or {}
        return cls(
            format=str(data.get("format", default_format)),
            era=str(data.get("era", "gregorian")),
            months=tuple(str(name) for name in data.get("months", []) or []),
        )

    def __bool__(self) -> bool:
        return bool(self.format)

    def render(self, moment: date | datetime) -> str:
        """``moment`` written this studio's way.

        Raises:
            DateFormatError: The format asks for ``{month}`` and no month names
                are configured — silently emitting an empty month would be
                worse than saying so.
        """
        pattern = self.format
        if not pattern:
            return moment.isoformat()[:10]
        if "{month}" in pattern:
            if not self.months:
                raise DateFormatError(
                    "The date format uses {month} but no month names are configured."
                )
            pattern = pattern.replace("{month}", self.months[moment.month - 1])
        # strftime owns the year's shape but not its value: an era offset can
        # push it past what a real date can hold, so the year is formatted from
        # a stand-in that exists only to carry it. Same trick as SpanFormat.
        offset = ERAS[self.era]
        stamp = moment.replace(year=moment.year + offset) if offset else moment
        return stamp.strftime(pattern)

    def of_text(self, value: str) -> str:
        """``value`` reformatted when it is a date, and returned as-is when not.

        Handles the ISO date a document property holds, with or without a time
        part. Anything else — a hand-typed phrase, an empty property — passes
        straight through.
        """
        raw = (value or "").strip()
        if not raw or not self.format:
            return raw
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
        return self.render(parsed.date())
