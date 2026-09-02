"""Taking a published summary back off the page: the mirror of `publish`.

Runs the real SQLite staging pipeline against a scripted document store, so
the only thing substituted is the network. Every attribution mode is reached
through the CLI the way an operator reaches it, including the pre-block-ids
"legacy" record, which is manufactured by stripping the block list a real
publish wrote: the record otherwise being exactly what an older Baton left
behind.
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
            summary:
              footer:
                lines:
                  - "สรุปโดยผู้ช่วย AI เมื่อ {date} {time}"
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


def published(studio, capsys):
    """stage → ingest → publish, each output drained as it prints, the page
    left holding exactly what a real publish puts there."""
    assert call(studio, "stage", "Ada Whitfield", "--session", "3", "--context", "notes") == Exit.OK
    capsys.readouterr()
    assert call(studio, "ingest", "Ada Whitfield", "--json-text", json.dumps(SUMMARY)) == Exit.OK
    capsys.readouterr()
    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    return out(capsys)


def record_path(studio) -> Path:
    profile, _ = studio
    return profile / "state" / "published" / "1-3.json"


def strip_recorded_blocks(studio) -> None:
    """Turn a fresh record into a legacy one: same content, no block ids."""
    path = record_path(studio)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("blocks", None)
    path.write_text(json.dumps(record), encoding="utf-8")


def replace_block(docs: FakeDocStore, doc_id: str, block: Block, **changes) -> None:
    """Stand in for a person editing a block in the document app."""
    docs.blocks[doc_id] = [
        Block(
            id=block.id,
            type=changes.get("type", block.type),
            text=changes.get("text", block.text),
            url=changes.get("url", block.url),
            raw=block.raw,
        )
        if current.id == block.id
        else current
        for current in docs.blocks[doc_id]
    ]


# -- recorded mode ----------------------------------------------------------


def test_unpublish_removes_the_summary_and_keeps_the_recording(studio, capsys):
    published(studio, capsys)
    _, docs = studio

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["mode"] == "recorded"
    assert payload["removed"] == payload["would_delete"] > 0
    # Only the recording the preserve policy protects is left on the page.
    assert [block.id for block in docs.list_blocks("doc-ada-03")] == ["vid"]
    assert payload["record_removed"] is True
    assert not record_path(studio).exists()


def test_unpublish_returns_the_session_and_the_draft_to_where_they_were(studio, capsys):
    published(studio, capsys)
    _, docs = studio

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert docs.get_status("doc-ada-03").status == "In progress"
    assert payload["draft_restored"] is True

    assert call(studio, "show", "Ada Whitfield") == Exit.OK
    draft = out(capsys)
    assert draft["status"] == "summarised"
    assert draft["summary"] is not None


def test_unpublish_keeps_a_block_no_record_names(studio, capsys):
    """A block typed by hand after the publish stays: with ids in hand there
    is nothing to guess about, and no id accounts for it."""
    published(studio, capsys)
    _, docs = studio
    docs.blocks["doc-ada-03"].append(Block(id="hand", type="paragraph", text="typed by hand"))

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    out(capsys)

    assert {block.id for block in docs.list_blocks("doc-ada-03")} == {"vid", "hand"}


def test_an_edited_block_stops_the_unpublish(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    target = next(
        block for block in docs.list_blocks("doc-ada-03") if block.id != "vid" and block.text
    )
    replace_block(docs, "doc-ada-03", target, text=target.text + " (fixed by hand)")

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.NEEDS_HUMAN
    payload = out(capsys)

    assert [candidate["kind"] for candidate in payload["details"]["candidates"]] == ["edited"]
    assert payload["details"]["candidates"][0]["id"] == target.id
    # Nothing went, and the record survives so the unpublish can be retried.
    assert target.id in {block.id for block in docs.list_blocks("doc-ada-03")}
    assert record_path(studio).exists()


def test_blocks_someone_removed_first_count_as_already_gone(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    appended = [block for block in docs.list_blocks("doc-ada-03") if block.id != "vid"]
    docs.blocks["doc-ada-03"] = [
        block for block in docs.blocks["doc-ada-03"] if block.id != appended[0].id
    ]

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["already_gone"] == 1
    assert [block.id for block in docs.list_blocks("doc-ada-03")] == ["vid"]


def test_dry_run_changes_nothing(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    before = docs.list_blocks("doc-ada-03")

    assert call(studio, "unpublish", "Ada Whitfield", "--dry-run") == Exit.OK
    payload = out(capsys)

    assert payload["dry_run"] is True
    assert payload["would_delete"] > 0
    assert docs.list_blocks("doc-ada-03") == before
    assert docs.get_status("doc-ada-03").status == "Complete"
    assert record_path(studio).exists()


def test_unpublish_selects_a_session_like_send_does(studio, capsys):
    published(studio, capsys)

    assert call(studio, "unpublish", "Ada Whitfield", "--session", "99") == Exit.STATE
    assert "99" in out(capsys)["message"]
    assert record_path(studio).exists()

    assert call(studio, "unpublish", "Ada Whitfield", "--session", "3") == Exit.OK
    assert not record_path(studio).exists()


def test_unpublish_needs_something_published_first(studio, capsys):
    assert call(studio, "unpublish", "Ada Whitfield") == Exit.STATE
    assert "Nothing has been published" in out(capsys)["message"]


# -- whole page -------------------------------------------------------------


def test_whole_page_demands_the_double_opt_in(studio, capsys):
    published(studio, capsys)

    assert call(studio, "unpublish", "Ada Whitfield", "--whole-page") == Exit.USAGE
    assert "force" in out(capsys)["remedy"]


def test_whole_page_removes_even_the_recording(studio, capsys):
    published(studio, capsys)
    _, docs = studio

    assert call(studio, "unpublish", "Ada Whitfield", "--whole-page", "--force") == Exit.OK
    payload = out(capsys)

    assert payload["mode"] == "whole_page"
    assert docs.list_blocks("doc-ada-03") == []


# -- legacy mode ------------------------------------------------------------


def test_a_legacy_record_is_matched_by_re_rendering(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    strip_recorded_blocks(studio)

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)

    assert payload["mode"] == "legacy"
    assert payload["ambiguous"] == []
    # The footer cannot be re-rendered (it says when the clock ran), so this
    # passing means its blocks matched by the clock-independent pattern.
    #
    # The embed the piece renderer wrote stays where the recorded mode took it:
    # with no ids there is no proof Baton wrote it, and the preserve policy
    # protects embeds. Less trust, less removed, which is the whole ordering.
    assert [block.id for block in docs.list_blocks("doc-ada-03")] == ["vid", "new-3"]


def test_a_hand_addition_on_a_legacy_page_is_ambiguous(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    strip_recorded_blocks(studio)
    docs.blocks["doc-ada-03"].append(Block(id="hand", type="paragraph", text="a note"))

    assert call(studio, "unpublish", "Ada Whitfield") == Exit.NEEDS_HUMAN
    payload = out(capsys)

    assert [candidate["kind"] for candidate in payload["details"]["candidates"]] == ["ambiguous"]
    assert payload["details"]["candidates"][0]["id"] == "hand"
    # Ambiguity removes nothing; the decision belongs to a person.
    assert {block.id for block in docs.list_blocks("doc-ada-03")} >= {"vid", "hand"}
    assert record_path(studio).exists()


# -- the round trip ---------------------------------------------------------


def test_a_lesson_can_be_published_again_after_unpublishing(studio, capsys):
    published(studio, capsys)
    _, docs = studio
    assert call(studio, "unpublish", "Ada Whitfield") == Exit.OK
    capsys.readouterr()

    assert call(studio, "publish", "Ada Whitfield") == Exit.OK
    payload = out(capsys)
    assert payload["appended"] > 0

    # The new record names the blocks now on the page, not the removed ones.
    record = json.loads(record_path(studio).read_text(encoding="utf-8"))
    recorded_ids = {entry["id"] for entry in record["blocks"]}
    live_ids = {block.id for block in docs.list_blocks("doc-ada-03")}
    assert recorded_ids
    assert recorded_ids <= live_ids
