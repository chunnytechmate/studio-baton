"""Frozen Song DB resources are published once, in a reviewable shape."""

from __future__ import annotations

import pytest

from baton.adapters.docs.base import Block, PreservePolicy
from baton.adapters.fakes import FakeDocStore
from baton.cli.cmd_lesson import _require_force_compatible
from baton.domain.models import Piece, Session
from baton.errors import GateError, UsageError
from baton.pipelines.learner import PublishedPieceUpdater
from baton.pipelines.publish import SummaryPublisher
from baton.pipelines.staging import LessonDraft, PieceSnapshot, PublishedRecord
from baton.render import piece as render_piece
from baton.render import summary as render_summary

SUMMARY = {"overview": ["Held a steady tempo."], "covered": [], "focus": [], "goals": []}
PIECE = Piece(
    id="7",
    title=" Fictional Study ",
    source_link=" https://example.invalid/source ",
    practice_track=" https://example.invalid/practice ",
    sheet_link=" https://example.invalid/sheet ",
)
SNAPSHOT = PieceSnapshot(status="captured", captured_at="2026-08-24T00:00:00Z", piece=PIECE)
POLICY = PreservePolicy.from_config(
    [{"type": "video"}, {"type": "bookmark"}, {"type": "embed"}, {"type": "callout"}]
)


def test_piece_blocks_have_the_exact_legacy_order_and_shape():
    assert render_piece.to_blocks(SNAPSHOT) == [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": "🎵 Fictional Study"}}]
            },
        },
        {
            "object": "block",
            "type": "bookmark",
            "bookmark": {"url": "https://example.invalid/source"},
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": "Practice track: https://example.invalid/practice"},
                    }
                ],
                "icon": {"type": "emoji", "emoji": "🎧"},
            },
        },
        {
            "object": "block",
            "type": "embed",
            "embed": {"url": "https://example.invalid/sheet"},
        },
    ]


def test_missing_links_and_non_captured_states_render_nothing_extra():
    bare = PieceSnapshot.capture(Piece(id="8", title="Bare Study"))

    assert [block["type"] for block in render_piece.to_blocks(bare)] == ["heading_2"]
    assert render_piece.to_blocks(PieceSnapshot.capture(None)) == []
    assert render_piece.to_blocks(PieceSnapshot.unavailable()) == []


def test_plan_matches_publish_and_summary_only_callers_stay_compatible():
    docs = FakeDocStore()
    publisher = SummaryPublisher(docs, POLICY)

    plan = publisher.plan("doc", SUMMARY, piece_snapshot=SNAPSHOT)
    result = publisher.publish("doc", SUMMARY, piece_snapshot=SNAPSHOT)

    assert plan["would_append"] == result.appended
    assert plan["would_append_resources"] == 3
    assert plan["piece_snapshot_status"] == "captured"
    assert plan["piece_id"] == "7"

    old_docs = FakeDocStore()
    old_result = SummaryPublisher(old_docs, POLICY).publish("old", SUMMARY)
    assert old_result.appended == len(render_summary.to_blocks(SUMMARY))


def test_same_snapshot_force_skips_exact_preserved_resources_and_restores_missing_ones():
    existing = [
        Block(id="source", type="bookmark", url="https://example.invalid/source"),
        Block(
            id="practice",
            type="callout",
            text="Practice track: https://example.invalid/practice",
            icon="🎧",
        ),
        Block(id="video", type="video", url="https://example.invalid/video"),
        Block(id="old", type="paragraph", text="old summary"),
    ]
    docs = FakeDocStore(blocks={"doc": existing})

    result = SummaryPublisher(docs, POLICY).publish("doc", SUMMARY, piece_snapshot=SNAPSHOT)
    remaining = docs.list_blocks("doc")

    assert result.preserved == 3
    assert sum(block.type == "bookmark" for block in remaining) == 1
    assert sum(block.type == "callout" for block in remaining) == 1
    assert sum(block.type == "embed" for block in remaining) == 1
    assert "old" not in {block.id for block in remaining}


def test_resource_identity_is_exact_not_broad_url_normalisation():
    docs = FakeDocStore(
        blocks={
            "doc": [
                Block(
                    id="source",
                    type="bookmark",
                    url="https://example.invalid/source?version=old",
                )
            ]
        }
    )

    SummaryPublisher(docs, POLICY).publish("doc", SUMMARY, piece_snapshot=SNAPSHOT)

    assert sum(block.type == "bookmark" for block in docs.list_blocks("doc")) == 2


@pytest.mark.parametrize(
    "published", [{}, {"piece_snapshot": PieceSnapshot.capture(None).to_dict()}]
)
def test_force_refuses_unknown_or_changed_snapshots_before_publish(published):
    draft = LessonDraft("1", "Ada", 3, piece_snapshot=SNAPSHOT)

    with pytest.raises(UsageError):
        _require_force_compatible(draft, published, force=True)


def test_same_snapshot_can_force_and_published_record_keeps_it(tmp_path):
    draft = LessonDraft("1", "Ada", 3, piece_snapshot=SNAPSHOT)
    same = {"piece_snapshot": {**SNAPSHOT.to_dict(), "captured_at": "later"}}

    _require_force_compatible(draft, same, force=True)
    records = PublishedRecord(tmp_path)
    records.save(draft, short_message="summary")

    assert PieceSnapshot.from_record(records.get("1", 3) or {}).same_content(SNAPSHOT)


def _published_piece(records, *, session_number, doc_id, snapshot):
    draft = LessonDraft(
        "1",
        "Ada",
        session_number,
        piece_snapshot=snapshot,
        doc_id=doc_id,
    )
    records.save(draft, short_message="summary", doc_url=f"https://example.invalid/{doc_id}")


def test_published_piece_update_replaces_only_the_renderer_owned_section(tmp_path):
    old = Piece(
        id="old",
        title="Old Study",
        source_link="https://example.invalid/old-source",
        practice_track="https://example.invalid/old-practice",
        sheet_link="https://example.invalid/old-sheet",
    )
    new = Piece(
        id="new",
        title="New Study",
        source_link="https://example.invalid/new-source",
    )
    old_snapshot = PieceSnapshot.capture(old)
    records = PublishedRecord(tmp_path / "published")
    _published_piece(records, session_number=8, doc_id="doc-8", snapshot=old_snapshot)
    docs = FakeDocStore(
        blocks={
            "doc-8": [
                Block(id="piece-heading", type="heading_2", text="🎵 Old Study"),
                Block(id="piece-source", type="bookmark", url=old.source_link),
                Block(
                    id="piece-practice",
                    type="callout",
                    text=f"Practice track: {old.practice_track}",
                    icon="🎧",
                ),
                Block(id="piece-sheet", type="embed", url=old.sheet_link),
                Block(id="summary-heading", type="heading_2", text="Overview"),
                Block(id="summary", type="paragraph", text="Teacher-approved summary"),
                Block(id="recording", type="video", url="https://youtu.be/lesson"),
                Block(id="theory", type="callout", text="Keep this theory", icon="💡"),
            ]
        }
    )
    updater = PublishedPieceUpdater(docs, records, POLICY)
    sessions = [Session(id="s8", learner_id="1", number=8, doc_id="doc-8")]

    plan = updater.plan(sessions, from_piece_id="old", to_piece=new)

    assert plan["would_update"] == 1
    assert plan["pages"][0]["delete_ids"] == [
        "piece-heading",
        "piece-source",
        "piece-practice",
        "piece-sheet",
    ]
    assert {block.id for block in docs.list_blocks("doc-8")} >= {
        "summary",
        "recording",
        "theory",
    }

    result = updater.apply(plan)
    remaining = docs.list_blocks("doc-8")

    assert result["updated"] == 1
    assert {block.id for block in remaining}.isdisjoint(
        {"piece-heading", "piece-source", "piece-practice", "piece-sheet"}
    )
    assert {block.id for block in remaining} >= {
        "summary-heading",
        "summary",
        "recording",
        "theory",
    }
    assert any(block.text == "🎵 New Study" for block in remaining)
    assert any(block.url == new.source_link for block in remaining)


def test_published_piece_update_uses_the_old_assignment_id_not_every_old_session(tmp_path):
    selected = PieceSnapshot.capture(Piece(id="selected", title="Selected Old Study"))
    unrelated = PieceSnapshot.capture(Piece(id="unrelated", title="Earlier Repertoire"))
    records = PublishedRecord(tmp_path / "published")
    _published_piece(records, session_number=3, doc_id="doc-3", snapshot=selected)
    _published_piece(records, session_number=2, doc_id="doc-2", snapshot=unrelated)
    docs = FakeDocStore(
        blocks={
            "doc-3": [Block(id="selected-heading", type="heading_2", text="🎵 Selected Old Study")],
            "doc-2": [
                Block(id="unrelated-heading", type="heading_2", text="🎵 Earlier Repertoire")
            ],
        }
    )
    updater = PublishedPieceUpdater(docs, records, POLICY)
    sessions = [
        Session(id="s2", learner_id="1", number=2, doc_id="doc-2"),
        Session(id="s3", learner_id="1", number=3, doc_id="doc-3"),
    ]

    result = updater.apply(
        updater.plan(
            sessions,
            from_piece_id="selected",
            to_piece=Piece(id="new", title="New Study"),
        )
    )

    assert [page["session_number"] for page in result["pages"]] == [3]
    assert [block.id for block in docs.list_blocks("doc-2")] == ["unrelated-heading"]


def test_published_piece_update_refuses_duplicate_renderer_headings(tmp_path):
    old = PieceSnapshot.capture(Piece(id="old", title="Duplicated"))
    records = PublishedRecord(tmp_path / "published")
    _published_piece(records, session_number=1, doc_id="doc-1", snapshot=old)
    docs = FakeDocStore(
        blocks={
            "doc-1": [
                Block(id="one", type="heading_2", text="🎵 Duplicated"),
                Block(id="two", type="heading_2", text="🎵 Duplicated"),
            ]
        }
    )
    updater = PublishedPieceUpdater(docs, records, POLICY)

    with pytest.raises(GateError):
        updater.plan(
            [Session(id="s1", learner_id="1", number=1, doc_id="doc-1")],
            from_piece_id="old",
            to_piece=Piece(id="new", title="New"),
        )

    assert [block.id for block in docs.list_blocks("doc-1")] == ["one", "two"]
