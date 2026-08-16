"""The video pipeline's three safety properties, and resume at every step.

Each property here corresponds to a way a recording gets lost or duplicated:
trashing the only copy before the upload lands, uploading twice after a crash,
and one bad clip taking the whole night's run down with it.
"""

from __future__ import annotations

import pytest

from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import (
    FakeDocStore,
    FakeEncoder,
    FakeLearnerStore,
    FakeMediaSource,
    FakePublisher,
)
from baton.adapters.media.base import EncodeProfile, SourceClip
from baton.domain.models import Learner, Session
from baton.errors import UpstreamError
from baton.pipelines.video import STEPS, VideoJobStore, VideoPipeline

ADA = Learner(id="1", name="Ada Whitfield", instrument="guitar")
BRUNO = Learner(id="2", name="Bruno Castell", instrument="drums")

CLIPS = [
    SourceClip(id="c1", name="IMG_001.MOV", learner_folder="Ada Whitfield", size_bytes=10),
    SourceClip(id="c2", name="IMG_002.MOV", learner_folder="Ada Whitfield", size_bytes=10),
]


@pytest.fixture
def pipeline(tmp_path):
    """A pipeline over fakes, with Ada's session 3 in progress."""
    source = FakeMediaSource(clips=list(CLIPS))
    encoder = FakeEncoder()
    publisher = FakePublisher()
    store = FakeLearnerStore(
        learners=[ADA, BRUNO],
        sessions=[
            Session(id="s1", learner_id="1", number=3, doc_id="doc-ada-03"),
            Session(id="s2", learner_id="2", number=1, doc_id="doc-bruno-01"),
        ],
    )
    docs = FakeDocStore(
        statuses={
            "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="In progress"),
            "doc-bruno-01": DocStatus(doc_id="doc-bruno-01", status="In progress"),
        }
    )
    jobs = VideoJobStore(tmp_path / "jobs")

    def resolve_session(learner):
        found = store.list_sessions(learner.id)
        return (found[0].number, found[0].doc_id) if found else None

    built = VideoPipeline(
        source=source,
        encoder=encoder,
        publisher=publisher,
        store=store,
        docs=docs,
        jobs=jobs,
        workdir=tmp_path / "work",
        profile=EncodeProfile(name="auto"),
        resolve_session=resolve_session,
    )
    return built, source, encoder, publisher, docs, jobs


# -- the happy path ----------------------------------------------------------


def test_a_full_run_completes_every_step(pipeline):
    built, source, _encoder, publisher, docs, _jobs = pipeline

    jobs = built.run()

    assert [job.status for job in jobs] == ["done"]
    job = jobs[0]
    assert all(job.done(step) for step in STEPS)
    assert len(publisher.uploads) == 1
    assert job.video_url.startswith("https://youtu.be/")
    assert source.trashed == ["c1", "c2"]
    assert any(block.type == "video" for block in docs.list_blocks("doc-ada-03"))


def test_the_upload_is_titled_with_the_learner_and_session(pipeline):
    built, _s, _e, publisher, _d, _j = pipeline

    built.run()

    assert publisher.uploads[0]["title"] == "Ada Whitfield — week 3"


# -- property 1: nothing is deleted until everything else succeeded ----------


def test_the_source_survives_a_failed_upload(pipeline):
    """The clips are the only copy at this point. Losing them here would be
    unrecoverable — the whole reason trashing is the last step."""
    built, source, _e, publisher, _d, _j = pipeline
    publisher.fail_with = UpstreamError("youtube is down", service="youtube")

    jobs = built.run()

    assert jobs[0].status == "failed"
    assert source.trashed == []


def test_the_source_survives_a_failed_document_link(pipeline):
    built, source, _e, _p, docs, _j = pipeline
    docs.fail_with = UpstreamError("notion is down", service="notion")

    jobs = built.run()

    assert jobs[0].status == "failed"
    assert source.trashed == []


def test_trashing_happens_only_after_the_link_is_recorded(pipeline):
    built, source, _e, _p, _d, _j = pipeline

    job = built.run()[0]

    assert job.done("doc_linked")
    assert job.done("source_trashed")
    assert source.trashed


# -- property 2: a completed upload is never repeated -----------------------


def test_a_resumed_run_does_not_upload_again(pipeline):
    """Two copies of a child's lesson online, with no way to tell which link
    was sent, is the failure this prevents."""
    built, _s, _e, publisher, docs, _j = pipeline
    docs.fail_with = UpstreamError("notion is down", service="notion")
    built.run()
    assert len(publisher.uploads) == 1

    docs.fail_with = None
    jobs = built.resume()

    assert jobs[0].status == "done"
    assert len(publisher.uploads) == 1  # not two


def test_re_running_a_finished_job_uploads_nothing(pipeline):
    built, _s, _e, publisher, _d, _j = pipeline
    built.run()

    built.run_one("Ada Whitfield", list(CLIPS))

    assert len(publisher.uploads) == 1


def test_the_video_id_is_recorded_before_anything_else_can_fail(pipeline):
    built = pipeline[0]
    docs = pipeline[4]
    jobs = pipeline[5]
    docs.fail_with = UpstreamError("notion is down", service="notion")

    built.run()

    saved = jobs.get("Ada Whitfield")
    assert saved.video_id == "vid1"
    assert saved.done("uploaded")


def test_linking_is_not_repeated_when_the_video_is_already_on_the_page(pipeline):
    """Guards the case where the link landed but the step record did not."""
    built, _s, _e, _p, docs, jobs = pipeline
    built.run()
    before = len(docs.list_blocks("doc-ada-03"))

    job = jobs.get("Ada Whitfield")
    job.steps.pop("doc_linked")
    job.steps.pop("source_trashed")
    jobs.save(job)
    built.run_one("Ada Whitfield", [])

    assert len(docs.list_blocks("doc-ada-03")) == before


# -- property 3: one learner's failure does not stop the others -------------


def test_a_failure_for_one_learner_leaves_the_others_alone(tmp_path, pipeline):
    built, source, _e, publisher, _d, _j = pipeline
    source.clips = [
        *CLIPS,
        SourceClip(id="c3", name="clip.mov", learner_folder="Bruno Castell", size_bytes=10),
    ]

    # Ada's session cannot be resolved; Bruno's can.
    def resolve_session(learner):
        if learner.name == "Ada Whitfield":
            return None
        return (1, "doc-bruno-01")

    built.resolve_session = resolve_session

    jobs = {job.learner_folder: job for job in built.run()}

    assert jobs["Ada Whitfield"].status == "failed"
    assert jobs["Bruno Castell"].status == "done"
    assert len(publisher.uploads) == 1


def test_an_unmatched_folder_is_skipped_rather_than_guessed(pipeline):
    """A folder called "Ada" does not resolve to "Ada Whitfield". Uploading
    one child's lesson onto another child's page is not worth the convenience."""
    built, source, _e, publisher, _d, _j = pipeline
    source.clips = [SourceClip(id="x", name="c.mov", learner_folder="Ada", size_bytes=10)]

    jobs = built.run()

    assert jobs[0].status == "skipped"
    assert "exactly" in jobs[0].error
    assert publisher.uploads == []


# -- resume at each step -----------------------------------------------------


@pytest.mark.parametrize("failing_step", ["combined", "session_resolved", "uploaded", "doc_linked"])
def test_a_run_resumes_from_wherever_it_broke(pipeline, failing_step):
    built, source, encoder, publisher, docs, _jobs = pipeline

    failures = {
        "combined": lambda: setattr(encoder, "fail_with", UpstreamError("ffmpeg", service="f")),
        "session_resolved": lambda: setattr(built, "resolve_session", lambda _l: None),
        "uploaded": lambda: setattr(publisher, "fail_with", UpstreamError("yt", service="y")),
        "doc_linked": lambda: setattr(docs, "fail_with", UpstreamError("notion", service="n")),
    }
    repairs = {
        "combined": lambda: setattr(encoder, "fail_with", None),
        "session_resolved": lambda: setattr(built, "resolve_session", lambda _l: (3, "doc-ada-03")),
        "uploaded": lambda: setattr(publisher, "fail_with", None),
        "doc_linked": lambda: setattr(docs, "fail_with", None),
    }

    failures[failing_step]()
    first = built.run()[0]
    assert first.status == "failed"
    assert not first.done(failing_step)
    assert source.trashed == []

    repairs[failing_step]()
    second = built.resume()[0]

    assert second.status == "done"
    assert all(second.done(step) for step in STEPS)
    assert len(publisher.uploads) == 1


def test_resume_processes_nothing_when_every_job_finished(pipeline):
    built = pipeline[0]
    built.run()

    assert built.resume() == []


def test_a_skipped_job_is_not_resumed(pipeline):
    built, source, _e, _p, _d, _j = pipeline
    source.clips = [SourceClip(id="x", name="c.mov", learner_folder="Nobody", size_bytes=10)]
    built.run()

    assert built.resume() == []


# -- selection and state -----------------------------------------------------


def test_only_restricts_the_run_to_named_folders(pipeline):
    built, source, _e, publisher, _d, _j = pipeline
    source.clips = [
        *CLIPS,
        SourceClip(id="c3", name="c.mov", learner_folder="Bruno Castell", size_bytes=10),
    ]

    jobs = built.run(only=["Bruno Castell"])

    assert [job.learner_folder for job in jobs] == ["Bruno Castell"]
    assert len(publisher.uploads) == 1


def test_job_state_survives_a_reload(pipeline, tmp_path):
    built = pipeline[0]
    built.run()

    reloaded = VideoJobStore(tmp_path / "jobs").get("Ada Whitfield")

    assert reloaded.status == "done"
    assert reloaded.video_id == "vid1"
    assert all(reloaded.done(step) for step in STEPS)


def test_work_files_are_cleaned_up_after_success(pipeline, tmp_path):
    built = pipeline[0]

    built.run()

    assert not (tmp_path / "work" / "Ada_Whitfield").exists()


def test_work_files_are_kept_after_a_failure_so_a_resume_can_use_them(pipeline, tmp_path):
    built, _s, _e, publisher, _d, _j = pipeline
    publisher.fail_with = UpstreamError("yt", service="y")

    built.run()

    assert (tmp_path / "work" / "Ada_Whitfield" / "combined.mp4").exists()
