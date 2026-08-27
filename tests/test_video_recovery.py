"""What `run` and `resume` owe a folder whose job is already done.

Every scenario here is the 2026-08-27 production night, replayed against
fakes:

* a folder holds next week's clips while this week's job sits done — the run
  must start the new session itself, not wait for a hand-run `video forget`;
* a completed job's trash step says ``moved`` and the source still lists the
  clips — the next run or resume must reclaim them, not report success again
  over the un-trashed files;
* a rebuild of the session page drops the video block a finished step record
  claims is there — the next pass must put it back;
* `resume` must never answer "nothing" while `run` would find work.
"""

from __future__ import annotations

import pytest

from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import (
    FakeDocStore,
    FakeEncoder,
    FakeLearnerStore,
    FakePublisher,
)
from baton.adapters.media.base import EncodeProfile, SourceClip
from baton.domain.models import Learner, Session
from baton.errors import UpstreamError
from baton.pipelines.video import VideoJobStore, VideoPipeline

ADA = Learner(id="1", name="Ada Whitfield", instrument="guitar")

W3_CLIPS = [
    SourceClip(id="w3a", name="IMG_001.MOV", learner_folder="Ada Whitfield", size_bytes=10),
    SourceClip(id="w3b", name="IMG_002.MOV", learner_folder="Ada Whitfield", size_bytes=10),
]
W4_CLIPS = [
    SourceClip(id="w4a", name="IMG_003.MOV", learner_folder="Ada Whitfield", size_bytes=10),
    SourceClip(id="w4b", name="IMG_004.MOV", learner_folder="Ada Whitfield", size_bytes=10),
]


class ReusableSource:
    """A source whose trash does not necessarily take effect.

    The shared fake models a well-behaved source; the production incident was
    a source that kept listing clips after recording them moved. ``sticky``
    holds the ids a trash call must *not* remove — exactly the failure the
    Drive API presented on the night of 2026-08-26/27: the update succeeded,
    the count came back, the clips stayed.
    """

    driver = "fake"

    def __init__(self, clips, sticky: set[str] | None = None) -> None:
        self.clips = list(clips)
        self.sticky = set(sticky or ())
        self.downloaded: list[str] = []
        self.trash_calls: list[list[str]] = []
        self.fail_trash_with: Exception | None = None

    def list_pending(self):
        return list(self.clips)

    def download(self, clip, destination):
        from pathlib import Path

        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-video-bytes")
        self.downloaded.append(clip.id)
        return path

    def trash(self, clip_ids):
        if self.fail_trash_with is not None:
            raise self.fail_trash_with
        self.trash_calls.append(list(clip_ids))
        moved = 0
        for clip_id in clip_ids:
            if clip_id in self.sticky:
                continue  # the update "succeeds" and the clip stays listed
            self.clips = [clip for clip in self.clips if clip.id != clip_id]
            moved += 1
        return moved

    def health(self) -> None:
        return None


@pytest.fixture
def studio(tmp_path):
    """A one-learner studio whose sessions resolve like the CLI's own rule.

    A recording attaches to the session in progress — the same first choice
    `cmd_video` makes. A published (Done) session never takes a new recording,
    which is what makes a folder's next batch of clips *next week's*.
    """

    def build(source, *, weeks=(3,)):
        sessions = [
            Session(id=f"s{number}", learner_id="1", number=number, doc_id=f"doc-ada-0{number}")
            for number in weeks
        ]
        store = FakeLearnerStore(learners=[ADA], sessions=sessions)
        docs = FakeDocStore(
            statuses={
                session.doc_id: DocStatus(doc_id=session.doc_id, status="In progress")
                for session in sessions
            }
        )

        def resolve_session(learner):
            active = [
                session
                for session in store.list_sessions(learner.id)
                if docs.statuses.get(session.doc_id) is not None
                and docs.statuses[session.doc_id].status == "In progress"
            ]
            return (active[0].number, active[0].doc_id) if active else None

        return VideoPipeline(
            source=source,
            encoder=FakeEncoder(),
            publisher=FakePublisher(),
            store=store,
            docs=docs,
            jobs=VideoJobStore(tmp_path / "video"),
            workdir=tmp_path / "work",
            profile=EncodeProfile(name="auto"),
            resolve_session=resolve_session,
        )

    return build


def _open_week(pipeline, number):
    """Create week `number`'s session so a new job can resolve into it."""
    session = Session(id=f"s{number}", learner_id="1", number=number, doc_id=f"doc-ada-0{number}")
    pipeline.store.sessions.append(session)
    pipeline.docs.statuses[session.doc_id] = DocStatus(
        doc_id=session.doc_id, status="In progress"
    )


def _publish(pipeline, number):
    """Mark week `number` published, as `lesson publish` would have."""
    doc_id = f"doc-ada-0{number}"
    pipeline.docs.statuses[doc_id] = DocStatus(doc_id=doc_id, status="Done")


# -- task 1: a done job must not swallow a newer lesson's clips --------------


def test_new_clips_after_a_done_job_start_the_next_session(studio):
    """The 19:10 case: W4 clips sat in the folder while the W3 job, done,
    occupied the store — `run` reported nothing to do until a person ran
    `video forget`. The run itself must roll the job over."""
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    first = pipeline.run()[0]
    assert first.session_number == 3
    assert first.status == "done"

    # A week later: the summary went out, new clips arrive, week 4 opens.
    _publish(pipeline, 3)
    source.clips = list(W4_CLIPS)
    _open_week(pipeline, 4)

    second = pipeline.run()[0]

    assert second.status == "done"
    assert second.session_number == 4
    assert second.video_id != first.video_id, "the new lesson needs its own upload"
    assert second.clip_ids == ["w4a", "w4b"]
    # The W3 record survives, archived, with its upload still readable.
    archived = pipeline.jobs.root / "archive"
    assert len(list(archived.glob("*.json"))) == 1
    assert VideoJobStore(archived).list()[0].video_id == first.video_id
    # And the live store holds only the new job.
    assert pipeline.jobs.get("Ada Whitfield").session_number == 4


def test_a_done_job_stays_silent_when_no_new_clips_arrive(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()

    source.clips = []

    assert pipeline.run() == []
    assert pipeline.jobs.get("Ada Whitfield").session_number == 3


def test_mixed_clips_reclaim_the_old_week_and_run_the_new(studio):
    """Re-running over a done job with mixed clips must not re-download or
    re-upload the old week's material — only the fresh session's."""
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    first = pipeline.run()[0]
    source.downloaded.clear()

    # The old trash never took either; the published week makes the new
    # clips next week's, and week 4 opens.
    source.clips = [*W3_CLIPS, *W4_CLIPS]
    _publish(pipeline, 3)
    _open_week(pipeline, 4)

    jobs = pipeline.run()

    assert [job.session_number for job in jobs] == [3, 4]
    again = jobs[0]
    assert again.video_id == first.video_id, "no second copy of week 3"
    assert again.status == "done"
    assert source.downloaded == ["w4a", "w4b"], "only the new week is collected"
    # Both weeks' clips are reclaimed: the old ones by recovery, the new ones
    # by their own job finishing.
    assert source.clips == []


def test_rollover_without_a_next_session_fails_with_the_remedy(studio):
    """New clips with nowhere to attach must fail loudly, not archive the
    done job into limbo and report success."""
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()
    _publish(pipeline, 3)

    source.clips = list(W4_CLIPS)  # week 4's session is never created

    job = pipeline.run()[0]

    assert job.status == "failed"
    assert "No session to attach" in job.error
    # The done week-3 job was archived and the failed week-4 job replaced it;
    # creating the session and re-running resumes the same job.
    _open_week(pipeline, 4)
    retried = pipeline.run()[0]
    assert retried.status == "done"
    assert retried.session_number == 4


# -- task 2: recovery must trash what the source kept ------------------------


def test_a_done_job_reclaims_clips_the_source_still_lists(studio):
    """The 15:03 case: both jobs recorded `source_trashed moved=N`, the run
    reported 2 done, and eight clips stayed in Drive until a person trashed
    them through the API. The next run must re-issue the trash."""
    source = ReusableSource(W3_CLIPS, sticky={"w3a", "w3b"})
    pipeline = studio(source)
    pipeline.run()
    assert source.clips, "the sticky clips must still be listed"

    # Overnight the source starts honouring trash.
    source.sticky = set()
    job = pipeline.run()[0]

    assert job.status == "done"
    assert job.session_number == 3, "recovery, not a new session"
    assert source.clips == [], "the leftover clips must be reclaimed"
    assert source.trash_calls[-1] == ["w3a", "w3b"]
    assert job.steps["source_trashed"]["reclaimed"] == 2


def test_a_failed_reclaim_fails_the_job_loudly(studio):
    source = ReusableSource(W3_CLIPS, sticky={"w3a", "w3b"})
    pipeline = studio(source)
    pipeline.run()

    source.fail_trash_with = UpstreamError("drive refused", service="gdrive")
    job = pipeline.run()[0]

    assert job.status == "failed"
    assert "drive refused" in job.error


# -- task 3: the link step verifies the live page, not its own record --------


def test_a_page_rebuilt_without_the_video_block_gets_it_back(studio):
    """The 21:46 case: `doc_linked` recorded done, a later forced publish
    rewrote the page without the block, and the send gate refused. When the
    pipeline passes that way again the block must return."""
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    job = pipeline.run()[0]
    blocks = pipeline.docs.blocks["doc-ada-03"]
    assert any(block.type == "video" for block in blocks), "precondition: linked"

    # A page rebuild drops it; the steps after the link reopen the way a
    # recovery pass finds them.
    pipeline.docs.blocks["doc-ada-03"] = [block for block in blocks if block.type != "video"]
    job.steps.pop("doc_linked")
    job.steps.pop("cleaned")
    job.steps.pop("source_trashed")
    pipeline.jobs.save(job)

    again = pipeline.run_one("Ada Whitfield", [])

    assert again.status == "done"
    restored = [block for block in pipeline.docs.blocks["doc-ada-03"] if block.type == "video"]
    assert [block.url for block in restored] == [job.video_url]


# -- task 4: resume and run must agree on what needs doing --------------------


def test_resume_reclaims_a_done_job_the_run_would_see(studio):
    """The exact 15:03 sequence: resume answered "Nothing to process." and the
    run that followed reported two jobs done while doing nothing at all."""
    source = ReusableSource(W3_CLIPS, sticky={"w3a", "w3b"})
    pipeline = studio(source)
    pipeline.run()

    source.sticky = set()
    jobs = pipeline.resume()

    assert len(jobs) == 1
    assert jobs[0].status == "done"
    assert source.clips == [], "resume must finish what the source undid"


def test_resume_reports_waiting_clips_it_will_not_collect(studio):
    """Resume must not start the new week itself — but it must say the clips
    are there instead of an empty nothing, so the operator runs `video run`."""
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()

    source.clips = list(W4_CLIPS)

    assert pipeline.resume() == []
    assert pipeline.waiting_clips() == 2


def test_waiting_clips_is_zero_when_nothing_is_left(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()

    assert pipeline.waiting_clips() == 0


def test_waiting_clips_does_not_count_a_done_jobs_own_clips(studio):
    """Clips a done job already owns are recovery, which resume has just
    handled — counting them would send the operator to `run` for nothing."""
    source = ReusableSource(W3_CLIPS, sticky={"w3a"})
    pipeline = studio(source)
    pipeline.run()

    assert pipeline.waiting_clips() == 0


# -- task 5: `video status --json` keeps one shape ---------------------------


def test_status_json_error_is_null_not_empty_string(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    job = pipeline.run()[0]

    payload = job.to_dict()

    assert payload["error"] is None
    assert payload["learner_name"] == "Ada Whitfield"


def test_status_json_survives_a_round_trip_through_null_error(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()

    reloaded = VideoJobStore(pipeline.jobs.root).get("Ada Whitfield")

    assert reloaded is not None
    assert reloaded.error == ""
    assert reloaded.to_dict()["error"] is None


def test_status_json_error_still_carries_the_message_when_set(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.publisher.fail_with = UpstreamError("youtube is down", service="youtube")

    job = pipeline.run()[0]

    assert job.status == "failed"
    assert job.to_dict()["error"] == "youtube is down"


def test_every_job_payload_carries_the_same_fields(studio):
    source = ReusableSource(W3_CLIPS)
    pipeline = studio(source)
    pipeline.run()
    done = pipeline.jobs.get("Ada Whitfield").to_dict()

    source.clips = list(W4_CLIPS)
    pipeline.publisher.fail_with = UpstreamError("youtube is down", service="youtube")
    _open_week(pipeline, 4)
    failed = pipeline.run()[0].to_dict()

    for payload in (done, failed):
        for field_name in (
            "learner_folder",
            "learner_id",
            "learner_name",
            "session_number",
            "doc_id",
            "clip_ids",
            "video_id",
            "video_url",
            "steps",
            "status",
            "error",
            "updated_at",
        ):
            assert field_name in payload, f"{field_name} must exist on every job"
        assert isinstance(payload["error"], str) or payload["error"] is None
