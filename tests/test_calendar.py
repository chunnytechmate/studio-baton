"""Date arithmetic, schedule parsing, and the ordering that keeps the calendar
and the documents from disagreeing.

The ordering tests are the important ones. Every other property here is about
not asking a language model to do arithmetic.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import FakeCalendar, FakeDocStore
from baton.domain.models import Learner, Session
from baton.domain.status import StatusVocabulary
from baton.domain.whenever import parse_date, parse_schedule, parse_time
from baton.errors import GateError, StateError, UpstreamError, UsageError
from baton.exits import Exit
from baton.pipelines.schedule import Scheduler, event_title

VOCAB = StatusVocabulary.from_config(
    {"done": "Done", "in_progress": "In progress", "not_started": "Not started"}
)
ADA = Learner(id="1", name="Ada Whitfield", instrument="guitar")
SESSION = Session(id="s", learner_id="1", number=3, doc_id="doc-3")
TODAY = date(2026, 8, 16)


# -- dates -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2026-08-20", date(2026, 8, 20)),
        ("today", date(2026, 8, 16)),
        ("tomorrow", date(2026, 8, 17)),
        ("yesterday", date(2026, 8, 15)),
        ("+3", date(2026, 8, 19)),
        ("-2", date(2026, 8, 14)),
        ("TOMORROW", date(2026, 8, 17)),
        ("  today  ", date(2026, 8, 16)),
    ],
)
def test_date_expressions_resolve(expression, expected):
    assert parse_date(expression, reference=TODAY) == expected


def test_configured_shorthand_resolves():
    """Thai studios type พน for tomorrow; another studio types something else,
    which is why the tokens are configuration rather than code."""
    shorthand = {"พน": 1, "วน": 0, "มร": 2}

    assert parse_date("พน", shorthand=shorthand, reference=TODAY) == date(2026, 8, 17)
    assert parse_date("มร", shorthand=shorthand, reference=TODAY) == date(2026, 8, 18)


def test_an_offset_crosses_a_month_boundary():
    assert parse_date("+20", reference=date(2026, 8, 16)) == date(2026, 9, 5)


def test_an_unknown_expression_lists_what_is_understood():
    with pytest.raises(UsageError) as excinfo:
        parse_date("next tuesday", shorthand={"พน": 1}, reference=TODAY)

    assert "today" in excinfo.value.details["understood"]
    assert "พน" in excinfo.value.details["understood"]


def test_an_impossible_date_is_rejected_rather_than_rounded():
    with pytest.raises(UsageError):
        parse_date("2026-02-30", reference=TODAY)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("17:00", time(17, 0)), ("17.00", time(17, 0)), ("9", time(9, 0)), ("09:05", time(9, 5))],
)
def test_time_expressions_resolve(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize("value", ["25:00", "12:75", "half past", ""])
def test_impossible_times_are_rejected(value):
    with pytest.raises(UsageError):
        parse_time(value)


# -- schedule parsing --------------------------------------------------------


def test_a_slot_ends_when_the_next_one_begins():
    """How a back-to-back teaching day actually works."""
    rows = parse_schedule("17:00 Ada\n18:00 Bruno")

    assert rows == [(time(17, 0), time(18, 0), "Ada"), (time(18, 0), time(19, 0), "Bruno")]


def test_a_free_period_is_skipped_but_still_bounds_the_slot_before_it():
    """Without this the 17:00 lesson silently becomes two hours long."""
    rows = parse_schedule("17:00 Ada\n18:00 -\n19:00 Bruno")

    assert rows[0] == (time(17, 0), time(18, 0), "Ada")
    assert [row[2] for row in rows] == ["Ada", "Bruno"]


@pytest.mark.parametrize("marker", ["-", "—", "free", "ว่าง"])
def test_every_free_marker_is_recognised(marker):
    rows = parse_schedule(f"17:00 Ada\n18:00 {marker}")

    assert len(rows) == 1


def test_lines_are_sorted_so_an_out_of_order_list_still_works():
    rows = parse_schedule("19:00 Clara\n17:00 Ada\n18:00 Bruno")

    assert [row[2] for row in rows] == ["Ada", "Bruno", "Clara"]


def test_the_last_slot_gets_the_default_length():
    rows = parse_schedule("17:00 Ada", default_minutes=45)

    assert rows[0][1] == time(17, 45)


def test_a_line_without_a_time_names_the_line():
    with pytest.raises(UsageError) as excinfo:
        parse_schedule("17:00 Ada\nBruno at some point")

    assert "Line 2" in str(excinfo.value)


def test_blank_lines_are_ignored():
    assert len(parse_schedule("\n17:00 Ada\n\n18:00 Bruno\n\n")) == 2


# -- titles ------------------------------------------------------------------


def test_the_title_carries_the_instrument_emoji():
    title = event_title(ADA, 3, session_label="lesson", emoji={"guitar": "🎸", "drums": "🥁"})

    assert title == "🎸 Ada Whitfield (lesson 3)"


def test_an_unmapped_instrument_falls_back_to_the_default():
    title = event_title(ADA, 3, emoji={"drums": "🥁"}, default_emoji="🎵")

    assert title.startswith("🎵 ")


def test_no_emoji_configured_means_no_prefix():
    assert event_title(ADA, 3) == "Ada Whitfield (week 3)"


# -- the ordered gate --------------------------------------------------------


def build(*, doc_status="Not started", docs_fail=None, calendar_fail=None):
    docs = FakeDocStore(statuses={"doc-3": DocStatus(doc_id="doc-3", status=doc_status)})
    docs.fail_with = docs_fail
    calendar = FakeCalendar()
    calendar.fail_with = calendar_fail
    scheduler = Scheduler(calendar, docs, VOCAB, timezone="Asia/Bangkok", session_label="lesson")
    return scheduler, calendar, docs


def test_booking_marks_the_document_then_creates_the_event():
    scheduler, calendar, docs = build()

    result = scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    assert result.doc_updated is True
    assert docs.get_status("doc-3").status == "in_progress"
    assert len(calendar.events) == 1
    assert calendar.events[0].start.startswith("2026-08-20T17:00:00+07:00")


def test_booking_the_same_session_twice_in_one_day_is_refused():
    """A re-submitted booking used to leave two identical events, and a later
    cancel removed only one of them — the calendar said the lesson existed
    after it had been cancelled."""
    scheduler, calendar, _docs = build()

    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    with pytest.raises(GateError) as excinfo:
        scheduler.book(ADA, SESSION, date(2026, 8, 20), "18:00")

    assert len(calendar.events) == 1
    assert "already" in excinfo.value.message
    assert "Nothing was booked twice" in (excinfo.value.remedy or "")
    assert f"event {calendar.events[0].id}" in excinfo.value.message


def test_a_dry_run_reports_the_duplicate_it_would_refuse():
    scheduler, calendar, _docs = build()

    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    with pytest.raises(GateError):
        scheduler.book(ADA, SESSION, date(2026, 8, 20), "18:00", dry_run=True)

    assert len(calendar.events) == 1


def test_a_second_different_session_the_same_day_is_a_real_double_lesson():
    """Two lessons in one day is a real studio arrangement; only a second
    copy of the *same* session is a re-submission."""
    docs = FakeDocStore(
        statuses={
            "doc-3": DocStatus(doc_id="doc-3", status="In progress"),
            "doc-4": DocStatus(doc_id="doc-4", status="Not started"),
        }
    )
    calendar = FakeCalendar()
    scheduler = Scheduler(calendar, docs, VOCAB, timezone="Asia/Bangkok", session_label="lesson")
    later = Session(id="s4", learner_id="1", number=4, doc_id="doc-4")
    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    result = scheduler.book(ADA, later, date(2026, 8, 20), "10:00")

    assert result.event is not None
    assert len(calendar.events) == 2


def test_no_event_is_created_when_the_document_update_fails():
    """The property this ordering exists for. An event with no matching session
    is the drift that made the original system untrustworthy."""
    scheduler, calendar, _docs = build(docs_fail=UpstreamError("notion down", service="notion"))

    with pytest.raises(StateError) as excinfo:
        scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    assert calendar.events == []
    assert "Nothing was booked" in (excinfo.value.remedy or "")


def test_the_end_time_defaults_to_an_hour_later():
    scheduler, calendar, _docs = build()

    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    assert calendar.events[0].end.startswith("2026-08-20T18:00:00")


def test_an_explicit_end_time_is_used():
    scheduler, calendar, _docs = build()

    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00", "17:45")

    assert calendar.events[0].end.startswith("2026-08-20T17:45:00")


def test_dry_run_touches_neither_store():
    scheduler, calendar, docs = build()

    result = scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00", dry_run=True)

    assert calendar.events == []
    assert docs.get_status("doc-3").status == "Not started"
    assert result.event.title.endswith("(lesson 3)")


def test_the_event_carries_the_studio_timezone_not_the_servers():
    """A studio in Bangkok on a UTC host would otherwise book seven hours out."""
    scheduler, calendar, _docs = build()

    scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00")

    assert "+07:00" in calendar.events[0].start


# -- cancelling --------------------------------------------------------------


def test_cancelling_removes_the_event_then_rolls_the_document_back():
    scheduler, calendar, docs = build()
    scheduler.book(ADA, SESSION, date(2026, 8, 16), "17:00")

    result = scheduler.cancel(ADA, SESSION, date(2026, 8, 16), today=TODAY)

    assert calendar.events == []
    assert docs.get_status("doc-3").status == "not_started"
    assert len(result["deleted"]) == 1


def test_cancelling_outside_the_window_is_blocked():
    """Rewriting last week's records is usually a mistake, not an intention."""
    scheduler, calendar, docs = build()
    scheduler.book(ADA, SESSION, date(2026, 8, 5), "17:00")

    with pytest.raises(GateError) as excinfo:
        scheduler.cancel(ADA, SESSION, date(2026, 8, 5), today=TODAY, rollback_window_days=1)

    assert excinfo.value.exit_code == Exit.GATE
    assert len(calendar.events) == 1  # untouched
    assert docs.get_status("doc-3").status == "in_progress"


def test_the_window_is_configurable():
    scheduler, _calendar, _docs = build()

    result = scheduler.cancel(ADA, SESSION, date(2026, 8, 5), today=TODAY, rollback_window_days=30)

    assert result["status"] == "not_started"


def test_a_completed_session_is_not_cancelled():
    scheduler, _calendar, _docs = build(doc_status="Done")

    with pytest.raises(GateError) as excinfo:
        scheduler.cancel(ADA, SESSION, TODAY, today=TODAY)

    assert "already done" in str(excinfo.value)


def test_cancel_dry_run_changes_nothing():
    scheduler, calendar, docs = build()
    scheduler.book(ADA, SESSION, TODAY, "17:00")

    result = scheduler.cancel(ADA, SESSION, TODAY, today=TODAY, dry_run=True)

    assert result["dry_run"] is True
    assert len(calendar.events) == 1
    assert docs.get_status("doc-3").status == "in_progress"


def test_cancelling_twice_is_not_an_error():
    """The desired state is reached either way; a retry must not fail."""
    scheduler, _calendar, _docs = build()
    scheduler.book(ADA, SESSION, TODAY, "17:00")

    scheduler.cancel(ADA, SESSION, TODAY, today=TODAY)
    again = scheduler.cancel(ADA, SESSION, TODAY, today=TODAY)

    assert again["deleted"] == []


def test_only_this_learners_events_are_cancelled():
    scheduler, calendar, _docs = build()
    scheduler.book(ADA, SESSION, TODAY, "17:00")
    bruno = Learner(id="2", name="Bruno Castell")
    scheduler.book(
        bruno, Session(id="s2", learner_id="2", number=1, doc_id="doc-3"), TODAY, "18:00"
    )

    scheduler.cancel(ADA, SESSION, TODAY, today=TODAY)

    assert [event.title for event in calendar.events] == ["Bruno Castell (lesson 1)"]


# -- a name that is a prefix of another name ---------------------------------


def test_cancelling_ann_does_not_touch_anna():
    """Substring matching made "Ann" a match for "Anna (lesson 3)"; the title
    Baton writes is `Name (label N)`, so the match anchors on that shape."""
    scheduler, calendar, _docs = build()
    ann = Learner(id="1", name="Ann")
    anna = Learner(id="2", name="Anna")
    scheduler.book(ann, Session(id="s1", learner_id="1", number=1, doc_id="doc-3"), TODAY, "17:00")
    scheduler.book(
        anna, Session(id="s2", learner_id="2", number=3, doc_id="doc-3"), TODAY, "18:00"
    )

    scheduler.cancel(ann, Session(id="s1", learner_id="1", number=1, doc_id="doc-3"),
                     TODAY, today=TODAY)

    assert [event.title for event in calendar.events] == ["Anna (lesson 3)"]


def test_the_exact_match_still_works_with_an_instrument_emoji():
    scheduler = Scheduler(
        FakeCalendar(),
        FakeDocStore(statuses={"doc-3": DocStatus(doc_id="doc-3", status="Not started")}),
        VOCAB,
        timezone="Asia/Bangkok",
        session_label="lesson",
        event_emoji={"piano": "🎹"},
        default_emoji="🎵",
    )
    ada = Learner(id="1", name="Ada", instrument="piano")
    adrian = Learner(id="2", name="Adrian", instrument="piano")
    scheduler.book(ada, SESSION, TODAY, "17:00")
    scheduler.book(
        adrian, Session(id="s2", learner_id="2", number=1, doc_id="doc-3"), TODAY, "18:00"
    )

    scheduler.cancel(adrian, Session(id="s2", learner_id="2", number=1, doc_id="doc-3"),
                     TODAY, today=TODAY)

    assert [event.title for event in scheduler.calendar.events] == ["🎹 Ada (lesson 3)"]


def test_cancelling_a_session_with_no_document_removes_the_event():
    """`doc_id=""` is the schema's default; reading its status asked the store
    for a page id that is empty and reported a sharing problem."""
    scheduler, calendar, docs = build()
    bare = Session(id="s9", learner_id="1", number=9, doc_id="")
    scheduler.book(ADA, bare, TODAY, "17:00", dry_run=False)

    result = scheduler.cancel(ADA, bare, TODAY, today=TODAY)

    assert result["deleted"]
    assert calendar.events == []
    # The other session's document was never read or reset.
    assert docs.statuses["doc-3"].status == "Not started"


# -- times that are not a real lesson ----------------------------------------


def test_a_reversed_range_is_refused_rather_than_booked():
    scheduler, _calendar, _docs = build()

    with pytest.raises(UsageError) as excinfo:
        scheduler.book(ADA, SESSION, date(2026, 8, 20), "18:00", "17:00")

    assert "cannot end" in str(excinfo.value)


def test_a_late_start_crossing_midnight_ends_the_next_day():
    """23:30 + 60 minutes is 00:30 tomorrow. Comparing bare `time` values
    rolled it back to 00:30 the same day — an event that ended before it
    began."""
    scheduler, _calendar, _docs = build()

    result = scheduler.book(ADA, SESSION, date(2026, 8, 20), "23:30")

    assert result.event is not None
    assert result.event.end.startswith("2026-08-21T00:30")
    assert result.event.end > result.event.start


def test_an_equal_start_and_end_is_refused():
    scheduler, _calendar, _docs = build()

    with pytest.raises(UsageError):
        scheduler.book(ADA, SESSION, date(2026, 8, 20), "17:00", "17:00")


def test_a_schedule_with_one_name_twice_is_refused(profile):
    """Two slots for one learner hand `_pick_session` the same in-progress
    session twice and put the lesson on the calendar twice; the send batch
    has refused this from the start."""
    from baton.cli.app import run as cli_run

    # The duplicate check sits before any store is opened, so a loadable
    # profile is all the state this needs.
    code = cli_run(
        [
            "--profile", str(profile),
            "calendar", "schedule",
            "--date", "2026-08-20",
            "--text", "17:00 Ada Whitfield\n18:00 ada whitfield",
        ]
    )

    assert code == Exit.USAGE
