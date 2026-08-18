"""Published records, separated by the identity inside them.

The filename is an index built from a learner id that may contain `-`, so a
glob for one learner can sweep in another's records. These tests pin the
separation: what `latest` returns must belong to the learner it was asked
about, by the id stored in the record, not by the filename's prefix.
"""

from __future__ import annotations

from pathlib import Path

import baton.pipelines.staging as staging
from baton.pipelines.staging import LessonDraft, PublishedRecord


class _Clock:
    """`_now()` has second precision; two saves in one second would tie."""

    def __init__(self) -> None:
        self.tick = 0

    def __call__(self) -> str:
        self.tick += 1
        return f"2026-08-17T00:00:{self.tick:02d}+00:00"


def _draft(learner_id: str, name: str, session: int) -> LessonDraft:
    return LessonDraft(
        learner_id=learner_id,
        learner_name=name,
        session_number=session,
        doc_id=f"doc-{learner_id}-{session}",
        titles="Blackbird",
    )


def test_latest_never_returns_another_learners_record(tmp_path: Path):
    """`ada` and `ada-1`: the shorter id is a prefix of the longer one, and a
    bare glob for `ada-*` picks up `ada-1`'s files. The message composed for
    one learner must never be handed to the other's family."""
    records = PublishedRecord(tmp_path)
    records.save(_draft("ada-1", "Ada the Second", 4), short_message="for ada-1")
    records.save(_draft("ada", "Ada Whitfield", 1), short_message="for ada")

    latest = records.latest("ada")

    assert latest is not None
    assert latest["learner_id"] == "ada"
    assert latest["short_message"] == "for ada"


def test_latest_picks_the_most_recent_among_only_that_learners_records(tmp_path: Path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(staging, "_now", clock)
    records = PublishedRecord(tmp_path)
    records.save(_draft("ada", "Ada Whitfield", 1), short_message="older")
    records.save(_draft("ada-1", "Ada the Second", 2), short_message="the other ada")
    # Same learner, same session, republished: the newer write wins.
    records.save(_draft("ada", "Ada Whitfield", 1), short_message="newer")

    latest = records.latest("ada")

    assert latest is not None
    assert latest["short_message"] == "newer"


def test_a_learner_with_no_records_gets_nothing_even_when_a_neighbour_has(tmp_path: Path):
    records = PublishedRecord(tmp_path)
    records.save(_draft("ada-1", "Ada the Second", 3), short_message="for ada-1")

    assert records.latest("ada") is None
    assert records.latest("ada-1") is not None
