"""Rendering, and the ordering that stops a rewrite destroying a recording."""

from __future__ import annotations

from baton.adapters.docs.base import Block, PreservePolicy
from baton.adapters.fakes import FakeDocStore
from baton.pipelines.publish import SummaryPublisher
from baton.render import summary as render

SUMMARY = {
    "overview": ["Held the tempo all the way through."],
    "covered": [
        {"topic": "Blackbird bars 9-16", "detail": "Thumb-and-finger"},
        {"topic": "Chords", "notation": "Em7: 022033"},
    ],
    "focus": [{"issue": "Late change to C", "fix": "Four beats each, alone"}],
    "goals": ["Bars 9-16 at 80bpm", "Chord changes for five minutes"],
    "short_summary": {"covered": "Blackbird", "progress": "Tempo held", "homework": "80bpm"},
}

POLICY = PreservePolicy.from_config([{"type": "video"}, {"type": "embed"}])

# A page mid-term: last week's summary, plus a recording and a sheet embed.
EXISTING = [
    Block(id="old1", type="heading_2", text="Overview"),
    Block(id="old2", type="paragraph", text="Last week's text"),
    Block(id="vid", type="video", url="https://example.invalid/watch/week-2"),
    Block(id="sheet", type="embed", url="https://example.invalid/sheets/blackbird.pdf"),
]


def test_rendering_is_deterministic():
    """Same input, same output: every time. This is what makes a published
    document reviewable rather than something a model felt like writing."""
    first = render.to_blocks(SUMMARY)
    second = render.to_blocks(SUMMARY)

    assert first == second


def test_every_section_reaches_the_document():
    blocks = render.to_blocks(SUMMARY)
    types = [block["type"] for block in blocks]

    assert types.count("heading_2") == 4  # overview, covered, focus, goals
    assert "code" in types  # the notation
    assert types.count("to_do") == 2  # one per goal


def test_notation_is_rendered_as_code_so_alignment_survives():
    blocks = render.to_blocks(SUMMARY)
    code = [b for b in blocks if b["type"] == "code"]

    assert code[0]["code"]["rich_text"][0]["text"]["content"] == "Em7: 022033"


def test_section_headings_come_from_configuration():
    blocks = render.to_blocks(SUMMARY, sections={"overview": "ภาพรวม", "goals": "การบ้าน"})
    headings = [
        b["heading_2"]["rich_text"][0]["text"]["content"]
        for b in blocks
        if b["type"] == "heading_2"
    ]

    assert "ภาพรวม" in headings
    assert "การบ้าน" in headings


def test_callout_text_comes_from_the_studios_notes_not_the_model():
    """The model supplies an id; Baton supplies the words."""
    blocks = render.to_blocks(
        {**SUMMARY, "callouts": ["vibrato"]},
        callout_texts={"vibrato": "Pick first, then oscillate from the wrist."},
        callout_icon="🎧",
    )
    callouts = [b for b in blocks if b["type"] == "callout"]

    assert callouts[0]["callout"]["rich_text"][0]["text"]["content"].startswith("Pick first")
    assert callouts[0]["callout"]["icon"]["emoji"] == "🎧"


def test_an_unresolvable_callout_id_renders_nothing_rather_than_a_placeholder():
    blocks = render.to_blocks({**SUMMARY, "callouts": ["missing"]}, callout_texts={})

    assert not [b for b in blocks if b["type"] == "callout"]


def test_markdown_and_blocks_carry_the_same_content():
    markdown = render.to_markdown(SUMMARY)

    assert "Blackbird bars 9-16" in markdown
    assert "- [ ] Bars 9-16 at 80bpm" in markdown
    assert "Em7: 022033" in markdown


def test_the_short_message_is_assembled_from_validated_fields():
    """Composed field by field rather than taken as a block of text, so the
    format holds regardless of what a model would have written around it."""
    message = render.short_message(SUMMARY)

    assert message == "• Covered: Blackbird\n• Progress: Tempo held\n• Practice: 80bpm"


def test_the_short_message_omits_absent_homework():
    summary = {**SUMMARY, "short_summary": {"covered": "a", "progress": "b"}}

    assert render.short_message(summary) == "• Covered: a\n• Progress: b"


def test_message_labels_and_bullet_are_configurable():
    message = render.short_message(
        SUMMARY, bullet="-", labels={"covered": "เนื้อหา", "progress": "ความคืบหน้า"}
    )

    assert message.startswith("- เนื้อหา: Blackbird")


# -- publishing --------------------------------------------------------------


def test_publishing_keeps_the_recording_and_removes_last_weeks_text():
    docs = FakeDocStore(blocks={"doc1": list(EXISTING)})
    publisher = SummaryPublisher(docs, POLICY)

    result = publisher.publish("doc1", SUMMARY)

    remaining = {block.id for block in docs.list_blocks("doc1")}
    assert "vid" in remaining and "sheet" in remaining
    assert "old1" not in remaining and "old2" not in remaining
    assert result.preserved == 2


def test_publishing_appends_before_deleting():
    """If deletion failed, the page would carry a duplicate section, which is
    recoverable. Deleting first and then failing to append would leave the page
    empty of the lesson, with the recordings gone too."""
    docs = FakeDocStore(blocks={"doc1": list(EXISTING)})
    order: list[str] = []
    original_append = docs.append_blocks
    original_delete = docs.delete_blocks

    def track_append(doc_id, blocks):
        order.append("append")
        return original_append(doc_id, blocks)

    def track_delete(block_ids):
        order.append("delete")
        return original_delete(block_ids)

    docs.append_blocks = track_append  # type: ignore[method-assign]
    docs.delete_blocks = track_delete  # type: ignore[method-assign]

    SummaryPublisher(docs, POLICY).publish("doc1", SUMMARY)

    assert order == ["append", "delete"]


def test_publishing_to_an_empty_page_deletes_nothing():
    docs = FakeDocStore(blocks={"doc1": []})

    result = SummaryPublisher(docs, POLICY).publish("doc1", SUMMARY)

    assert result.deleted == 0
    assert result.appended > 0


def test_append_only_mode_leaves_everything_in_place():
    docs = FakeDocStore(blocks={"doc1": list(EXISTING)})

    result = SummaryPublisher(docs, POLICY).publish("doc1", SUMMARY, replace=False)

    assert result.deleted == 0
    assert {b.id for b in docs.list_blocks("doc1")} >= {"old1", "old2", "vid", "sheet"}


def test_plan_reports_what_would_change_without_touching_anything():
    docs = FakeDocStore(blocks={"doc1": list(EXISTING)})
    before = list(docs.list_blocks("doc1"))

    plan = SummaryPublisher(docs, POLICY).plan("doc1", SUMMARY)

    assert plan["would_preserve"] == 2
    assert plan["would_delete"] == 2
    assert plan["preserved_types"] == ["embed", "video"]
    assert docs.list_blocks("doc1") == before


def test_an_empty_preserve_policy_replaces_everything():
    """Including the recording. Worth asserting, because it is what a profile
    with no preserve rules genuinely means."""
    docs = FakeDocStore(blocks={"doc1": list(EXISTING)})

    result = SummaryPublisher(docs, PreservePolicy.from_config([])).publish("doc1", SUMMARY)

    assert result.preserved == 0
    assert result.deleted == 4
