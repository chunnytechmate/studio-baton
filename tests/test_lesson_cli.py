"""The full loop: stage → contract → ingest → render → publish.

Runs the real SQLite driver and a scripted document store, so the only thing
substituted is the network.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

SUMMARY = {
    "overview": ["Held the tempo through the whole B section."],
    "covered": [{"topic": "Blackbird bars 9-16", "detail": "Thumb-and-finger pattern"}],
    "focus": [{"issue": "Late change to C", "fix": "Four beats each, alone"}],
    "goals": ["Bars 9-16 at 80bpm with the backing track"],
    "short_summary": {
        "covered": "Blackbird bars 9 to 16",
        "progress": "Tempo held without stopping",
        "homework": "Bars 9-16 at 80bpm",
    },
}


@pytest.fixture
def studio(profile, monkeypatch):
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
            labels:
              learner: student
              session: lesson
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            docs:
              driver: notion
              statuses:
                done: Complete
                in_progress: In progress
                not_started: Not started
              preserve:
                - type: video
                - type: embed
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (profile / "theory.json").write_text(
        json.dumps({"vibrato": "Pick first, then oscillate from the wrist."}),
        encoding="utf-8",
    )

    fake = FakeDocStore(
        statuses={
            "doc-ada-01": DocStatus(doc_id="doc-ada-01", status="Complete", date="2026-05-01"),
            "doc-ada-02": DocStatus(doc_id="doc-ada-02", status="Complete", date="2026-06-01"),
            "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="Not started"),
        },
        blocks={
            "doc-ada-03": [
                Block(id="vid", type="video", url="https://example.invalid/watch/ada-3"),
            ]
        },
        wording={"done": "Complete", "in_progress": "In progress"},
    )
    for module in ("cmd_learner", "cmd_lesson"):
        monkeypatch.setattr(f"baton.cli.{module}.open_docs", lambda _config: fake)
    return profile, fake


def call(studio, *args):
    profile, _ = studio
    return run(["--profile", str(profile), "--json", "lesson", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def stage_ada(studio, capsys):
    assert call(studio, "stage", "Ada Whitfield", "--session", "3", "--context", "notes") == Exit.OK
    return out(capsys)


# -- stage -------------------------------------------------------------------


def test_stage_accepts_an_explicit_session(studio, capsys):
    assert call(studio, "stage", "Ada Whitfield", "--session", "3") == Exit.OK

    assert out(capsys)["session_number"] == 3


def test_staging_an_explicit_session_needs_no_document_reads(studio, capsys):
    """The teacher writes the notes straight after the lesson, often on a
    phone. When the session is named, both the number and the document id come
    from the database — so a document-store outage must not block staging."""
    from baton.errors import UpstreamError

    _, docs = studio
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert call(studio, "stage", "Ada Whitfield", "--session", "3", "--context", "n") == Exit.OK
    assert out(capsys)["session_number"] == 3


def test_choosing_the_session_automatically_does_read_the_documents(studio, capsys):
    """ "Free" is a fact about the page, not the database row, so this path
    genuinely needs the document store and must fail when it is unreachable."""
    from baton.errors import UpstreamError

    _, docs = studio
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert call(studio, "stage", "Ada Whitfield") == Exit.UPSTREAM


def test_stage_refuses_a_session_that_does_not_exist(studio, capsys):
    assert call(studio, "stage", "Ada Whitfield", "--session", "99") == Exit.USAGE

    assert "99" in out(capsys)["message"]


def test_stage_reads_context_from_a_file(studio, capsys, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("worked on bars 9-16", encoding="utf-8")

    call(studio, "stage", "Ada Whitfield", "--session", "3", "--context-file", str(notes))
    capsys.readouterr()

    call(studio, "show", "Ada Whitfield")
    assert out(capsys)["context"] == "worked on bars 9-16"


# -- contract ----------------------------------------------------------------


def test_contract_gives_the_model_everything_it_needs(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "contract", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["schema"]["title"] == "Lesson summary"
    assert payload["context"]["session_number"] == 3
    assert payload["context"]["lesson_notes"] == "notes"
    assert payload["context"]["current_piece"]["title"] == "Blackbird"
    assert payload["constraints"]["available_callout_ids"] == ["vibrato"]


def test_contract_needs_a_staged_lesson_first(studio, capsys):
    assert call(studio, "contract", "Bruno Castell") == Exit.USAGE

    assert "stage" in out(capsys)["remedy"]


# -- ingest ------------------------------------------------------------------


def test_ingest_accepts_a_valid_summary(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY)) == Exit.OK
    assert out(capsys)["has_summary"] is True


def test_ingest_rejects_a_summary_with_emoji_in_the_message(studio, capsys):
    stage_ada(studio, capsys)
    bad = json.loads(json.dumps(SUMMARY))
    bad["short_summary"]["progress"] = "Brilliant 🎉"

    assert call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(bad)) == Exit.CONTRACT

    payload = out(capsys)
    assert payload["error"] == "contract"
    assert any("emoji" in v["reason"] for v in payload["details"]["violations"])


def test_a_rejected_summary_is_not_stored(studio, capsys):
    """Nothing partial. A rejected attempt must leave no trace, or the next
    command would publish half-validated content."""
    stage_ada(studio, capsys)
    bad = {"overview": ["only this"]}

    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(bad))
    capsys.readouterr()

    call(studio, "show", "Ada Whitfield")
    assert out(capsys)["summary"] is None


def test_ingest_rejects_an_unknown_callout_id(studio, capsys):
    stage_ada(studio, capsys)
    bad = {**SUMMARY, "callouts": ["invented-technique"]}

    assert call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(bad)) == Exit.CONTRACT
    assert any("invented-technique" in v["reason"] for v in out(capsys)["details"]["violations"])


def test_malformed_json_is_a_contract_failure_not_a_usage_error(studio, capsys):
    """The model produced it, so the model is what has to fix it — which means
    exit 4, the code that tells an agent to try again."""
    stage_ada(studio, capsys)

    assert call(studio, "ingest", "Ada Whitfield", "--json-text", "{not json") == Exit.CONTRACT
    assert "JSON" in out(capsys)["message"]


def test_ingest_reads_a_file(studio, capsys, tmp_path):
    stage_ada(studio, capsys)
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(SUMMARY), encoding="utf-8")

    assert call(studio, "ingest", "Ada Whitfield", "--file", str(path)) == Exit.OK


# -- render ------------------------------------------------------------------


def test_render_produces_markdown(studio, capsys):
    stage_ada(studio, capsys)
    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY))
    capsys.readouterr()

    assert call(studio, "render", "Ada Whitfield") == Exit.OK
    assert "Blackbird bars 9-16" in out(capsys)["markdown"]


def test_render_produces_the_parent_message(studio, capsys):
    stage_ada(studio, capsys)
    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY))
    capsys.readouterr()

    call(studio, "render", "Ada Whitfield", "--format", "message")
    assert out(capsys)["message"].startswith("• Covered: Blackbird bars 9 to 16")


def test_render_needs_an_accepted_summary(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "render", "Ada Whitfield") == Exit.USAGE


# -- publish -----------------------------------------------------------------


def prepared(studio, capsys):
    stage_ada(studio, capsys)
    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY))
    capsys.readouterr()


def test_publish_writes_the_summary_and_keeps_the_recording(studio, capsys):
    prepared(studio, capsys)
    _, docs = studio

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["appended"] > 0
    assert payload["preserved"] == 1
    assert "vid" in {block.id for block in docs.list_blocks("doc-ada-03")}


def test_publish_updates_the_youtube_description_when_a_video_is_linked(
    studio, capsys, monkeypatch
):
    """The just-published summary lands on the video's description too, the
    way the studio's previous pipeline did — but gated through Baton's own
    ownership check rather than trusting whatever link sits on the page."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    docs.blocks["doc-ada-03"] = [
        Block(id="vid", type="video", url="https://youtu.be/dQw4w9WgXcQ"),
    ]
    fake_publisher = FakePublisher()
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)
    prepared(studio, capsys)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["youtube"] == {"status": "ok", "video_id": "dQw4w9WgXcQ"}
    description = fake_publisher.descriptions["dQw4w9WgXcQ"]
    assert "Blackbird bars 9-16" in description
    assert "Ada Whitfield" in description


def test_publish_skips_the_youtube_step_when_youtube_is_not_configured(studio, capsys):
    """The fixture's `baton.yaml` has no `media:` section — the ordinary shape
    of a studio that only uses Baton for lesson summaries. This must not be
    an error; `open_publisher` raising `ConfigError` is the expected signal
    that there is nothing to update."""
    _, docs = studio
    docs.blocks["doc-ada-03"] = [
        Block(id="vid", type="video", url="https://youtu.be/dQw4w9WgXcQ"),
    ]
    prepared(studio, capsys)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    assert out(capsys)["youtube"] is None


def test_publish_skips_the_youtube_step_when_there_is_no_video_yet(studio, capsys, monkeypatch):
    """`doc-ada-03`'s only block in the base fixture is a non-YouTube link —
    the ordinary state before a recording has been attached."""
    from baton.adapters.fakes import FakePublisher

    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: FakePublisher())
    prepared(studio, capsys)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    assert out(capsys)["youtube"] is None


def test_publish_reports_but_does_not_fail_on_a_foreign_video(studio, capsys, monkeypatch):
    """A reference link on the document — a tutorial on someone else's
    channel — must not be overwritten, but the summary itself already landed
    on the page, so the command still succeeds overall."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    docs.blocks["doc-ada-03"] = [
        Block(id="vid", type="video", url="https://youtu.be/dQw4w9WgXcQ"),
    ]
    fake_publisher = FakePublisher()
    fake_publisher.foreign_video_ids.add("dQw4w9WgXcQ")
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)
    prepared(studio, capsys)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["appended"] > 0  # the summary itself still landed
    assert payload["youtube"]["status"] == "error"
    assert "dQw4w9WgXcQ" not in fake_publisher.descriptions


def test_publish_dry_run_changes_nothing(studio, capsys):
    prepared(studio, capsys)
    _, docs = studio
    before = list(docs.list_blocks("doc-ada-03"))

    assert call(studio, "publish", "Ada Whitfield", "--dry-run") == Exit.OK

    assert out(capsys)["dry_run"] is True
    assert docs.list_blocks("doc-ada-03") == before


def test_publishing_twice_is_refused_by_default(studio, capsys):
    """A second append leaves two copies of the summary on the page with
    nothing to tell them apart."""
    prepared(studio, capsys)
    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()
    _, docs = studio
    after_first = len(docs.list_blocks("doc-ada-03"))

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    assert out(capsys)["skipped"] == "already published"
    assert len(docs.list_blocks("doc-ada-03")) == after_first


def test_force_publishes_again(studio, capsys):
    prepared(studio, capsys)
    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield", "--force") == Exit.OK
    assert out(capsys)["appended"] > 0


def test_a_failed_publish_is_recorded_and_can_be_retried(studio, capsys):
    """The draft must survive the failure carrying the reason, so a re-run
    resumes rather than starting from nothing."""
    from baton.errors import UpstreamError

    prepared(studio, capsys)
    _, docs = studio
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert call(studio, "publish", "Ada Whitfield") == Exit.UPSTREAM
    capsys.readouterr()

    docs.fail_with = None
    call(studio, "show", "Ada Whitfield")
    payload = out(capsys)
    assert payload["targets"]["docs"]["status"] == "failed"
    assert payload["summary"] is not None  # the work was not lost

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK


def test_publish_stores_the_message_for_the_send_step(studio, capsys):
    """Composed once, at publish time, so what is sent later is exactly what
    was reviewed here."""
    profile, _ = studio
    prepared(studio, capsys)
    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()

    records = list((profile / "state" / "published").glob("*.json"))
    assert records
    saved = json.loads(records[0].read_text(encoding="utf-8"))
    assert saved["short_message"].startswith("• Covered:")
    assert saved["session_number"] == 3


def test_publish_marks_the_session_done(studio, capsys):
    """A summary on the page is not the same as a session that is over.

    Everything downstream reads the status, not the blocks: `next` writes to a
    fresh in-progress session, `prep` only briefs finished ones. Leaving the
    status alone after publishing kept pointing the next summary at the session
    that had just been written.
    """
    prepared(studio, capsys)
    _, docs = studio

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    assert docs.get_status("doc-ada-03").status == "Complete"


def test_publish_fills_the_date_and_titles_a_session_had_none_of(studio, capsys):
    """`prep` requires both, and nothing else in the loop ever wrote them."""
    prepared(studio, capsys)
    _, docs = studio

    call(studio, "publish", "Ada Whitfield")

    status = docs.get_status("doc-ada-03")
    assert status.date  # today, in the profile's timezone
    assert "Blackbird bars 9-16" in status.titles


def test_publish_does_not_overwrite_a_date_the_studio_already_set(studio, capsys):
    """The booked date is the day the lesson happened. Publishing can happen
    the following morning, so what it can infer is the worse record."""
    prepared(studio, capsys)
    _, docs = studio
    docs.set_properties("doc-ada-03", {"date": "2026-07-04", "titles": "Blackbird, by ear"})

    call(studio, "publish", "Ada Whitfield")

    status = docs.get_status("doc-ada-03")
    assert status.date == "2026-07-04"
    assert status.titles == "Blackbird, by ear"


def test_a_summary_that_lands_but_cannot_be_marked_done_says_so(studio, capsys):
    """The two writes fail independently. Reporting success here would hide
    exactly the state this fix exists to prevent: a published session that
    still looks in progress."""
    prepared(studio, capsys)
    _, docs = studio
    docs.fail_on_properties = True

    assert call(studio, "publish", "Ada Whitfield") == Exit.UPSTREAM
    payload = out(capsys)

    assert "marked done" in payload["message"]
    assert docs.list_blocks("doc-ada-03")  # the summary did land


def test_re_running_after_that_finishes_the_session_without_appending_again(studio, capsys):
    """The blocks are already where they belong, so the retry is the property
    write alone — appending a second copy is what the publish gate prevents."""
    prepared(studio, capsys)
    _, docs = studio
    docs.fail_on_properties = True
    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()
    after_first = len(docs.list_blocks("doc-ada-03"))

    docs.fail_on_properties = False
    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    assert len(docs.list_blocks("doc-ada-03")) == after_first
    assert docs.get_status("doc-ada-03").status == "Complete"


def test_publish_needs_a_summary(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "publish", "Ada Whitfield") == Exit.USAGE


# -- housekeeping ------------------------------------------------------------


def test_list_shows_staged_lessons(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "list") == Exit.OK
    assert out(capsys)["count"] == 1


def test_remove_discards_a_draft(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "remove", "Ada Whitfield") == Exit.OK
    assert out(capsys)["removed"] is True

    call(studio, "list")
    assert out(capsys)["count"] == 0


def test_clear_requires_confirmation(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "clear") == Exit.USAGE
    capsys.readouterr()

    call(studio, "list")
    assert out(capsys)["count"] == 1


def test_clear_with_yes_discards_everything(studio, capsys):
    stage_ada(studio, capsys)

    assert call(studio, "clear", "--yes") == Exit.OK
    assert out(capsys)["removed"] == 1


def test_the_resolution_gate_applies_here_too(studio, capsys):
    assert call(studio, "stage", "Whitfield") == Exit.NEEDS_HUMAN
    assert out(capsys)["error"] == "needs_human"


def test_re_staging_does_not_reopen_the_publish_gate(studio, capsys):
    """The draft is one file per learner, overwritten by every `stage`.

    The "already published" gate used to key on a mark inside that file, so
    staging again between two publishes wiped the evidence and the second
    publish appended a duplicate summary to the same page — the exact outcome
    the gate exists to prevent. The published record, which is per session and
    survives re-staging, is the durable answer.
    """
    prepared(studio, capsys)
    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()
    _, docs = studio
    after_first = len(docs.list_blocks("doc-ada-03"))

    # Staging the same session again — a studio correcting a title, say.
    stage_ada(studio, capsys)
    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY))
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    assert out(capsys)["skipped"] == "already published"
    assert len(docs.list_blocks("doc-ada-03")) == after_first


# -- publish resumes the description update it owes (F5) ----------------------


def _video_on(docs):
    """Put the lesson's recording on the document, keeping whatever else is
    on the page."""
    docs.blocks["doc-ada-03"] = [
        block for block in docs.blocks.get("doc-ada-03", []) if block.type != "video"
    ] + [Block(id="vid", type="video", url="https://youtu.be/dQw4w9WgXcQ")]


def test_a_failed_description_update_is_retried_by_a_re_run(studio, capsys, monkeypatch):
    """The summary is on the page for good; the description update is the part
    that can fail (here: a foreign-owned video) and be finished later — without
    appending a second copy of the summary to get there."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    _video_on(docs)
    prepared(studio, capsys)

    fake_publisher = FakePublisher()
    fake_publisher.foreign_video_ids = {"dQw4w9WgXcQ"}
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)
    assert payload["youtube"]["status"] == "error"
    after_first = len(docs.list_blocks("doc-ada-03"))

    # The ownership problem is fixed (the video is ours now, say).
    fake_publisher.foreign_video_ids.clear()
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    payload = out(capsys)
    assert payload["youtube"]["status"] == "ok"
    assert payload["youtube"]["video_id"] == "dQw4w9WgXcQ"
    # Nothing was appended a second time.
    assert len(docs.list_blocks("doc-ada-03")) == after_first


def test_the_recording_landing_later_gets_its_description(studio, capsys, monkeypatch):
    """The ordinary order for a filmed lesson: publish, then the video pipeline
    uploads and links the recording. The first publish found no video and owed
    nothing; the re-run writes the description once one exists."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    prepared(studio, capsys)

    fake_publisher = FakePublisher()
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    assert out(capsys)["youtube"] is None
    after_first = len(docs.list_blocks("doc-ada-03"))

    # The recording lands on the document afterwards.
    _video_on(docs)
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    payload = out(capsys)
    assert payload["youtube"]["status"] == "ok"
    assert payload["youtube"]["video_id"] == "dQw4w9WgXcQ"
    assert len(docs.list_blocks("doc-ada-03")) == after_first


def test_a_re_run_stops_retrying_once_attempts_run_out(studio, capsys, monkeypatch):
    """A permanently refused update must not be retried on every publish for
    the rest of the session's life."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    _video_on(docs)
    prepared(studio, capsys)

    fake_publisher = FakePublisher()
    fake_publisher.foreign_video_ids = {"dQw4w9WgXcQ"}
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)

    call(studio, "publish", "Ada Whitfield")  # attempt 1
    capsys.readouterr()
    call(studio, "publish", "Ada Whitfield")  # attempt 2
    capsys.readouterr()
    call(studio, "publish", "Ada Whitfield")  # attempt 3
    third = out(capsys)
    assert third["youtube"]["status"] == "error"
    updates_attempted = fake_publisher.descriptions
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    payload = out(capsys)
    assert payload["skipped"] == "already published"
    # The cap held: no fourth call was made.
    assert len(updates_attempted) == len(fake_publisher.descriptions)


def test_re_staging_does_not_reopen_the_description_gate(studio, capsys, monkeypatch):
    """The draft is wiped by every `stage`; the published record is what
    remembers the description was already written. Re-staging to correct a
    title must not re-update the video."""
    from baton.adapters.fakes import FakePublisher

    _, docs = studio
    _video_on(docs)
    prepared(studio, capsys)

    fake_publisher = FakePublisher()
    monkeypatch.setattr("baton.cli.cmd_lesson.open_publisher", lambda _config: fake_publisher)

    call(studio, "publish", "Ada Whitfield")
    capsys.readouterr()
    described = dict(fake_publisher.descriptions)

    stage_ada(studio, capsys)
    call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY))
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK

    payload = out(capsys)
    assert payload["skipped"] == "already published"
    assert fake_publisher.descriptions == described
