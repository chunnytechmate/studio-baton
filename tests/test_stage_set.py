"""Amending one field of a staged lesson without re-running the stage step.

A typo in the teacher's notes or a wrong title used to mean discarding the
draft and staging again — losing whatever the stage step had gathered. The
amend path is deliberately narrow instead: three plain-text fields, the
summary still only through `lesson ingest`, and a published draft refuses
the amendment because the published record is what the next lesson is
compared against.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit
from baton.pipelines.staging import PUBLISHED, LessonDraft, StagingStore

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"


@pytest.fixture
def studio(profile, monkeypatch):
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            labels:
              learner: student
              session: lesson
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            docs:
              driver: notion
              statuses:
                done: Complete
                in_progress: In progress
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    fake = FakeDocStore(
        statuses={
            "doc-ada-01": DocStatus(doc_id="doc-ada-01", status="Complete", date="2026-05-01"),
            "doc-ada-02": DocStatus(doc_id="doc-ada-02", status="Complete", date="2026-06-01"),
            "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="In progress"),
        }
    )
    for module in ("cmd_learner", "cmd_lesson"):
        monkeypatch.setattr(f"baton.cli.{module}.open_docs", lambda _config: fake)
    return profile


def call(studio, *args):
    return run(["--profile", str(studio), "--json", "lesson", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def text(studio, capsys, *args):
    """Run in human mode and return what the operator reads."""
    assert run(["--profile", str(studio), "lesson", *args]) == Exit.OK
    return capsys.readouterr().out


def staged(studio, capsys, *, context: str = "first attempt"):
    assert call(studio, "stage", "Ada Whitfield", "--session", "3", "--context", context) == Exit.OK
    return out(capsys)


def test_only_the_three_plain_text_fields_are_settable(studio, capsys):
    staged(studio, capsys)

    assert call(studio, "stage-set", "Ada Whitfield", "--field", "summary", "--value", "{}") == (
        Exit.USAGE
    )
    # Refused while parsing, before anything was read or written — and still
    # as an envelope, so an agent driving Baton reads one shape either way.
    payload = out(capsys)
    assert payload["error"] == "usage"
    assert "--field" in payload["message"]

    assert call(studio, "show", "Ada Whitfield") == Exit.OK
    assert out(capsys)["summary"] is None


def test_amending_the_titles_shows_up_in_the_draft(studio, capsys):
    staged(studio, capsys)

    assert (
        call(studio, "stage-set", "Ada Whitfield", "--field", "titles", "--value", "Blackbird")
        == Exit.OK
    )
    payload = out(capsys)
    assert payload["field"] == "titles"
    assert payload["from"] == ""
    assert payload["to"] == "Blackbird"

    assert call(studio, "show", "Ada Whitfield") == Exit.OK
    assert out(capsys)["titles"] == "Blackbird"


def test_the_context_can_be_corrected_without_re_staging(studio, capsys):
    staged(studio, capsys, context="wrote it in a hurry")

    assert (
        call(
            studio, "stage-set", "Ada Whitfield", "--field", "context", "--value", "the real notes"
        )
        == Exit.OK
    )
    assert out(capsys)["from"] == "wrote it in a hurry"

    assert (
        call(
            studio,
            "stage-set",
            "Ada Whitfield",
            "--field",
            "corrected_context",
            "--value",
            "the real notes, spellings fixed",
        )
        == Exit.OK
    )
    assert out(capsys)["to"] == "the real notes, spellings fixed"

    assert call(studio, "show", "Ada Whitfield") == Exit.OK
    draft = out(capsys)
    # The raw notes survive the correction, checkable against what was written.
    assert draft["context"] == "the real notes"
    assert draft["corrected_context"] == "the real notes, spellings fixed"


def test_clearing_a_field_shows_what_was_lost(studio, capsys):
    staged(studio, capsys, context="temporary notes")

    # Read the way an operator reads it: the diff is the whole point of the
    # command, and an emptied field has to say it was emptied rather than
    # printing a blank line and looking like nothing happened.
    printed = text(
        studio, capsys, "stage-set", "Ada Whitfield", "--field", "context", "--value", ""
    )
    assert "เดิม: temporary notes" in printed
    assert "ใหม่: (ว่าง)" in printed

    assert call(studio, "show", "Ada Whitfield") == Exit.OK
    assert out(capsys)["context"] == ""


def test_setting_the_value_it_already_holds_does_nothing(studio, capsys):
    staged(studio, capsys, context="already right")

    assert (
        call(studio, "stage-set", "Ada Whitfield", "--field", "context", "--value", "already right")
        == Exit.OK
    )
    assert out(capsys)["unchanged"] is True

    printed = text(
        studio,
        capsys,
        "stage-set",
        "Ada Whitfield",
        "--field",
        "context",
        "--value",
        "already right",
    )
    assert "Nothing to do" in printed


def test_a_published_draft_refuses_to_be_amended(studio, capsys):
    StagingStore(studio / "state" / "lessons").save(
        LessonDraft(
            learner_id="1",
            learner_name="Ada Whitfield",
            session_number=3,
            status=PUBLISHED,
        )
    )

    assert (
        call(studio, "stage-set", "Ada Whitfield", "--field", "titles", "--value", "Blackbird")
        == Exit.USAGE
    )
    payload = out(capsys)
    assert "already published" in payload["message"]
    assert "unpublish" in payload["remedy"]


def test_amending_needs_a_draft_to_amend(studio, capsys):
    assert (
        call(studio, "stage-set", "Ada Whitfield", "--field", "titles", "--value", "Blackbird")
        == Exit.USAGE
    )
    payload = out(capsys)
    assert "No lesson is staged" in payload["message"]
    assert "stage" in payload["remedy"]
