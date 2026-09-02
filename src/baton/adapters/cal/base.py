"""What a calendar must do.

Small on purpose. Baton books lessons and cancels them; it is not a calendar
client. Anything richer belongs in the calendar app the studio already uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class CalendarStore(Protocol):
    """Creates, lists, and removes bookings."""

    def create(self, event: CalendarEvent) -> CalendarEvent:
        """Create one event. Returns it with the calendar's assigned id."""
        ...

    def list_between(self, start: str, end: str) -> list[CalendarEvent]:
        """Events overlapping the window, in start order."""
        ...

    def delete(self, event_id: str) -> None:
        """Remove one event. A missing event is not an error: the desired
        state has been reached, and a cancel run twice must not fail."""
        ...

    def health(self) -> None: ...
