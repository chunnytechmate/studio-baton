"""`learner attach-work` — a recording that skipped the pipeline, put on its page.

The video pipeline writes the lesson's own recording onto the session page,
but it is the only writer. A recording that did not go through it — a
teacher's own edit, a clip shared directly — had links in the database and
nothing on any page, and the Drive side of a recording never landed on a page
at all.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest
from tests.test_send_video import FakeMessenger  # noqa: F401 - keeps linters honest about reuse

import baton
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit
from baton.pipelines.recording import attach_work, recording_blocks

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

WORK = {
    "id": "w1",
    "learner_id": "1",
    "title": "Blackbird — full take",
    "type": "performance",
    "video_link": "https://youtu.be/attach-me",
    "drive_link": "https://drive.google.com/file/keep-me/view",
    "performed_date": "2026-08-23",
}


@pytest.fixture
def studio(profile, monkeypatch):
    """One learner, session 3 In progress, one recorded work with both sides."""
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO works (learner_id, title, type, video_link, drive_link, performed_date) "
        "VALUES ('1', ?, 'performance', ?, ?, '2026-08-23')",
        (WORK["title"], WORK["video_link"], WORK["drive_link"]),
    )
    connection.commit()
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            labels:
              learner: student
              session: week
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            docs:
              driver: notion
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    docs = FakeDocStore(
        statuses={
            "doc-ada-01": DocStatus(doc_id="doc-ada-01", status="Complete"),
            "doc-ada-02": DocStatus(doc_id="doc-ada-02", status="Not started"),
            "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="In progress"),
        },
        blocks={"doc-ada-03": []},
    )
    monkeypatch.setattr("baton.cli.cmd_learner.open_docs", lambda _config: docs)
    return profile, docs


def call(studio, *args):
    return run(["--profile", str(studio[0]), "--json", "learner", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def _work(**overrides):
    from baton.domain.models import Work

    return Work(**{**WORK, **overrides})


# -- the blocks ---------------------------------------------------------------


def test_the_section_matches_the_shape_the_old_push_wrote():
    blocks = recording_blocks(_work())

    assert blocks[0]["type"] == "heading_3"
    assert blocks[0]["heading_3"]["rich_text"][0]["text"]["content"] == "🎬 ผลงาน Record"
    title_runs = blocks[1]["paragraph"]["rich_text"]
    assert title_runs[1]["text"]["content"] == WORK["title"]
    assert title_runs[1]["annotations"]["bold"] is True
    assert blocks[2]["video"]["external"]["url"] == WORK["video_link"]
    assert blocks[3]["bookmark"]["url"] == WORK["drive_link"]


def test_only_the_sides_the_work_has_are_built():
    blocks = recording_blocks(_work(drive_link=""))

    assert [b["type"] for b in blocks] == ["heading_3", "paragraph", "video"]


# -- the attach, and its idempotency ------------------------------------------


def test_a_fresh_work_writes_the_whole_section(studio):
    docs = studio[1]

    result = attach_work(docs, "doc-ada-03", _work())

    assert result["appended"] == 4
    urls = [b.url for b in docs.list_blocks("doc-ada-03")]
    assert WORK["video_link"] in urls
    assert WORK["drive_link"] in urls


def test_a_link_already_on_the_page_is_not_written_twice(studio):
    """The old rule cleared every video and bookmark first — safe when it was
    the only writer, fatal now that the pipeline puts the lesson's own video
    on the same page. The guard is the URL."""
    docs = studio[1]

    first = attach_work(docs, "doc-ada-03", _work())
    second = attach_work(docs, "doc-ada-03", _work())

    assert first["appended"] == 4
    assert second["appended"] == 0
    assert set(second["already_on_page"]) == {WORK["video_link"], WORK["drive_link"]}
    # Still exactly one section on the page.
    assert len(docs.list_blocks("doc-ada-03")) == 4


def test_one_new_side_carries_a_fresh_section_for_it(studio):
    docs = studio[1]
    docs.blocks["doc-ada-03"] = [
        Block(id="v", type="video", url=WORK["video_link"]),
    ]

    result = attach_work(docs, "doc-ada-03", _work())

    # Heading + title + the Drive side; the video side was already there.
    assert result["appended"] == 3
    assert result["already_on_page"] == [WORK["video_link"]]


def test_the_lessons_own_video_is_never_touched(studio):
    docs = studio[1]
    docs.blocks["doc-ada-03"] = [
        Block(id="lesson", type="video", url="https://youtu.be/the-lesson-itself"),
    ]

    attach_work(docs, "doc-ada-03", _work())

    urls = [b.url for b in docs.list_blocks("doc-ada-03")]
    assert urls.count("https://youtu.be/the-lesson-itself") == 1


def test_a_work_with_no_links_is_refused(studio):
    import pytest

    from baton.errors import GateError

    with pytest.raises(GateError, match="no link to put on the page"):
        attach_work(studio[1], "doc-ada-03", _work(video_link="", drive_link=""))


# -- the command ---------------------------------------------------------------


def test_without_a_pick_it_lists_and_asks(studio, capsys):
    assert call(studio, "attach-work", "Ada Whitfield") == Exit.NEEDS_HUMAN

    payload = out(capsys)
    assert payload["details"]["candidates"][0]["name"] == WORK["title"]
    assert studio[1].list_blocks("doc-ada-03") == []


def test_it_targets_the_session_in_progress(studio, capsys):
    assert call(studio, "attach-work", "Ada Whitfield", "--pick", "1") == Exit.OK

    payload = out(capsys)
    assert payload["doc_id"] == "doc-ada-03"
    assert payload["appended"] == 4


def test_an_explicit_session_is_honoured(studio, capsys):
    assert call(studio, "attach-work", "Ada Whitfield", "--pick", "1", "--session", "1") == Exit.OK

    payload = out(capsys)
    assert payload["doc_id"] == "doc-ada-01"


def test_no_session_in_progress_names_the_way_out(studio, capsys):
    docs = studio[1]
    docs.statuses["doc-ada-03"] = DocStatus(doc_id="doc-ada-03", status="Not started")

    assert call(studio, "attach-work", "Ada Whitfield", "--pick", "1") == Exit.USAGE

    payload = out(capsys)
    assert "none is in progress" in payload["message"]
    assert "--session" in payload["remedy"]


def test_dry_run_writes_nothing(studio, capsys):
    assert call(studio, "attach-work", "Ada Whitfield", "--pick", "1", "--dry-run") == Exit.OK

    payload = out(capsys)
    assert payload["dry_run"] is True
    assert payload["would_append"] == 4
    assert studio[1].list_blocks("doc-ada-03") == []


def test_running_it_twice_is_still_one_section(studio, capsys):
    call(studio, "attach-work", "Ada Whitfield", "--pick", "1")
    capsys.readouterr()

    assert call(studio, "attach-work", "Ada Whitfield", "--pick", "1") == Exit.OK

    payload = out(capsys)
    assert payload["appended"] == 0
    assert len(studio[1].list_blocks("doc-ada-03")) == 4
