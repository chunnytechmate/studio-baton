"""The two session rules, tested against the shapes that actually break them.

Both rules exist because the obvious implementation is wrong in ways that only
show up weeks later, on one learner, quietly.
"""

from __future__ import annotations

from datetime import date

import pytest

from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import FakeDocStore, FakeLearnerStore
from baton.domain.models import Learner, Piece, Session
from baton.domain.status import StatusVocabulary
from baton.pipelines.learner import LearnerHistory

VOCAB = StatusVocabulary.from_config(
    {"done": "Done", "in_progress": "In progress", "not_started": "Not started"}
)

ADA = Learner(id="1", name="Ada Whitfield", instrument="guitar", current_piece_id="7")


def build(sessions, docs, *, learners=(ADA,), pieces=()):
    """A history over fakes, with sessions and their document states."""
    store = FakeLearnerStore(learners=list(learners), sessions=list(sessions), pieces=list(pieces))
    doc_store = FakeDocStore(statuses=docs)
    return store, LearnerHistory(store, doc_store, VOCAB)


# -- rule 1: latest done is not the highest number --------------------------


def test_latest_done_ignores_a_higher_numbered_unstarted_session():
    """The rule in one test. Session 12 exists but has not happened; the
    answer is 3. Taking the highest number would report 12 and every
    downstream summary would be filed against the wrong session."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=3, doc_id="d3"),
            Session(id="b", learner_id="1", number=12, doc_id="d12"),
        ],
        docs={
            "d3": DocStatus(doc_id="d3", status="Done", date="2026-05-09"),
            "d12": DocStatus(doc_id="d12", status="Not started"),
        },
    )

    latest = history.latest_done(history.sessions(ADA))

    assert latest is not None
    assert latest.number == 3


def test_latest_done_uses_the_date_not_the_number():
    """Sessions get backfilled out of order. The newest date wins."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=4, doc_id="d4"),
            Session(id="b", learner_id="1", number=5, doc_id="d5"),
        ],
        docs={
            "d4": DocStatus(doc_id="d4", status="Done", date="2026-07-01"),
            "d5": DocStatus(doc_id="d5", status="Done", date="2026-06-01"),
        },
    )

    assert history.latest_done(history.sessions(ADA)).number == 4


def test_latest_done_falls_back_to_the_number_when_dates_are_missing():
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done"),
            "d2": DocStatus(doc_id="d2", status="Done"),
        },
    )

    assert history.latest_done(history.sessions(ADA)).number == 2


def test_latest_done_is_none_when_nothing_has_happened_yet():
    _, history = build(
        sessions=[Session(id="a", learner_id="1", number=1, doc_id="d1")],
        docs={"d1": DocStatus(doc_id="d1", status="In progress")},
    )

    assert history.latest_done(history.sessions(ADA)) is None


def test_skipped_sessions_do_not_confuse_the_answer():
    """Weeks 2 and 4 were cancelled and never marked. 5 is still the answer."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
            Session(id="c", learner_id="1", number=4, doc_id="d4"),
            Session(id="d", learner_id="1", number=5, doc_id="d5"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done", date="2026-01-05"),
            "d2": DocStatus(doc_id="d2", status="Not started"),
            "d4": DocStatus(doc_id="d4", status="Not started"),
            "d5": DocStatus(doc_id="d5", status="Done", date="2026-02-02"),
        },
    )

    assert history.latest_done(history.sessions(ADA)).number == 5


# -- rule 2: next free is where a new lesson may land ------------------------


def test_next_empty_skips_an_unstarted_page_that_already_has_content():
    """The dangerous case. Session 2 says "Not started" but somebody has
    already written on it; returning it as free would overwrite their work."""
    store = FakeLearnerStore(
        learners=[ADA],
        sessions=[
            Session(id="a", learner_id="1", number=2, doc_id="d2"),
            Session(id="b", learner_id="1", number=3, doc_id="d3"),
        ],
    )
    docs = FakeDocStore(
        statuses={
            "d2": DocStatus(doc_id="d2", status="Not started"),
            "d3": DocStatus(doc_id="d3", status="Not started"),
        },
        blocks={"d2": [_block("b1")]},  # content already present
    )
    history = LearnerHistory(store, docs, VOCAB)

    assert history.next_empty(history.sessions(ADA)).number == 3


def test_next_empty_takes_the_lowest_qualifying_session():
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=7, doc_id="d7"),
            Session(id="b", learner_id="1", number=4, doc_id="d4"),
        ],
        docs={
            "d4": DocStatus(doc_id="d4", status="Not started"),
            "d7": DocStatus(doc_id="d7", status="Not started"),
        },
    )

    assert history.next_empty(history.sessions(ADA)).number == 4


def test_next_empty_takes_a_fresh_in_progress_page():
    """The studio's own flow: book a lesson, the page turns In progress, the
    summary is written onto *that* page. Pointing anywhere else would file the
    lesson against the wrong week."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done"),
            "d2": DocStatus(doc_id="d2", status="In progress", date="2026-01-10"),
        },
    )

    assert history.next_empty(history.sessions(ADA), today=date(2026, 1, 10)).number == 2


def test_next_empty_passes_over_an_in_progress_page_left_stale():
    """A page still In progress well past its day is abandoned, not the
    lesson happening now: one missed week must not hold every later week
    hostage."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="In progress", date="2026-01-05"),
            "d2": DocStatus(doc_id="d2", status="Not started"),
        },
    )

    assert history.next_empty(history.sessions(ADA), today=date(2026, 1, 10)).number == 2


def test_null_stale_days_never_abandons_a_page():
    """`next_stale_days: null` is the legacy behaviour: an In-progress page
    stays the target no matter how old, until it is marked done."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="In progress", date="2026-01-05"),
            "d2": DocStatus(doc_id="d2", status="Not started"),
        },
    )
    history.next_stale_days = None

    assert history.next_empty(history.sessions(ADA), today=date(2026, 1, 10)).number == 1


def test_an_in_progress_page_with_no_date_stays_the_target():
    """No date means staleness cannot be proven; the page is returned rather
    than quietly skipped."""
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="In progress"),
            "d2": DocStatus(doc_id="d2", status="Not started"),
        },
    )

    assert history.next_empty(history.sessions(ADA), today=date(2026, 1, 10)).number == 1


def test_next_empty_still_ignores_done_sessions():
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done", date="2026-01-05"),
            "d2": DocStatus(doc_id="d2", status="Done", date="2026-01-06"),
        },
    )

    assert history.next_empty(history.sessions(ADA)) is None


def test_an_unrecognised_status_is_never_treated_as_free():
    """A studio adds "Cancelled". It is not in docs.statuses, so it maps to
    unknown, and unknown must not be quietly offered as the next free slot."""
    _, history = build(
        sessions=[Session(id="a", learner_id="1", number=1, doc_id="d1")],
        docs={"d1": DocStatus(doc_id="d1", status="Cancelled")},
    )

    views = history.sessions(ADA)

    assert views[0].state == ""
    assert history.next_empty(views) is None
    assert history.latest_done(views) is None


# -- joining -----------------------------------------------------------------


def test_a_session_with_no_document_is_treated_as_not_started():
    _, history = build(
        sessions=[Session(id="a", learner_id="1", number=1, doc_id="")],
        docs={},
    )
    views = history.sessions(ADA)

    assert views[0].state == "not_started"
    assert views[0].is_empty is True


def test_status_matching_tolerates_case_and_stray_spaces():
    """Statuses are typed by hand into a document. "  in progress " is the
    same state as "In progress", and treating them differently would hide a
    session from the morning list."""
    _, history = build(
        sessions=[Session(id="a", learner_id="1", number=1, doc_id="d1")],
        docs={"d1": DocStatus(doc_id="d1", status="  IN PROGRESS  ")},
    )

    assert history.sessions(ADA)[0].state == "in_progress"


def test_sessions_come_back_ordered_by_number_despite_parallel_reads():
    _, history = build(
        sessions=[
            Session(id=str(n), learner_id="1", number=n, doc_id=f"d{n}") for n in (5, 1, 3, 2, 4)
        ],
        docs={f"d{n}": DocStatus(doc_id=f"d{n}", status="Done") for n in range(1, 6)},
    )

    assert [view.number for view in history.sessions(ADA)] == [1, 2, 3, 4, 5]


# The "who still owes a summary" question now lives in Scheduler, answered
# from a calendar window: its tests are in test_calendar.py. Scanning every
# page of every learner was the most expensive call Baton made.


def test_summarise_gathers_the_whole_picture():
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
            Session(id="c", learner_id="1", number=3, doc_id="d3"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done", date="2026-01-01"),
            "d2": DocStatus(doc_id="d2", status="In progress"),
            "d3": DocStatus(doc_id="d3", status="Not started"),
        },
        pieces=[Piece(id="7", title="Blackbird")],
    )

    summary = history.summarise(ADA, history.sessions(ADA))

    assert summary["current_piece"]["title"] == "Blackbird"
    assert summary["sessions"]["total"] == 3
    assert summary["sessions"]["done"] == 1
    assert summary["sessions"]["latest_done"]["number"] == 1
    # Session 2 is In progress with no date: it cannot be proven stale, so it
    # is the target, not the untouched session 3 after it.
    assert summary["sessions"]["next_empty"]["number"] == 2
    assert [v["number"] for v in summary["sessions"]["in_progress"]] == [2]


def test_a_learner_with_no_sessions_summarises_cleanly():
    _, history = build(sessions=[], docs={})

    summary = history.summarise(ADA, history.sessions(ADA))

    assert summary["sessions"]["total"] == 0
    assert summary["sessions"]["latest_done"] is None
    assert summary["sessions"]["next_empty"] is None


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_parallelism_does_not_change_the_result(workers):
    store = FakeLearnerStore(
        learners=[ADA],
        sessions=[
            Session(id=str(n), learner_id="1", number=n, doc_id=f"d{n}") for n in range(1, 7)
        ],
    )
    docs = FakeDocStore(
        statuses={
            f"d{n}": DocStatus(doc_id=f"d{n}", status="Done", date=f"2026-0{n}-01")
            for n in range(1, 7)
        }
    )
    history = LearnerHistory(store, docs, VOCAB, max_parallel_reads=workers)

    assert history.latest_done(history.sessions(ADA)).number == 6


def _block(block_id: str):
    from baton.adapters.docs.base import Block

    return Block(id=block_id, type="paragraph", text="already written")


# -- one bad row must not sink the rest --------------------------------------


def test_an_unreadable_document_does_not_break_the_whole_learner():
    """Found by running against real data: one truncated page id made all
    twelve of a learner's sessions unusable. A single bad row must cost that
    row only."""
    from baton.errors import UpstreamError

    store = FakeLearnerStore(
        learners=[ADA],
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="good-1"),
            Session(id="b", learner_id="1", number=2, doc_id="broken"),
            Session(id="c", learner_id="1", number=3, doc_id="good-3"),
        ],
    )

    class OneBadDocument(FakeDocStore):
        def get_status(self, doc_id):
            if doc_id == "broken":
                raise UpstreamError("page_id is not a valid uuid", service="notion")
            return super().get_status(doc_id)

    docs = OneBadDocument(
        statuses={
            "good-1": DocStatus(doc_id="good-1", status="Done", date="2026-01-01"),
            "good-3": DocStatus(doc_id="good-3", status="Not started"),
        }
    )
    history = LearnerHistory(store, docs, VOCAB)

    views = history.sessions(ADA)

    assert len(views) == 3
    assert history.latest_done(views).number == 1
    assert history.next_empty(views).number == 3


def test_an_unreadable_session_is_reported_rather_than_swallowed():
    """Degrading quietly would hide a broken row forever."""
    history = _history_with_one_bad_row()
    views = history.sessions(ADA)

    broken = next(view for view in views if view.number == 5)
    assert broken.unreadable
    assert broken.to_dict(VOCAB)["unreadable"]
    assert history.summarise(ADA, views)["sessions"]["unreadable"][0]["number"] == 5


def test_an_unreadable_session_is_never_treated_as_done_or_free():
    """Unknown state, and the existing rules already refuse to act on it."""
    history = _history_with_one_bad_row()
    views = history.sessions(ADA)

    broken = next(view for view in views if view.number == 5)
    assert broken.state == ""
    # The good session is done; the broken one is neither done nor free.
    assert history.latest_done(views).number == 1
    assert history.next_empty(views) is None


def _history_with_one_bad_row() -> LearnerHistory:
    """A learner with one good session and one whose page id is malformed:
    the exact shape found in the real data."""
    from baton.errors import UpstreamError

    store = FakeLearnerStore(
        learners=[ADA],
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="good"),
            Session(id="b", learner_id="1", number=5, doc_id="broken"),
        ],
    )

    class OneBadRow(FakeDocStore):
        def get_status(self, doc_id):
            if doc_id == "broken":
                raise UpstreamError("page_id is not a valid uuid", service="notion")
            return super().get_status(doc_id)

    docs = OneBadRow(statuses={"good": DocStatus(doc_id="good", status="Done", date="2026-01-01")})
    return LearnerHistory(store, docs, VOCAB)


def test_every_document_failing_is_an_outage_not_an_empty_answer():
    """The distinction that matters: one bad row degrades, but a total outage
    must not come back as "no free session": a confident wrong answer at
    exactly the moment nothing can be trusted."""
    from baton.errors import UpstreamError

    store = FakeLearnerStore(
        learners=[ADA],
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
    )
    docs = FakeDocStore()
    docs.fail_with = UpstreamError("notion is down", service="notion")
    history = LearnerHistory(store, docs, VOCAB)

    with pytest.raises(UpstreamError) as excinfo:
        history.sessions(ADA)

    assert "outage" in (excinfo.value.remedy or "")


def test_sessions_without_a_document_do_not_count_towards_the_outage_check():
    """A learner whose sessions have no page ids yet is not an outage."""
    store = FakeLearnerStore(
        learners=[ADA], sessions=[Session(id="a", learner_id="1", number=1, doc_id="")]
    )
    history = LearnerHistory(store, FakeDocStore(), VOCAB)

    assert history.sessions(ADA)[0].state == "not_started"
