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
    built, source = pipeline[:2]
    built.run()

    # A real source stops listing clips once they are trashed; the shared
    # fake does not, so empty it the way the source would be.
    source.clips = []

    assert built.resume() == []


def test_a_skipped_job_is_not_resumed(pipeline):
    built, source, _e, _p, _d, _j = pipeline
    source.clips = [SourceClip(id="x", name="c.mov", learner_folder="Nobody", size_bytes=10)]
    built.run()

    assert built.resume() == []


# -- selection and state -----------------------------------------------------


def test_non_ascii_learner_folders_do_not_share_a_job_record(tmp_path):
    """A regression for the incident that shipped: `_slug` kept only ASCII, so
    two folders named entirely in Thai both stripped to "" and fell back to
    the same literal "unknown" — the second learner's run read the first's
    completed job back, saw every step already done, and silently skipped
    downloading, uploading, and linking her own clips."""
    ikkyu = Learner(id="10", name="น้องอิคคิว", instrument="guitar")
    khing = Learner(id="11", name="น้องขิงขิง", instrument="guitar")
    clips = [
        SourceClip(id="c1", name="clip.mov", learner_folder="น้องขิงขิง", size_bytes=10),
        SourceClip(id="c2", name="clip.mov", learner_folder="น้องอิคคิว", size_bytes=10),
    ]
    store = FakeLearnerStore(
        learners=[ikkyu, khing],
        sessions=[
            Session(id="s1", learner_id="10", number=1, doc_id="doc-ikkyu-01"),
            Session(id="s2", learner_id="11", number=1, doc_id="doc-khing-01"),
        ],
    )
    docs = FakeDocStore(
        statuses={
            "doc-ikkyu-01": DocStatus(doc_id="doc-ikkyu-01", status="In progress"),
            "doc-khing-01": DocStatus(doc_id="doc-khing-01", status="In progress"),
        }
    )
    publisher = FakePublisher()

    def resolve_session(learner):
        found = store.list_sessions(learner.id)
        return (found[0].number, found[0].doc_id) if found else None

    built = VideoPipeline(
        source=FakeMediaSource(clips=clips),
        encoder=FakeEncoder(),
        publisher=publisher,
        store=store,
        docs=docs,
        jobs=VideoJobStore(tmp_path / "jobs"),
        workdir=tmp_path / "work",
        profile=EncodeProfile(name="auto"),
        resolve_session=resolve_session,
    )

    jobs = {job.learner_folder: job for job in built.run()}

    assert jobs["น้องขิงขิง"].status == "done"
    assert jobs["น้องอิคคิว"].status == "done"
    assert jobs["น้องขิงขิง"].video_id != jobs["น้องอิคคิว"].video_id
    assert len(publisher.uploads) == 2
    # Each learner's job file lives at its own path, not a shared "unknown.json".
    assert len(list((tmp_path / "jobs").glob("*.json"))) == 2


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


# -- resume honesty ----------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402  (used by the tests below)


def test_resuming_a_job_that_never_downloaded_is_an_error_not_a_poisoned_record(pipeline, tmp_path):
    """A crash before the download finished used to resume with clips=[], mark
    `downloaded` done over zero files, and leave every later run skipping the
    download forever. The failure must be loud and the step record untouched."""
    built, _source, _encoder, _publisher, _docs, jobs = pipeline
    from baton.pipelines.video import VideoJob

    jobs.save(VideoJob(learner_folder="Ada Whitfield", status="in_progress"))

    job = built.resume()[0]

    assert job.status == "failed"
    assert "none were downloaded" in job.error
    assert not jobs.get("Ada Whitfield").done("downloaded")


def test_two_clips_with_the_same_filename_do_not_clobber_each_other(pipeline):
    """A phone's own numbering restarts across recording sessions, so two
    distinct clips sharing one filename (e.g. two `IMG_8131.MOV`) is normal.
    Downloading both to the same destination used to let the second silently
    overwrite the first before either was combined — the run then reported
    success and trashed both originals, having actually used only one twice."""
    from baton.pipelines.video import VideoJob

    built, source, _encoder, _publisher, _docs, _jobs = pipeline
    same_name_clips = [
        SourceClip(id="dup-1", name="IMG_8131.MOV", learner_folder="Ada Whitfield", size_bytes=16),
        SourceClip(id="dup-2", name="IMG_8131.MOV", learner_folder="Ada Whitfield", size_bytes=16),
    ]

    paths = built._download(VideoJob(learner_folder="Ada Whitfield"), same_name_clips)

    assert source.downloaded == ["dup-1", "dup-2"], "both clips must actually be fetched"
    assert len({p.name for p in paths}) == 2, f"expected two distinct destinations, got {paths}"
    on_disk = sorted(p.name for p in built._clip_dir("Ada Whitfield").glob("*.MOV"))
    assert len(on_disk) == 2, f"expected two distinct files on disk, got {on_disk}"


def test_a_stale_download_step_with_no_files_names_the_job_file(pipeline, tmp_path):
    """`downloaded` recorded but the directory empty: the job file is the lie,
    and the error must say so rather than fail in the encoder."""
    built, _source, _encoder, _publisher, _docs, jobs = pipeline
    from baton.pipelines.video import VideoJob

    job = VideoJob(learner_folder="Ada Whitfield", status="in_progress")
    job.record("downloaded", count=0)
    jobs.save(job)

    result = built.resume()[0]

    assert result.status == "failed"
    assert "working directory holds no clips" in result.error


def test_a_partial_file_from_a_killed_run_is_downloaded_again(pipeline, tmp_path):
    """Existing-but-wrong files were treated as complete downloads. A one-byte
    leftover used to become the lesson video and then trash the only other
    copy of the recording."""
    built, source, _encoder, _publisher, _docs, _jobs = pipeline
    clip_dir = built._clip_dir("Ada Whitfield")
    clip_dir.mkdir(parents=True)
    (clip_dir / "IMG_001.MOV").write_bytes(b"x")  # 1 byte, clip says 10

    built.run_one("Ada Whitfield", list(CLIPS))

    assert source.downloaded, "the partial file must not satisfy the download"


def test_a_complete_file_from_a_earlier_run_is_not_downloaded_again(pipeline):
    built, source, _encoder, _publisher, _docs, _jobs = pipeline
    clip_dir = built._clip_dir("Ada Whitfield")
    clip_dir.mkdir(parents=True)
    (clip_dir / "IMG_001.MOV").write_bytes(b"0123456789")  # exactly 10 bytes

    built.run_one("Ada Whitfield", [CLIPS[0]])

    assert not source.downloaded


def test_workdir_strays_never_become_concat_inputs(pipeline):
    """ffmpeg's interrupted temp file is dot-prefixed and lives in the same
    directory; a bare glob used to hand it to the concat filter."""
    built, _source, encoder, _publisher, _docs, jobs = pipeline
    from baton.pipelines.video import VideoJob

    job = VideoJob(learner_folder="Ada Whitfield", status="in_progress")
    job.record("downloaded", count=2)
    jobs.save(job)
    clip_dir = built._clip_dir("Ada Whitfield")
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip1.mp4").write_bytes(b"a")
    (clip_dir / "clip2.mp4").write_bytes(b"b")
    (clip_dir / ".baton-encode-tmp123.mp4").write_bytes(b"junk")
    (clip_dir / "notes.txt").write_bytes(b"not a video")

    built.resume()

    inputs = encoder.calls[0][0] if encoder.calls else []
    assert [_Path(name).name for name in inputs] == ["clip1.mp4", "clip2.mp4"]


def test_clips_concatenate_in_human_order_not_text_order(pipeline):
    built, _source, encoder, _publisher, _docs, jobs = pipeline
    from baton.pipelines.video import VideoJob

    clip_dir = built._clip_dir("Ada Whitfield")
    clip_dir.mkdir(parents=True)
    for name in ("clip1.mp4", "clip10.mp4", "clip9.mp4", "clip2.mp4"):
        (clip_dir / name).write_bytes(b"x")
    job = VideoJob(learner_folder="Ada Whitfield", status="in_progress")
    job.record("downloaded", count=4)
    jobs.save(job)

    built.resume()

    inputs = [_Path(name).name for name in encoder.calls[0][0]]
    assert inputs == ["clip1.mp4", "clip2.mp4", "clip9.mp4", "clip10.mp4"]


def test_the_fake_encoder_refuses_an_empty_input_list_like_the_real_one(tmp_path):
    from baton.errors import ConfigError

    with pytest.raises(ConfigError):
        FakeEncoder().combine([], tmp_path / "nowhere.mp4", EncodeProfile(name="auto"))


def test_gdrive_list_pending_skips_non_video_files():
    """The local source always filtered by extension; Drive did not, so a
    photo or note in a learner's folder became a clip."""
    from baton.adapters.media.google import DriveSource

    class FakeDrive(DriveSource):
        def __init__(self):  # no super(): no credentials needed for this test
            self.folder_id = "root"
            self._listing = {
                "root": [
                    {"id": "f1", "name": "Ada Whitfield"},
                ],
                "f1": [
                    {"id": "a", "name": "lesson.mp4", "size": "10"},
                    {"id": "b", "name": "poster.jpg", "size": "200"},
                    {"id": "c", "name": "tuning notes.txt", "size": "30"},
                ],
            }

        def _children(self, parent_id, *, folders):
            return [
                item
                for item in self._listing[parent_id]
                if (item["name"].startswith("Ada") == folders)
            ]

    clips = FakeDrive().list_pending()

    assert [clip.name for clip in clips] == ["lesson.mp4"]


def test_drive_preserves_authorized_user_file_scopes(profile, monkeypatch, tmp_path):
    from baton.adapters.media import google
    from baton.core import config as config_module

    credentials_file = tmp_path / "drive.json"
    credentials_file.write_text("{}", encoding="utf-8")
    with (profile / "baton.yaml").open("a", encoding="utf-8") as handle:
        handle.write(f"\nmedia:\n  drive:\n    credentials_file: {credentials_file}\n")

    seen = {}

    class Credentials:
        @classmethod
        def from_authorized_user_file(cls, path):
            seen.update(path=path)
            return cls()

    monkeypatch.setattr(google, "_require_google", lambda: (None, None, Credentials))

    result = google._credentials(config_module.load(profile), "media.drive")

    assert isinstance(result, Credentials)
    assert seen == {"path": str(credentials_file)}


def test_drive_can_override_the_shared_refresh_token(profile, monkeypatch):
    from baton.adapters.media import google
    from baton.core import config as config_module

    monkeypatch.setenv("GOOGLE_DRIVE_REFRESH_TOKEN", "drive-token")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "youtube-token")
    with (profile / "baton.yaml").open("a", encoding="utf-8") as handle:
        handle.write("\nmedia:\n  drive:\n    refresh_token_env: GOOGLE_DRIVE_REFRESH_TOKEN\n")

    class Credentials:
        def __init__(self, **kwargs):
            self.refresh_token = kwargs["refresh_token"]
            self.scopes_were_overridden = "scopes" in kwargs

    monkeypatch.setattr(google, "_require_google", lambda: (None, None, Credentials))
    config = config_module.load(profile)

    drive = google._credentials(config, "media.drive")
    youtube = google._credentials(config, "media.youtube")

    assert drive.refresh_token == "drive-token"
    assert youtube.refresh_token == "youtube-token"
    assert not drive.scopes_were_overridden
    assert not youtube.scopes_were_overridden


def test_google_vendor_errors_stay_inside_the_baton_contract():
    from baton.adapters.media.google import _google_call

    def fail():
        raise RuntimeError("vendor traceback")

    with pytest.raises(UpstreamError) as excinfo:
        _google_call("gdrive", fail)

    assert excinfo.value.details["service"] == "gdrive"
    assert "vendor traceback" not in excinfo.value.message


# -- where the recording lands on the page -----------------------------------


def _summary_already_on_the_page(docs):
    """What `lesson publish` leaves behind: the summary appended to the page."""
    docs.append_blocks(
        "doc-ada-03",
        [
            {"object": "block", "type": "heading_2", "heading_2": {}},
            {"object": "block", "type": "paragraph", "paragraph": {}},
        ],
    )


def test_the_recording_goes_above_a_summary_that_was_published_first(pipeline):
    """Encoding takes minutes and the teacher writes meanwhile, so the video
    arriving second is the ordinary case. Appending put it under the summary
    there and over it when the order went the other way: the same lesson
    filed two ways, with nothing on the page to explain why."""
    built, _s, _e, _p, docs, _jobs = pipeline
    _summary_already_on_the_page(docs)

    built.run()

    types = [block.type for block in docs.list_blocks("doc-ada-03")]
    assert types[0] == "video"
    assert types[1:] == ["heading_2", "paragraph"]


def test_the_recording_is_above_the_summary_when_it_arrives_first_too(pipeline):
    """The point is that the page reads the same either way round, so the
    order the day happened in stops being visible to the family."""
    built, _s, _e, _p, docs, _jobs = pipeline

    built.run()
    _summary_already_on_the_page(docs)

    types = [block.type for block in docs.list_blocks("doc-ada-03")]
    assert types[0] == "video"
    assert types[1:] == ["heading_2", "paragraph"]
