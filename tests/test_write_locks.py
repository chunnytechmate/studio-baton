"""Two agents, one profile, one writer at a time.

`video` has always held a whole-run lock. The commands that publish, book, and
send did not, and in this studio's actual deployment there are two agents (a
Claude Code session and an OpenClaw container) pointed at the same profile,
neither of which knows the other exists. Exit 8 already means "something else
is in the way"; these tests are about the commands that never used to say it.
"""

from __future__ import annotations

import json

import pytest
from tests.test_send import call, publish, studio  # noqa: F401 - fixture reuse

from baton.cli.app import run
from baton.core.jobs import run_lock
from baton.exits import Exit


@pytest.fixture
def held(studio):  # noqa: F811
    """A lock taken by "another run", released when the test ends."""
    profile = studio[0]

    def take(name: str):
        lock = run_lock(profile / "state", name)
        lock.acquire()
        return lock

    locks = []
    yield lambda name: locks.append(take(name))
    for lock in locks:
        lock.release()


def test_a_send_waits_for_the_send_already_running(studio, held, capsys):  # noqa: F811
    profile, messenger, _docs = studio
    publish(profile, "1")
    held("send")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher") == Exit.RUNNING
    payload = json.loads(capsys.readouterr().out)

    assert payload["error"] == "running"
    # Nothing was sent, which is the whole reason the collision is worth
    # reporting rather than queueing behind.
    assert messenger.sent == []


def test_a_dry_run_is_not_blocked_by_a_send_in_flight(studio, held, capsys):  # noqa: F811
    """Inspecting a gate writes nothing, so refusing to inspect buys nothing."""
    profile, messenger, _docs = studio
    publish(profile, "1")
    held("send")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher", "--dry-run") == Exit.OK
    assert messenger.sent == []


def test_listing_recordings_is_not_blocked_by_a_send_in_flight(studio, held, capsys):  # noqa: F811
    """`send recording` with no --pick is a listing wearing a sender's name."""
    held("send")

    # Exit 3 is the listing's own answer: "which one goes out?": reached only
    # because the lock let a read-only invocation through.
    assert call(studio, "recording", "Ada Whitfield") in (Exit.NEEDS_HUMAN, Exit.GATE)


def test_publishing_waits_for_the_publish_already_running(studio, held, capsys):  # noqa: F811
    profile, _messenger, _docs = studio
    held("lesson")

    code = run(["--profile", str(profile), "--json", "lesson", "publish", "Ada Whitfield"])

    assert code == Exit.RUNNING


def test_booking_waits_for_the_booking_already_running(studio, held, capsys):  # noqa: F811
    profile, _messenger, _docs = studio
    held("calendar")

    code = run(
        [
            "--profile",
            str(profile),
            "--json",
            "calendar",
            "book",
            "Ada Whitfield",
            "today",
            "17:00",
        ]
    )

    assert code == Exit.RUNNING


def test_the_locks_are_separate_per_workflow(studio, held, capsys):  # noqa: F811
    """A video encode running all evening must not stop the day's sends."""
    profile, _messenger, _docs = studio
    publish(profile, "1")
    held("video")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher") == Exit.OK
