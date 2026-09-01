"""Which lesson a recording is filed against.

The video pipeline reads a status it never writes. Only `lesson publish` moves
a session to Done, and the recording still belongs to that lesson afterwards,
so what the status says has to be enough to find it either way round.

The bug these cover: publishing the summary first left nothing in progress, and
the old fallback answered with the next *empty* page. The recording went onto a
week nobody had taught yet, and the lesson it came from was then reported as
having no video, after the upload to YouTube had already succeeded.
"""

from __future__ import annotations

from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import FakeDocStore, FakeLearnerStore
from baton.cli.cmd_video import session_for_recording
from baton.domain.models import Learner, Session
from baton.domain.status import StatusVocabulary
from baton.pipelines.learner import LearnerHistory

VOCAB = StatusVocabulary.from_config(
    {"done": "Done", "in_progress": "In progress", "not_started": "Not started"}
)
ADA = Learner(id="1", name="Ada Whitfield", instrument="guitar")

# Three booked weeks and one that has not been taught, which is the shape of
# every learner mid-course.
SESSIONS = [
    Session(id="a", learner_id="1", number=3, doc_id="d3"),
    Session(id="b", learner_id="1", number=4, doc_id="d4"),
]


def history(docs: dict) -> LearnerHistory:
    return LearnerHistory(
        FakeLearnerStore(learners=[ADA], sessions=list(SESSIONS)),
        FakeDocStore(statuses=docs),
        VOCAB,
    )


def test_a_booked_lesson_in_progress_is_the_answer():
    found = history(
        {
            "d3": DocStatus(doc_id="d3", status="In progress", date="2026-09-01"),
            "d4": DocStatus(doc_id="d4", status="Not started"),
        }
    )

    assert session_for_recording(found, ADA) == (3, "d3")


def test_after_the_summary_is_published_the_recording_still_finds_its_lesson():
    """The whole point. Publishing marks week 3 Done, which leaves nothing in
    progress; week 4 is untaught and must not be the answer."""
    found = history(
        {
            "d3": DocStatus(doc_id="d3", status="Done", date="2026-09-01"),
            "d4": DocStatus(doc_id="d4", status="Not started"),
        }
    )

    assert session_for_recording(found, ADA) == (3, "d3")


def test_an_untaught_page_is_never_the_answer():
    """Guarding the specific regression: with nothing in progress and nothing
    done, there is no lesson to file against, and the run has to stop instead
    of writing onto a page nobody has taught."""
    found = history(
        {
            "d3": DocStatus(doc_id="d3", status="Not started"),
            "d4": DocStatus(doc_id="d4", status="Not started"),
        }
    )

    assert session_for_recording(found, ADA) is None


def test_a_lesson_in_progress_outranks_a_finished_one():
    """Ordinary case: last week is Done, this week is booked and taught."""
    found = history(
        {
            "d3": DocStatus(doc_id="d3", status="Done", date="2026-08-25"),
            "d4": DocStatus(doc_id="d4", status="In progress", date="2026-09-01"),
        }
    )

    assert session_for_recording(found, ADA) == (4, "d4")


def test_the_most_recent_finished_lesson_wins_by_date():
    """`latest_done` orders by the document's date, so a studio that backfills
    a date does not get last month's lesson because it has a higher number."""
    found = history(
        {
            "d3": DocStatus(doc_id="d3", status="Done", date="2026-09-01"),
            "d4": DocStatus(doc_id="d4", status="Done", date="2026-08-01"),
        }
    )

    assert session_for_recording(found, ADA) == (3, "d3")
