"""The cleanup ledger and `baton video cleanup`, end to end.

The ledger is the memory of deletions a run could only defer: a clip that
left its learner folder without being trashed still exists in its uploader's
Drive, and after unfiling the pipeline credential can no longer see it. Only
the recorded id can find it again.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from baton.adapters.fakes import FakeMediaSource
from baton.adapters.media.base import GONE, TRASHED, UNFILED, TrashOutcome
from baton.cli.app import run
from baton.core.cleanup import CleanupLedger
from baton.exits import Exit
from baton.pipelines.video import VideoJob

# -- the ledger itself --------------------------------------------------------


def test_unfiled_and_settled_round_trip(tmp_path):
    ledger = CleanupLedger(tmp_path / "cleanup.json")
    job = VideoJob(
        learner_folder="Ada Whitfield",
        learner_name="Ada Whitfield",
        session_number=3,
    )

    ledger.record_unfiled(["c1"], job=job, now="2026-09-11T00:00:00+00:00")
    ledger.mark_cleared(["c2"], now="2026-09-11T00:00:00+00:00")  # no entry: fine

    assert [item.clip_id for item in ledger.pending()] == ["c1"]
    assert ledger.summary() == {"pending": 1, "cleared": 0}

    reread = CleanupLedger(tmp_path / "cleanup.json")  # a fresh instance, as
    # another process would read it
    assert reread.summary() == ledger.summary()

    reread.mark_cleared(["c1"], now="2026-09-11T01:00:00+00:00")
    assert reread.pending() == []
    assert reread.summary() == {"pending": 0, "cleared": 1}


def test_mark_blocked_keeps_the_debt_and_names_the_reason(tmp_path):
    ledger = CleanupLedger(tmp_path / "cleanup.json")
    job = VideoJob(learner_folder="Ada Whitfield")
    ledger.record_unfiled(["c1"], job=job, now="2026-09-11T00:00:00+00:00")

    ledger.mark_blocked(["c1"], reason="this credential cannot trash it (not the owner)")

    (entry,) = ledger.pending()
    assert entry.reason == "this credential cannot trash it (not the owner)"


def test_re_recording_a_cleared_id_reopens_the_debt(tmp_path):
    """A cleared clip is gone; if its id ever unfiles again, that is a new
    file sharing the id's slot in the ledger, and it is owed again."""
    ledger = CleanupLedger(tmp_path / "cleanup.json")
    job = VideoJob(learner_folder="Ada Whitfield")
    ledger.record_unfiled(["c1"], job=job, now="2026-09-11T00:00:00+00:00")
    ledger.mark_cleared(["c1"], now="2026-09-11T00:00:00+00:00")

    ledger.record_unfiled(["c1"], job=job, now="2026-09-12T00:00:00+00:00")

    assert [item.clip_id for item in ledger.pending()] == ["c1"]


# -- `baton video cleanup` ----------------------------------------------------


class _ScriptedSource(FakeMediaSource):
    """Answers each trash call from a script of outcomes by clip id."""

    def __init__(self, script):
        super().__init__([])
        self.script = dict(script)

    def trash(self, clip_ids):
        self.trashed.extend(clip_ids)
        return [TrashOutcome(clip_id, self.script.get(clip_id, TRASHED)) for clip_id in clip_ids]


@pytest.fixture
def video_state(profile):
    """The state directory a `video` run would have left behind."""
    state = profile / "state"
    (state / "video" / "archive").mkdir(parents=True, exist_ok=True)
    current = VideoJob(
        learner_folder="Ada Whitfield",
        learner_name="Ada Whitfield",
        session_number=3,
        clip_ids=["c1", "c2"],
        status="done",
    ).to_dict()
    (state / "video" / "ada_whitfield.json").write_text(json.dumps(current), encoding="utf-8")
    archived = VideoJob(
        learner_folder="Bruno Castell",
        learner_name="Bruno Castell",
        session_number=1,
        clip_ids=["b1"],
        status="done",
    ).to_dict()
    (state / "video" / "archive" / "bruno_castell__s1.json").write_text(
        json.dumps(archived), encoding="utf-8"
    )
    return state


def _patch_source(monkeypatch, script):
    from baton.cli import cmd_video

    monkeypatch.setattr(cmd_video, "_cleanup_source", lambda ctx: _ScriptedSource(script))


def test_cleanup_clears_what_it_can_and_reports_the_rest(profile, video_state, monkeypatch):
    _patch_source(monkeypatch, {"c1": TRASHED, "c2": UNFILED})

    exit_code = run(["--profile", str(profile), "--json", "video", "cleanup", "--rebuild"])

    assert exit_code == int(Exit.UPSTREAM)
    ledger = CleanupLedger.for_state(video_state)
    assert ledger.summary() == {"pending": 1, "cleared": 2}  # c2 stays owed
    (entry,) = ledger.pending()
    assert entry.clip_id == "c2"
    assert "not the owner" in entry.reason


def test_cleanup_exits_zero_when_nothing_remains(profile, video_state, monkeypatch):
    _patch_source(monkeypatch, {"c1": GONE, "c2": TRASHED})

    exit_code = run(["--profile", str(profile), "--json", "video", "cleanup", "--rebuild"])

    assert exit_code == int(Exit.OK)


def test_cleanup_rebuild_seeds_the_backlog_from_job_records(profile, video_state, monkeypatch):
    """The week of unfiled clips that predates the ledger is reconstructible
    from the clip ids the job records kept."""
    _patch_source(monkeypatch, {})

    exit_code = run(["--profile", str(profile), "--json", "video", "cleanup", "--rebuild"])

    assert exit_code == int(Exit.OK)
    assert CleanupLedger.for_state(video_state).summary() == {
        "pending": 0,
        "cleared": 3,  # c1, c2 from the current job, b1 from the archive
    }


def test_a_pending_entry_survives_a_blocked_cleanup_and_clears_later(
    profile, video_state, monkeypatch
):
    ledger = CleanupLedger.for_state(video_state)
    job = replace(VideoJob(learner_folder="Ada Whitfield"))
    ledger.record_unfiled(["c9"], job=job, now="2026-09-11T00:00:00+00:00")

    _patch_source(monkeypatch, {"c9": UNFILED})
    assert run(["--profile", str(profile), "--json", "video", "cleanup"]) == int(Exit.UPSTREAM)

    _patch_source(monkeypatch, {"c9": TRASHED})
    assert run(["--profile", str(profile), "--json", "video", "cleanup"]) == int(Exit.OK)
    assert CleanupLedger.for_state(video_state).pending() == []
