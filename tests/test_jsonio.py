"""Crash-safety guarantees of the state layer.

These are the tests that matter most in the whole suite: every resumable
pipeline trusts that a half-written state file is impossible, and that a
corrupt one degrades to the backup instead of taking the run down.
"""

from __future__ import annotations

import json

import pytest

from baton.core import jsonio


def test_round_trip_preserves_non_ascii(tmp_path):
    target = tmp_path / "state.json"
    payload = {"learner": "น้องมานี", "week": 3, "titles": ["Enemy"]}

    jsonio.write_json(target, payload)

    assert jsonio.read_json(target) == payload
    # Thai must survive as characters, not \u escapes — the file is read by
    # people as often as by code.
    assert "น้องมานี" in target.read_text(encoding="utf-8")


def test_write_creates_parent_directories(tmp_path):
    target = tmp_path / "deep" / "nested" / "state.json"

    jsonio.write_json(target, {"ok": True})

    assert jsonio.read_json(target) == {"ok": True}


def test_second_write_snapshots_the_previous_file(tmp_path):
    target = tmp_path / "state.json"

    jsonio.write_json(target, {"generation": 1})
    jsonio.write_json(target, {"generation": 2})

    assert jsonio.read_json(target) == {"generation": 2}
    assert json.loads(jsonio.backup_path(target).read_text(encoding="utf-8")) == {"generation": 1}


def test_backup_can_be_disabled(tmp_path):
    target = tmp_path / "state.json"

    jsonio.write_json(target, {"generation": 1}, backup=False)
    jsonio.write_json(target, {"generation": 2}, backup=False)

    assert not jsonio.backup_path(target).exists()


def test_corrupt_file_falls_back_to_backup(tmp_path):
    target = tmp_path / "state.json"
    jsonio.write_json(target, {"generation": 1})
    jsonio.write_json(target, {"generation": 2})

    # Simulate a truncated write from a power cut.
    target.write_text('{"generation": 2', encoding="utf-8")

    assert jsonio.read_json(target) == {"generation": 1}


def test_corrupt_file_without_backup_returns_default(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("not json at all", encoding="utf-8")

    assert jsonio.read_json(target, default={"fresh": True}) == {"fresh": True}


def test_missing_file_returns_default(tmp_path):
    assert jsonio.read_json(tmp_path / "absent.json", default=[]) == []


def test_no_temp_file_survives_a_successful_write(tmp_path):
    target = tmp_path / "state.json"

    jsonio.write_json(target, {"ok": True})

    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


@pytest.mark.parametrize("text", ["short", "ครูสอนกีตาร์\nบรรทัดที่สอง\n"])
def test_write_text_round_trip(tmp_path, text):
    target = tmp_path / "note.md"

    jsonio.write_text(target, text)

    assert target.read_text(encoding="utf-8") == text
