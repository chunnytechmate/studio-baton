"""Booking a lesson, in an order that cannot leave the two records disagreeing.

The sequence is a chain, and each link is a gate on the next:

1. resolve the learner exactly, or stop and ask
2. mark the session document in progress — and *only if that succeeds*
3. create the calendar event

The order is the point. Creating the event first and then failing to update the
document leaves a lesson on the calendar that no session knows about; the
teacher trusts the calendar, the pipeline trusts the documents, and they quietly
disagree until someone reconciles them by hand. That is exactly what happened in
the system this replaces, which is why the document goes first and the event is
never created without it.

Cancelling runs the chain backwards for the same reason: remove the event, then
roll the document back. A rollback with the event still standing would leave the
opposite inconsistency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..adapters.cal.base import CalendarEvent, CalendarStore
from ..adapters.docs.base import DocStore
from ..domain.models import Learner, Session
from ..domain.status import DONE, IN_PROGRESS, NOT_STARTED, StatusVocabulary
from ..domain.whenever import combine, parse_time
from ..errors import GateError, StateError


@dataclass
class BookingResult:
    """What one booking did, step by step."""

    learner_name: str
    session_number: int
    doc_id: str
    doc_updated: bool
    event: CalendarEvent | None
    title: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "learner": self.learner_name,
            "session_number": self.session_number,
            "doc_id": self.doc_id,
            "doc_updated": self.doc_updated,
            "event": self.event.to_dict() if self.event else None,
            "title": self.title,
        }


def event_title(
    learner: Learner,
    session_number: int,
    *,
    session_label: str = "week",
    emoji: dict[str, Any] | None = None,
    default_emoji: str = "",
) -> str:
    """The event's title, prefixed by the instrument's emoji when configured."""
    icon = ""
    if learner.instrument:
        lookup = {str(k).casefold(): str(v) for k, v in (emoji or {}).items()}
        icon = lookup.get(learner.instrument.strip().casefold(), default_emoji)
    else:
        icon = default_emoji
    prefix = f"{icon} " if icon else ""
    return f"{prefix}{learner.name} ({session_label} {session_number})"


class Scheduler:
    """Books and cancels lessons, keeping documents and calendar in step."""

    def __init__(
        self,
        calendar: CalendarStore,
        docs: DocStore,
        vocabulary: StatusVocabulary,
        *,
        timezone: str = "UTC",
        session_label: str = "week",
        require_doc_update: bool = True,
        event_emoji: dict[str, Any] | None = None,
        default_emoji: str = "",
        default_minutes: int = 60,
    ) -> None:
        self.calendar = calendar
        self.docs = docs
        self.vocabulary = vocabulary
        self.timezone = timezone
        self.session_label = session_label
        self.require_doc_update = require_doc_update
        self.event_emoji = event_emoji or {}
        self.default_emoji = default_emoji
        self.default_minutes = default_minutes

    # -- booking -----------------------------------------------------------

    def book(
        self,
        learner: Learner,
        session: Session,
        day: date,
        start: str,
        end: str | None = None,
        *,
        dry_run: bool = False,
    ) -> BookingResult:
        """Mark the session in progress, then put it on the calendar.

        Raises:
            StateError: The document update failed, so no event was created.
        """
        start_time = parse_time(start)
        if end:
            end_time = parse_time(end)
        else:
            end_dt = combine(day, start_time, self.timezone) + timedelta(
                minutes=self.default_minutes
            )
            end_time = end_dt.time()

        title = event_title(
            learner,
            session.number,
            session_label=self.session_label,
            emoji=self.event_emoji,
            default_emoji=self.default_emoji,
        )

        if dry_run:
            return BookingResult(
                learner_name=learner.name,
                session_number=session.number,
                doc_id=session.doc_id,
                doc_updated=False,
                event=CalendarEvent(
                    id="",
                    title=title,
                    start=combine(day, start_time, self.timezone).isoformat(),
                    end=combine(day, end_time, self.timezone).isoformat(),
                ),
                title=title,
            )

        doc_updated = False
        if session.doc_id and self.require_doc_update:
            try:
                self.docs.set_status(session.doc_id, IN_PROGRESS)
                doc_updated = True
            except Exception as exc:
                # Deliberately not "carry on and book anyway": an event with no
                # matching session is the drift this ordering exists to prevent.
                raise StateError(
                    f"Could not mark {learner.name}'s {self.session_label} "
                    f"{session.number} as in progress: {exc}",
                    remedy="Nothing was booked. Fix the document store and re-run — "
                    "the calendar is left untouched so the two cannot disagree.",
                    details={"doc_id": session.doc_id},
                ) from exc

        event = self.calendar.create(
            CalendarEvent(
                id="",
                title=title,
                start=combine(day, start_time, self.timezone).isoformat(),
                end=combine(day, end_time, self.timezone).isoformat(),
                description=f"{learner.name} — {self.session_label} {session.number}",
            )
        )

        return BookingResult(
            learner_name=learner.name,
            session_number=session.number,
            doc_id=session.doc_id,
            doc_updated=doc_updated,
            event=event,
            title=title,
        )

    # -- cancelling --------------------------------------------------------

    def cancel(
        self,
        learner: Learner,
        session: Session,
        day: date,
        *,
        rollback_window_days: int = 1,
        today: date | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove the booking and roll the session back to not started.

        Args:
            rollback_window_days: How far ahead or behind ``today`` a cancel is
                allowed to reach.

        Raises:
            GateError: The lesson is outside the rollback window, or the
                session is already done. Both are cases where rewriting history
                is more likely a mistake than an intention.
        """
        reference = today or datetime.now().date()
        distance = abs((day - reference).days)
        if distance > rollback_window_days:
            raise GateError(
                f"That {self.session_label} is {distance} days from today, "
                f"beyond the {rollback_window_days}-day cancel window.",
                missing=[
                    {
                        "field": "date",
                        "reason": f"{day.isoformat()} is outside the window",
                        "how_to_fix": "Cancel on the day, or widen "
                        "calendar.rollback_window_days if your studio works that way.",
                    }
                ],
                remedy="Nothing was changed. Rewriting a past week's records is "
                "usually a mistake rather than an intention.",
            )

        current = self.vocabulary.canonical(self.docs.get_status(session.doc_id).status)
        if current == DONE:
            raise GateError(
                f"{learner.name}'s {self.session_label} {session.number} is already done.",
                missing=[
                    {
                        "field": "status",
                        "reason": "the session is marked done",
                        "how_to_fix": "A completed lesson is not cancelled. Edit the "
                        "document directly if it was marked done by mistake.",
                    }
                ],
            )

        events = self._events_for(learner, day)
        if dry_run:
            return {
                "dry_run": True,
                "learner": learner.name,
                "session_number": session.number,
                "would_delete": [event.to_dict() for event in events],
                "would_set_status": NOT_STARTED,
            }

        # Event first: a rolled-back document with the lesson still on the
        # calendar is the same disagreement in the other direction.
        for event in events:
            self.calendar.delete(event.id)

        if session.doc_id:
            self.docs.set_status(session.doc_id, NOT_STARTED)

        return {
            "learner": learner.name,
            "session_number": session.number,
            "deleted": [event.to_dict() for event in events],
            "status": NOT_STARTED,
        }

    def _events_for(self, learner: Learner, day: date) -> list[CalendarEvent]:
        """This learner's events on one day, matched by name in the title."""
        start = combine(day, parse_time("00:00"), self.timezone).isoformat()
        end = combine(day + timedelta(days=1), parse_time("00:00"), self.timezone).isoformat()
        return [
            event for event in self.calendar.list_between(start, end) if learner.name in event.title
        ]
