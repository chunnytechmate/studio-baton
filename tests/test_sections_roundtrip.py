"""What Baton writes onto a page, Baton has to be able to read back.

The renderer and the section reader kept separate vocabularies and disagreed:
the renderer wrote "What we covered", the reader looked for เนื้อหา /
สิ่งที่เรียน / core lesson, and the largest section of every published summary
read back empty. `prep.required` lists `content` and prep is fail-closed, so
the teacher got no briefing at all for any learner whose last summary came
from Baton: with nothing anywhere saying why.
"""

from __future__ import annotations

from pathlib import Path

from baton.adapters.docs.base import Block
from baton.core.config import Config
from baton.domain.prep import SectionRules
from baton.domain.sections import (
    READ_KEYWORDS,
    WRITES_INTO,
    WRITTEN_HEADINGS,
    with_written_headings,
)
from baton.render import summary as render

SUMMARY = {
    "overview": ["เล่นได้ตลอดเพลงแล้ว"],
    "progress": [{"before": "ต้องครูช่วยนับจังหวะ", "after": "นับเองได้ตลอดเพลง"}],
    "covered": [{"topic": "Blackbird ห้อง 9-16", "detail": "นิ้วโป้งกับนิ้วชี้"}],
    "focus": [{"issue": "เปลี่ยนคอร์ด C ช้า", "fix": "ซ้อมสลับช้า ๆ"}],
    "goals": ["ห้อง 9-16 ที่ 80bpm"],
}


def _config(data: dict | None = None) -> Config:
    return Config(data=data or {}, config_file=Path("baton.yaml"), profile_dir=Path("."))


def _as_page(blocks: list[dict]) -> list[Block]:
    """The rendered blocks as the document store hands them back."""
    read: list[Block] = []
    for index, block in enumerate(blocks):
        kind = block["type"]
        body = block[kind]
        text = "".join(run["text"]["content"] for run in body.get("rich_text", []))
        read.append(Block(id=f"b{index}", type=kind, text=text))
    return read


# -- the round trip ---------------------------------------------------------


def test_every_section_baton_writes_reads_back_with_content():
    """The regression itself: publish, then read the page back, and expect the
    lesson to still be there."""
    page = _as_page(render.to_blocks(SUMMARY))
    sections = SectionRules.from_config(_config()).read(page)

    assert "เล่นได้ตลอดเพลงแล้ว" in sections["overview"]
    assert "Blackbird" in sections["content"]
    assert "เปลี่ยนคอร์ด C ช้า" in sections["focus"]


def test_the_progress_section_reads_back_too():
    """Added after this module existed, so it is the one section whose written
    and read names match, and the trap this file guards is a section that is
    written and then cannot be found."""
    page = _as_page(render.to_blocks(SUMMARY))
    sections = SectionRules.from_config(_config()).read(page)

    assert "นับเองได้ตลอดเพลง" in sections["progress"]


def test_progress_is_not_required_for_prep():
    """Deliberate: every page published before this section existed has no
    such heading, and `prep.required` is fail-closed. Adding it there would
    refuse the teacher a briefing for every learner with an older summary:
    exactly the failure this module was written to fix."""
    from baton.core.config import packaged_defaults

    assert "progress" not in packaged_defaults()["prep"]["required"]


def test_the_content_section_is_not_empty():
    """Pinned on its own because this is the field `prep.required` blocks on,
    and an empty one refuses the whole briefing."""
    page = _as_page(render.to_blocks(SUMMARY))

    assert SectionRules.from_config(_config()).read(page)["content"].strip()


def test_a_renamed_heading_is_still_found():
    """The trap the shared vocabulary exists to disarm: a studio translating
    its headings must not silently break its own prep."""
    config = _config({"summary": {"sections": {"covered": "เนื้อหาที่เรียนวันนี้"}}})
    page = _as_page(render.to_blocks(SUMMARY, sections={"covered": "เนื้อหาที่เรียนวันนี้"}))

    assert "Blackbird" in SectionRules.from_config(config).read(page)["content"]


def test_a_page_the_old_pipeline_wrote_still_reads():
    """The studio template's own Thai headings keep working: Baton's headings
    are added to the list, not swapped in for it."""
    page = [
        Block(id="h", type="heading_2", text="เนื้อหาและเทคนิคที่เรียน"),
        Block(id="b", type="paragraph", text="ซ้อม Blackbird ห้อง 9-16"),
    ]

    assert "Blackbird" in SectionRules.from_config(_config()).read(page)["content"]


# -- the mapping itself -----------------------------------------------------


def test_every_written_section_lands_somewhere_readable():
    """A heading Baton writes with nowhere to be read back is the whole bug."""
    for written in WRITTEN_HEADINGS:
        assert written in WRITES_INTO, f"{written} is written but never read"
        assert WRITES_INTO[written] in READ_KEYWORDS


def test_a_written_heading_joins_the_keywords_it_feeds():
    merged = with_written_headings(READ_KEYWORDS, WRITTEN_HEADINGS)

    assert "What we covered" in merged["content"]
    assert "Focus areas" in merged["focus"]


def test_a_heading_the_keywords_already_cover_is_left_alone():
    """ "Overview" and the keyword "overview" are the same word, as are
    "Practice goals" and "practice goals", only the two headings the keywords
    genuinely missed get added."""
    merged = with_written_headings(READ_KEYWORDS, WRITTEN_HEADINGS)

    assert merged["overview"] == READ_KEYWORDS["overview"]
    assert merged["practice_goals"] == READ_KEYWORDS["practice_goals"]


def test_the_studios_own_keywords_keep_their_priority():
    """Appended, not prepended: a studio that ordered its keywords deliberately
    keeps that order."""
    merged = with_written_headings(READ_KEYWORDS, WRITTEN_HEADINGS)

    assert merged["content"][: len(READ_KEYWORDS["content"])] == READ_KEYWORDS["content"]


def test_a_heading_already_listed_is_not_added_twice():
    keywords = {"content": ("What we covered",)}

    merged = with_written_headings(keywords, {"covered": "what we covered"})

    assert merged["content"] == ("What we covered",)


def test_sections_that_are_read_but_never_written_are_untouched():
    """`homework` arrives through docs.homework_types, and `next_goal` is not
    something Baton writes at all."""
    merged = with_written_headings(READ_KEYWORDS, WRITTEN_HEADINGS)

    assert merged["homework"] == READ_KEYWORDS["homework"]
    assert merged["next_goal"] == READ_KEYWORDS["next_goal"]


def test_a_blank_heading_adds_nothing():
    merged = with_written_headings(READ_KEYWORDS, {"covered": "   "})

    assert merged["content"] == READ_KEYWORDS["content"]
