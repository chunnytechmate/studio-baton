"""`baton learner schedule` / `schedule-set`: the weekly slots, end to end.

Real SQLite against the shipped migration (which seeds a few slots); no
document store is opened, so none is faked here. The clash gate gets its own
group: it is the rule that keeps one weekday-hour in one learner's hands.
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
    """A seeded profile; the seed carries Ada Mon 16:00, Bruno Wed 15:00 and
    16:00, Clara Fri 17:00, and Devon nothing."""
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
              aliases:
                ada: Ada Whitfield
            docs:
              driver: notion
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return profile


def call(studio, *args):
    return run(["--profile", str(studio), "--json", "learner", *args])


def payload(capsys):
    return json.loads(capsys.readouterr().out)


def drain(capsys):
    """Drop one command's output so the next `payload` reads one document."""
    capsys.readouterr()


def slots_without_ids(body):
    return [
        {"learner_id": slot["learner_id"], "weekday": slot["weekday"], "start": slot["start"]}
        for slot in body["slots"]
    ]


def test_schedule_reads_the_seeded_slots(studio, capsys):
    assert call(studio, "schedule", "Ada Whitfield") == Exit.OK

    slots = payload(capsys)["slots"]
    assert slots == [{"id": "1", "learner_id": "1", "weekday": "Monday", "start": "16:00"}]


def test_schedule_orders_monday_first_then_time(studio, capsys):
    assert call(studio, "schedule", "Bruno Castell") == Exit.OK

    slots = payload(capsys)["slots"]
    assert [slot["weekday"] for slot in slots] == ["Wednesday", "Wednesday"]
    assert [slot["start"] for slot in slots] == ["15:00", "16:00"]


def test_schedule_set_replaces_the_whole_set(studio, capsys):
    assert (
        call(
            studio,
            "schedule-set",
            "Devon Marsh",
            "--slot",
            "Tuesday 09:00",
            "--slot",
            "Tuesday 10:00",
        )
        == Exit.OK
    )
    assert payload(capsys)["slots"][0]["weekday"] == "Tuesday"

    # The next full set drops the first hour: replace-all, not merge.
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "Tuesday 10:00") == Exit.OK
    drain(capsys)
    assert call(studio, "schedule", "Devon Marsh") == Exit.OK
    assert slots_without_ids(payload(capsys)) == [
        {"learner_id": "4", "weekday": "Tuesday", "start": "10:00"}
    ]


def test_schedule_set_with_no_slot_clears(studio, capsys):
    assert call(studio, "schedule-set", "Ada Whitfield") == Exit.OK
    assert payload(capsys)["slots"] == []
    assert call(studio, "schedule", "Ada Whitfield") == Exit.OK
    assert payload(capsys)["slots"] == []


def test_schedule_set_accepts_lowercase_and_pads_the_time(studio, capsys):
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "sunday 9:00") == Exit.OK

    slots = payload(capsys)["slots"]
    assert slots[0]["weekday"] == "Sunday"
    assert slots[0]["start"] == "09:00"


def test_schedule_set_refuses_a_duplicate_in_one_request(studio, capsys):
    doubled = ["--slot", "Monday 10:00", "--slot", "Monday 10:00"]
    assert call(studio, "schedule-set", "Devon Marsh", *doubled) == Exit.USAGE
    drain(capsys)
    # Nothing was written along the way.
    assert call(studio, "schedule", "Devon Marsh") == Exit.OK
    assert payload(capsys)["slots"] == []


@pytest.mark.parametrize(
    "slot",
    ["Monday", "Monday 16", "16:00 Monday", "Funday 16:00", "Monday 24:00", "Monday 16:60"],
)
def test_schedule_set_refuses_a_malformed_slot(studio, slot):
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", slot) == Exit.USAGE


def test_a_slot_held_by_another_active_learner_refuses_the_save(studio, capsys):
    """Ada holds Monday 16:00; Devon asking for it exits GATE and names her,
    with nothing written for Devon either way."""
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "Monday 16:00") == Exit.GATE

    error = payload(capsys)
    assert "Ada Whitfield" in error["message"]

    assert call(studio, "schedule", "Devon Marsh") == Exit.OK
    assert payload(capsys)["slots"] == []
    # Ada's own slot is untouched.
    assert call(studio, "schedule", "Ada Whitfield") == Exit.OK
    assert payload(capsys)["slots"] != []


def test_dry_run_reports_the_clash_it_would_refuse(studio):
    """The gate runs before the dry-run branch, same order as `calendar
    book`: a dry run must not say "would" about a save that could not happen."""
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "Monday 16:00", "--dry-run") == (
        Exit.GATE
    )


def test_an_inactive_learners_slot_does_not_clash(studio, capsys):
    assert call(studio, "deactivate", "Ada Whitfield") == Exit.OK
    capsys.readouterr()

    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "Monday 16:00") == Exit.OK
    assert payload(capsys)["slots"] != []


def test_learner_list_carries_slots(studio, capsys):
    assert call(studio, "list") == Exit.OK

    learners = payload(capsys)["learners"]
    by_name = {item["name"]: item for item in learners}
    assert by_name["Ada Whitfield"]["slots"] == [
        {"id": "1", "learner_id": "1", "weekday": "Monday", "start": "16:00"}
    ]
    assert by_name["Devon Marsh"]["slots"] == []
    # The trashed roster stays lean: no seed learner is trashed, and the
    # empty answer carries no slots key anywhere.
    assert call(studio, "list", "--trashed") == Exit.OK
    assert payload(capsys)["count"] == 0


def test_dry_run_writes_nothing(studio, capsys):
    assert call(studio, "schedule-set", "Devon Marsh", "--slot", "Monday 10:00", "--dry-run") == (
        Exit.OK
    )
    body = payload(capsys)
    assert body["dry_run"] is True

    assert call(studio, "schedule", "Devon Marsh") == Exit.OK
    assert payload(capsys)["slots"] == []
