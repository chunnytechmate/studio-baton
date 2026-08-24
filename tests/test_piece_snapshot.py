"""A staged lesson keeps the Song DB row that belonged to that lesson."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import baton
from baton.cli.app import run
from baton.domain.models import Piece
from baton.errors import UsageError
from baton.exits import Exit
from baton.pipelines.staging import LessonDraft, PieceSnapshot

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"


@pytest.fixture
def studio(profile):
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.close()
    return profile, db_path


def call(profile: Path, *args: str) -> int:
    return run(["--profile", str(profile), "--json", "lesson", *args])


def output(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_snapshot_states_round_trip_without_conflating_legacy_state():
    piece = Piece(
        id="7",
        title="Fictional Study",
        source_link="https://example.invalid/source",
        practice_track="https://example.invalid/practice",
        sheet_link="https://example.invalid/sheet",
    )
    captured = PieceSnapshot.capture(piece)
    restored = PieceSnapshot.from_record({"piece_snapshot": captured.to_dict()})
    deliberate_none = PieceSnapshot.capture(None)
    legacy = PieceSnapshot.from_record({})

    assert restored == captured
    assert restored.same_content(
        PieceSnapshot(status="captured", captured_at="different", piece=piece)
    )
    assert deliberate_none.status == "none"
    assert PieceSnapshot.from_record(
        {"piece_snapshot": deliberate_none.to_dict()}
    ) == deliberate_none
    assert legacy.status == "unavailable"
    assert not deliberate_none.same_content(legacy)


@pytest.mark.parametrize(
    "value",
    [None, {}, {"status": "captured", "captured_at": "now", "piece": None}],
)
def test_malformed_snapshot_state_fails_closed(value):
    with pytest.raises(UsageError) as excinfo:
        PieceSnapshot.from_record({"piece_snapshot": value})

    assert "re-stage" in (excinfo.value.remedy or "").lower()


def test_contract_uses_the_staged_song_after_live_assignment_changes(studio, capsys):
    profile, db_path = studio
    assert call(profile, "stage", "Ada Whitfield", "--session", "3") == Exit.OK
    output(capsys)

    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE learners SET current_piece_id = 1 WHERE id = 1")
    connection.commit()
    connection.close()

    assert call(profile, "contract", "Ada Whitfield") == Exit.OK
    context = output(capsys)["context"]

    assert context["current_piece"]["title"] == "Blackbird"
    assert "current_piece_id" not in context["learner"]

    assert call(profile, "show", "Ada Whitfield") == Exit.OK
    snapshot = output(capsys)["piece_snapshot"]
    assert snapshot["status"] == "captured"
    assert snapshot["piece"]["id"] == "2"


def test_no_song_is_explicit_but_a_dangling_song_id_saves_nothing(studio, capsys):
    profile, db_path = studio
    assert call(profile, "stage", "Devon Marsh", "--session", "1") == Exit.OK
    output(capsys)
    assert call(profile, "show", "Devon Marsh") == Exit.OK
    assert output(capsys)["piece_snapshot"]["status"] == "none"

    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE learners SET current_piece_id = 999 WHERE id = 1")
    connection.commit()
    connection.close()

    assert call(profile, "stage", "Ada Whitfield", "--session", "3") == Exit.USAGE
    error = output(capsys)
    assert "999" in error["message"]
    assert "clear" in error["remedy"].lower()

    assert call(profile, "show", "Ada Whitfield") == Exit.USAGE
    assert "stage" in output(capsys)["remedy"].lower()


def test_a_legacy_draft_deserializes_as_snapshot_unavailable():
    draft = LessonDraft.from_dict(
        {"learner_id": "1", "learner_name": "Ada", "session_number": 3}
    )

    assert draft.piece_snapshot.status == "unavailable"
