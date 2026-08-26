"""Frozen Song DB resources are published once, in a reviewable shape."""

from __future__ import annotations

import pytest

from baton.adapters.docs.base import Block, PreservePolicy
from baton.adapters.fakes import FakeDocStore
from baton.cli.cmd_lesson import _require_force_compatible
from baton.domain.models import Piece
from baton.errors import UsageError
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
