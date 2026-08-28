"""`baton song` end to end, over the real SQLite driver and the shipped seed.

Ada Whitfield is seeded assigned to piece 2 (Blackbird) — the fixture for
the "refuses while a learner is assigned" tests.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.cli.app import run
from baton.exits import Exit

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"


@pytest.fixture
def studio(profile):
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
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return profile


def call(profile, *args):
    return run(["--profile", str(profile), "--json", "song", *args])


def test_list_returns_every_piece(studio, capsys):
    assert call(studio, "list") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["count"] == 4
    assert {item["title"] for item in payload["pieces"]} == {
        "Autumn Leaves",
        "Blackbird",
        "Minuet in G",
        "Take Five",
    }


def test_search_matches_case_insensitively(studio, capsys):
    assert call(studio, "search", "black") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert [item["title"] for item in payload["pieces"]] == ["Blackbird"]


def test_search_with_no_match_still_exits_ok(studio, capsys):
    assert call(studio, "search", "nocturne") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["pieces"] == []


def test_show_names_who_is_assigned(studio, capsys):
    assert call(studio, "show", "2") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["piece"]["title"] == "Blackbird"
    assert [item["name"] for item in payload["used_by"]] == ["Ada Whitfield"]


def test_show_reports_nobody_when_unassigned(studio, capsys):
    assert call(studio, "show", "1") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["used_by"] == []


def test_show_an_unknown_id_is_a_usage_error(studio, capsys):
    assert call(studio, "show", "999") == Exit.USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "999" in payload["message"]


def test_add_creates_a_piece(studio, capsys):
    assert (
        call(
            studio,
            "add",
            "Nocturne No. 2",
            "--sheet-link",
            "https://example.invalid/nocturne.pdf",
        )
        == Exit.OK
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["piece"]["title"] == "Nocturne No. 2"
    assert payload["piece"]["sheet_link"] == "https://example.invalid/nocturne.pdf"

    assert call(studio, "list") == Exit.OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 5


def test_update_changes_only_the_named_fields(studio, capsys):
    assert call(studio, "update", "1", "--title", "Autumn Leaves (Bb)") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["piece"]["title"] == "Autumn Leaves (Bb)"
    assert payload["piece"]["source_link"] == "https://example.invalid/autumn-leaves"
    assert payload["changed"] == ["title"]


def test_update_an_empty_value_clears_the_field(studio, capsys):
    assert call(studio, "update", "1", "--source-link", "") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["piece"]["source_link"] == ""
    assert payload["piece"]["title"] == "Autumn Leaves"


def test_update_a_blank_title_is_refused(studio, capsys):
    assert call(studio, "update", "1", "--title", "   ") == Exit.USAGE


def test_update_with_no_flags_is_refused(studio, capsys):
    assert call(studio, "update", "1") == Exit.USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "Nothing to update" in payload["message"]


def test_update_an_unknown_id_is_a_usage_error_not_a_silent_success(studio, capsys):
    """PostgREST would answer 200 with no rows here — the legacy song manager
    read that as a successful edit. Baton refuses instead."""
    assert call(studio, "update", "999", "--title", "Ghost") == Exit.USAGE


def test_remove_deletes_an_unassigned_piece(studio, capsys):
    assert call(studio, "remove", "1") == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["deleted"] is True

    assert call(studio, "list") == Exit.OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 3


def test_remove_dry_run_deletes_nothing(studio, capsys):
    assert call(studio, "remove", "1", "--dry-run") == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True

    assert call(studio, "list") == Exit.OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 4


def test_remove_refuses_while_a_learner_is_assigned(studio, capsys):
    """Piece 2 (Blackbird) is seeded assigned to Ada Whitfield."""
    assert call(studio, "remove", "2") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)
    assert "Ada Whitfield" in payload["message"]

    assert call(studio, "list") == Exit.OK
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 4


def test_remove_an_unknown_id_is_a_usage_error(studio, capsys):
    assert call(studio, "remove", "999") == Exit.USAGE


def test_a_subcommand_is_required(studio, capsys):
    assert call(studio) == Exit.USAGE


# -- the learner roster shows who is on what ----------------------------------


def test_learner_list_shows_the_assigned_piece(studio, capsys):
    run(["--profile", str(studio), "learner", "list"])
    human = capsys.readouterr().out

    assert "Ada Whitfield" in human
    assert "Blackbird" in human


def test_learner_list_json_is_unchanged_by_the_piece_annotation(studio, capsys):
    run(["--profile", str(studio), "--json", "learner", "list"])
    payload = json.loads(capsys.readouterr().out)

    ada = next(item for item in payload["learners"] if item["name"] == "Ada Whitfield")
    assert set(ada) == {"id", "name", "instrument", "tone", "has_instrument", "current_piece_id"}
