"""Edges of note text: an empty ``--text`` and what becomes a title.

Two quiet failures of the old heuristics: ``--text ""`` was refused as
"pass exactly one of --text or --file" — telling someone who passed --text to
pass --text — and a note opening with a fence or a table was titled after
that scaffolding line.
"""

from __future__ import annotations

import json

import pytest

from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit


@pytest.fixture
def studio(profile, monkeypatch):
    (profile / "baton.yaml").write_text(
        "version: 1\ntimezone: Asia/Bangkok\nnotes:\n  parent_id_env: BATON_NOTES_PARENT\n",
        encoding="utf-8",
    )
    docs = FakeDocStore()
    monkeypatch.setattr("baton.cli.cmd_notes.open_docs", lambda _config: docs)
    monkeypatch.setenv("BATON_NOTES_PARENT", "parent-1")
    return profile, docs


def call(studio, *args):
    profile, _ = studio
    return run(["--profile", str(profile), "--json", "notes", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_empty_text_is_an_empty_note_not_a_missing_argument(studio, capsys):
    assert call(studio, "push", "--text", "") == Exit.USAGE

    payload = out(capsys)
    assert "empty" in payload["message"].lower()
    assert "exactly one" not in payload["message"]


def test_a_note_opening_with_a_fence_is_not_titled_after_the_fence(studio, capsys):
    _, docs = studio

    assert call(studio, "push", "--text", "```python\nx = 1\n```\nafter the code") == Exit.OK

    assert docs.created_pages[0]["title"] == "after the code"


def test_a_note_opening_with_a_table_row_is_not_titled_after_the_row(studio, capsys):
    _, docs = studio

    assert call(studio, "push", "--text", "| a | b |\n|---|---|\n| 1 | 2 |\nplain words") == Exit.OK

    assert docs.created_pages[0]["title"] == "plain words"


def test_a_note_that_is_only_a_table_gets_the_fallback_title(studio, capsys):
    _, docs = studio

    assert call(studio, "push", "--text", "| a | b |\n|---|---|") == Exit.OK

    assert not docs.created_pages[0]["title"].startswith("|")
