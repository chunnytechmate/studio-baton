"""The two session rules, tested against the shapes that actually break them.

Both rules exist because the obvious implementation is wrong in ways that only
show up weeks later, on one learner, quietly.
"""

from __future__ import annotations

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


# -- rule 2: next free must be unstarted *and* empty -------------------------


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


def test_next_empty_ignores_done_and_in_progress_sessions():
    _, history = build(
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="1", number=2, doc_id="d2"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="Done"),
            "d2": DocStatus(doc_id="d2", status="In progress"),
        },
    )

    assert history.next_empty(history.sessions(ADA)) is None


def test_an_unrecognised_status_is_never_treated_as_free():
    """A studio adds "Cancelled". It is not in docs.statuses, so it maps to
    unknown — and unknown must not be quietly offered as the next free slot."""
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


def test_everyone_in_progress_spans_learners():
    bruno = Learner(id="2", name="Bruno Castell")
    _, history = build(
        learners=[ADA, bruno],
        sessions=[
            Session(id="a", learner_id="1", number=1, doc_id="d1"),
            Session(id="b", learner_id="2", number=9, doc_id="d9"),
        ],
        docs={
            "d1": DocStatus(doc_id="d1", status="In progress"),
            "d9": DocStatus(doc_id="d9", status="In progress"),
        },
    )

    found = history.everyone_in_progress()

    assert [(learner.name, view.number) for learner, view in found] == [
        ("Ada Whitfield", 1),
        ("Bruno Castell", 9),
    ]


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
    assert summary["sessions"]["next_empty"]["number"] == 3
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
