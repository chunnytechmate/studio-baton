"""What a calendar must do.

Small on purpose. Baton books lessons and cancels them; it is not a calendar
client. Anything richer belongs in the calendar app the studio already uses.

The one thing beyond bookings is the standing weekly schedule: a sync keeps
one recurring series per lesson slot so the studio's calendar shows who comes
when without anybody typing it. Those series are marked, and ``list_between``
keeps them out of the booking world entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

#: The private extended property that marks a standing series as ours.
#: Everything a schedule sync does finds its own events through this key and
#: so never touches an event a person typed.
STANDING_MARKER = "batonStanding"


@dataclass(frozen=True)
class CalendarEvent:
    """One booking."""

    id: str
    title: str
    start: str
    """Local ISO 8601 with offset, e.g. ``2026-08-20T17:00:00+07:00``."""
    end: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "description": self.description,
        }


@dataclass(frozen=True)
class StandingSpec:
    """One weekly series a schedule sync keeps on the calendar.

    ``first_date`` is deliberately not here: which occurrence a series starts
    from is a dating rule (the next one, today never counts) that belongs to
    the pipeline, which knows today; the adapter only turns a decided date
    into a recurrence.
    """

    learner_id: str
    title: str
    weekday: str
    """An English day name, the same seven words the lesson slots carry."""
    start: str
    """``HH:MM``, the hour the lesson starts."""
    end: str
    """``HH:MM``, one hour after ``start`` unless a studio says otherwise."""
    timezone: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "title": self.title,
            "weekday": self.weekday,
            "start": self.start,
            "end": self.end,
            "timezone": self.timezone,
            "description": self.description,
        }


@runtime_checkable
class CalendarStore(Protocol):
    """Creates, lists, and removes bookings, and keeps the standing set."""

    def create(self, event: CalendarEvent) -> CalendarEvent:
        """Create one event. Returns it with the calendar's assigned id."""
        ...

    def list_between(self, start: str, end: str) -> list[CalendarEvent]:
        """Booked events overlapping the window, in start order.

        Standing series are not bookings: implementations keep them out of
        this answer so every booking command keeps meaning "lessons someone
        booked", never "the weekly pattern behind them".
        """
        ...

    def delete(self, event_id: str) -> None:
        """Remove one event or series. A missing event is not an error: the
        desired state has been reached, and a cancel run twice must not fail.
        """
        ...

    def list_standing(self, learner_id: str | None = None) -> list[CalendarEvent]:
        """Every standing series this sync owns (one learner's when
        ``learner_id`` is given), whatever their date. Bookings never appear;
        unmarked events belong to a person and are never returned."""
        ...

    def create_standing(self, spec: StandingSpec, *, first_date: str) -> CalendarEvent:
        """Create one weekly series, starting on ``first_date`` (an ISO date
        that is the spec's weekday). Returns it with the calendar's id."""
        ...

    def health(self) -> None: ...
