"""Booking a lesson, in an order that cannot leave the two records disagreeing.

The sequence is a chain, and each link is a gate on the next:

1. resolve the learner exactly, or stop and ask
2. mark the session document in progress, and *only if that succeeds*
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

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..adapters.cal.base import CalendarEvent, CalendarStore
from ..adapters.db.base import LearnerStore
from ..adapters.docs.base import DocStore
from ..domain.models import Learner, Session
from ..domain.status import DONE, IN_PROGRESS, NOT_STARTED, StatusVocabulary
from ..domain.whenever import combine, parse_time, today_in
from ..errors import BatonError, GateError, StateError, UsageError
from .learner import SessionView


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


@dataclass
class InProgressReport:
    """Who owes a summary, read from a calendar window.

    The calendar is the index and the session page is the truth; events that
    name no learner were typed by a person and are listed rather than
    guessed at.
    """

    found: list[tuple[Learner, SessionView]]
    unreadable: list[dict[str, Any]]
    unmatched: list[dict[str, Any]]
    window: dict[str, Any]


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
        time_words: Mapping[str, object] | None = None,
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
        #: The profile's own time vocabulary, so a booking may say ``6 โมงเย็น``
        #: wherever it may say ``18:00``. Reaches every parse_time below.
        self.time_words = time_words

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
            UsageError: The times are not a real lesson: an explicit end at or
                before the start.
            StateError: The document update failed, so no event was created.
        """
        # Datetimes, not times: a 23:30 start with a 60-minute default ends at
        # 00:30 *the next day*, and only datetime arithmetic knows that.
        start_dt = combine(day, parse_time(start, words=self.time_words), self.timezone)
        if end:
            end_dt = combine(day, parse_time(end, words=self.time_words), self.timezone)
            if end_dt <= start_dt:
                raise UsageError(
                    f"The lesson cannot end at {end} when it starts at {start}.",
                    remedy="Check the times, or pass only the start and let "
                    f"{self.default_minutes} default minutes apply.",
                )
        else:
            end_dt = start_dt + timedelta(minutes=self.default_minutes)

        title = event_title(
            learner,
            session.number,
            session_label=self.session_label,
            emoji=self.event_emoji,
            default_emoji=self.default_emoji,
        )

        # The same session twice in one day is almost always a re-submitted
        # booking rather than a second lesson: two identical events, both of
        # which a later cancel removes only one of. A second *different*
        # session for the same learner on the same day is a real double
        # lesson and stays allowed. Checked before the dry-run branch so a
        # dry run reports the refusal it would refuse.
        marker = f"({self.session_label} {session.number})"
        clash = next(
            (event for event in self._events_for(learner, day) if event.title.endswith(marker)),
            None,
        )
        if clash:
            raise GateError(
                f"{learner.name}'s {self.session_label} {session.number} is already "
                f"on the calendar on {day.isoformat()} at {(clash.start or '')[11:16]} "
                f"(event {clash.id}).",
                missing=[
                    {
                        "field": "date",
                        "reason": "this session is booked on that day already",
                        "how_to_fix": "Cancel that booking first if the time is wrong, "
                        "then book the new time in its place.",
                    }
                ],
                remedy="Nothing was booked twice. Keeping one event per session per "
                "day is what a later cancel relies on to remove exactly the right "
                "lesson.",
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
                    start=start_dt.isoformat(),
                    end=end_dt.isoformat(),
                ),
                title=title,
            )

        doc_updated = False
        if session.doc_id and self.require_doc_update:
            try:
                # The date goes on with the status. Booking is the moment the
                # lesson's date is actually known: inferring it later from
                # whenever the summary happened to be written would put the
                # wrong day on a lesson summarised the following morning.
                self.docs.set_properties(
                    session.doc_id,
                    {"status": IN_PROGRESS, "date": start_dt.date().isoformat()},
                )
                doc_updated = True
            except Exception as exc:
                # Deliberately not "carry on and book anyway": an event with no
                # matching session is the drift this ordering exists to prevent.
                raise StateError(
                    f"Could not mark {learner.name}'s {self.session_label} "
                    f"{session.number} as in progress: {exc}",
                    remedy="Nothing was booked. Fix the document store and re-run: "
                    "the calendar is left untouched so the two cannot disagree.",
                    details={"doc_id": session.doc_id},
                ) from exc

        event = self.calendar.create(
            CalendarEvent(
                id="",
                title=title,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                description=f"{learner.name} - {self.session_label} {session.number}",
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

        # A session with no document has no status to consult; cancelling it
        # is just removing the event. Reading `get_status("")` instead would
        # ask the document store for a page id that is empty: a 404 dressed
        # up as a sharing problem.
        current = ""
        if session.doc_id:
            current = self.vocabulary.canonical(
                self.docs.get_status(session.doc_id, with_blocks=False).status
            )
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

    # -- who is mid-session -------------------------------------------------

    def in_progress(
        self,
        store: LearnerStore,
        *,
        window_days: int = 14,
        today: date | None = None,
    ) -> InProgressReport:
        """Who owes a summary, read from a calendar window.

        The calendar is the index and the page is the truth. Only learners
        with a lesson event inside the window are candidates, so this costs
        one calendar call and one document read per candidate, not a read of
        every session page of every learner. A candidate counts as in
        progress only when its page still says so: a cancelled lesson (event
        removed, page rolled back) and a summarized one (page done) drop out
        on their own.

        A page that cannot be read is reported as unreadable for that learner
        alone; the rest of the report stands. An event that names no learner
        was typed by a person in the calendar and is listed, never guessed
        at. Sessions booked for a future day are outside the window: this
        answers "who still owes a summary", and `calendar list` answers
        "who is coming".
        """
        reference = today or today_in(self.timezone)
        start = combine(
            reference - timedelta(days=window_days - 1), parse_time("00:00"), self.timezone
        )
        end = combine(reference + timedelta(days=1), parse_time("00:00"), self.timezone)
        events = sorted(
            self.calendar.list_between(start.isoformat(), end.isoformat()),
            key=lambda event: event.start,
        )

        by_name = {learner.name: learner for learner in store.list_learners()}
        found: list[tuple[Learner, SessionView]] = []
        unreadable: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for event in events:
            match = self._match_event(event.title, by_name)
            if match is None:
                unmatched.append({"title": event.title, "start": event.start})
                continue
            learner, number = match
            if (learner.id, number) in seen:
                continue
            seen.add((learner.id, number))
            session = next(
                (row for row in store.list_sessions(learner.id) if row.number == number),
                None,
            )
            if session is None or not session.doc_id:
                unmatched.append(
                    {
                        "title": event.title,
                        "start": event.start,
                        "why": f"no document for {self.session_label} {number}",
                    }
                )
                continue
            try:
                doc = self.docs.get_status(session.doc_id, with_blocks=False)
            except BatonError as exc:
                unreadable.append({"learner": learner.name, "number": number, "why": exc.message})
                continue
            state = self.vocabulary.canonical(doc.status)
            if state == IN_PROGRESS:
                found.append((learner, SessionView(session=session, doc=doc, state=state)))

        return InProgressReport(
            found=found,
            unreadable=unreadable,
            unmatched=unmatched,
            window={
                "days": window_days,
                "start": (reference - timedelta(days=window_days - 1)).isoformat(),
                "through": reference.isoformat(),
            },
        )

    def who_is_booked(
        self, store: LearnerStore, day: date
    ) -> tuple[list[Learner], list[dict[str, Any]]]:
        """Every learner with a lesson booked on ``day``, plus unmatched events.

        The same anchored title match ``in_progress`` uses, asked of one day
        instead of a window. A learner booked twice that day appears once. An
        event naming no learner is listed, never guessed at: a person typed
        it, and only they know what it meant.
        """
        start = combine(day, parse_time("00:00"), self.timezone).isoformat()
        end = combine(day + timedelta(days=1), parse_time("00:00"), self.timezone).isoformat()
        events = sorted(self.calendar.list_between(start, end), key=lambda event: event.start)

        by_name = {learner.name: learner for learner in store.list_learners()}
        learners: list[Learner] = []
        unmatched: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in events:
            match = self._match_event(event.title, by_name)
            if match is None:
                unmatched.append({"title": event.title, "start": event.start})
                continue
            learner = match[0]
            if learner.id not in seen:
                seen.add(learner.id)
                learners.append(learner)
        return learners, unmatched

    def _icon_prefixes(self) -> list[str]:
        """The configured icon openings a title Baton writes may carry."""
        icons = {str(value) for value in self.event_emoji.values()}
        if self.default_emoji:
            icons.add(self.default_emoji)
        return [f"{icon} " for icon in icons if icon]

    def _match_event(self, title: str, by_name: dict[str, Learner]) -> tuple[Learner, int] | None:
        """The learner and session a calendar event's title names, if any.

        The same anchored shape the cancel path matches on (an optional
        configured icon, then the name, then the session part) read in the
        opposite direction. Longer names are tried first so a learner whose
        name extends another's cannot be swallowed by the shorter one.
        """
        marker = re.compile(rf"\({re.escape(self.session_label)} (\d+)\)")
        for prefix in [*self._icon_prefixes(), ""]:
            for name in sorted(by_name, key=len, reverse=True):
                head = f"{prefix}{name} ("
                if not title.startswith(head):
                    continue
                tail = title[len(head) - 1 :]
                found = marker.fullmatch(tail)
                if found:
                    return by_name[name], int(found.group(1))
        return None

    def _events_for(self, learner: Learner, day: date) -> list[CalendarEvent]:
        """This learner's events on one day, matched against the title's shape.

        A plain substring cannot tell "Ann (lesson 1)" from "Anna (lesson 3)":
        the shorter name is inside the longer one, and a cancel for Ann
        would delete Anna's lesson too. Baton writes every title itself as
        ``[icon ]Name (label N)``, so the match anchors on that exact shape:
        an optional configured icon, then the name, then the opening
        parenthesis of the session part. The legacy calendar skill learned
        the same lesson and switched to prefix matching for the same reason.
        """
        start = combine(day, parse_time("00:00"), self.timezone).isoformat()
        end = combine(day + timedelta(days=1), parse_time("00:00"), self.timezone).isoformat()
        bare = f"{learner.name} ("
        with_icon = [f"{icon}{bare}" for icon in self._icon_prefixes()]

        def ours(title: str) -> bool:
            return title.startswith(bare) or any(title.startswith(prefix) for prefix in with_icon)

        return [event for event in self.calendar.list_between(start, end) if ours(event.title)]
