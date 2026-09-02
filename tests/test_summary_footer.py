"""The disclosure at the bottom of a published summary.

Every summary the studio's previous pipeline wrote said, in a dated line, that
an assistant had written it. The rewrite dropped that line while keeping the
regex that strips it out of old pages, so Baton removed a disclosure it no
longer wrote. These pin it back on.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from baton.adapters.docs.base import PreservePolicy
from baton.adapters.fakes import FakeDocStore
from baton.domain.footer import Footer, FooterError, emphasis
from baton.pipelines.publish import SummaryPublisher
from baton.render import summary as render

SUMMARY = {
    "overview": ["Held the tempo all the way through."],
    "goals": ["Bars 9-16 at 80bpm"],
}

THAI_MONTHS = [
    "มกราคม",
    "กุมภาพันธ์",
    "มีนาคม",
    "เมษายน",
    "พฤษภาคม",
    "มิถุนายน",
    "กรกฎาคม",
    "สิงหาคม",
    "กันยายน",
    "ตุลาคม",
    "พฤศจิกายน",
    "ธันวาคม",
]

STUDIO = Footer(
    lines=(
        "*หางยาว ({date} เวลา {time})*",
        "*สรุปนี้มาจากผู้ช่วย AI*",
        "powered by chunnylab.com",
    ),
    date_format="{day} {month} {year}".replace("{day}", "%-d").replace("{year}", "%Y"),
    era="buddhist",
    months=tuple(THAI_MONTHS),
)

MOMENT = datetime(2026, 8, 27, 2, 30, tzinfo=ZoneInfo("Asia/Bangkok"))


# -- rendering the lines ----------------------------------------------------


def test_no_footer_is_configured_by_default():
    """A studio that never asked for a disclaimer does not get one."""
    assert not Footer.from_config({})
    assert Footer.from_config({}).render(MOMENT) == []
    assert Footer.from_config(None).render(MOMENT) == []


def test_the_year_is_written_in_the_studios_era():
    """2026 reads as 2569 on a page a Thai parent opens."""
    lines = STUDIO.render(MOMENT)

    assert lines[0] == "*หางยาว (27 สิงหาคม 2569 เวลา 02:30)*"


def test_the_month_name_comes_from_configuration_not_strftime():
    """%B renders English wherever the studio's locale is not installed, which
    in a container is nearly always, and nobody notices until a parent does."""
    assert "สิงหาคม" in STUDIO.render(MOMENT)[0]


def test_a_month_placeholder_without_month_names_is_refused_not_guessed():
    footer = Footer(lines=("{date}",), date_format="%-d {month} %Y")

    with pytest.raises(FooterError, match="no month names"):
        footer.render(MOMENT)


def test_the_clock_reading_is_the_studios_own():
    """Published at 02:30 in Bangkok, stamped 02:30, not 19:30 the day before,
    which is what the server's clock would have said."""
    lines = STUDIO.render(MOMENT)

    assert "02:30" in lines[0]
    assert "27 สิงหาคม" in lines[0]


def test_lines_without_placeholders_pass_through_unchanged():
    assert STUDIO.render(MOMENT)[2] == "powered by chunnylab.com"


def test_an_unknown_placeholder_names_itself():
    footer = Footer(lines=("written by {nobody}",))

    with pytest.raises(FooterError, match="unknown placeholder"):
        footer.render(MOMENT)


def test_twelve_month_names_or_none():
    with pytest.raises(FooterError, match="12 names"):
        Footer(months=("Jan", "Feb"))


def test_an_unknown_era_is_refused_at_construction():
    with pytest.raises(FooterError, match="Unknown era"):
        Footer(era="martian")


def test_from_config_reads_the_summary_footer_section():
    footer = Footer.from_config(
        {
            "lines": ["*by the assistant, {date}*"],
            "date_format": "%Y-%m-%d",
            "era": "gregorian",
        }
    )

    assert footer.render(MOMENT) == ["*by the assistant, 2026-08-27*"]


# -- emphasis ---------------------------------------------------------------


def test_paired_asterisks_mark_italics():
    assert [(s.text, s.italic) for s in emphasis("*หางยาว* said so")] == [
        ("หางยาว", True),
        (" said so", False),
    ]


def test_an_unpaired_asterisk_is_literal_text():
    """A stray asterisk should print, not swallow the rest of the sentence."""
    assert [(s.text, s.italic) for s in emphasis("5 * 4 = 20")] == [("5 * 4 = 20", False)]


def test_a_fully_italic_line_is_one_run():
    assert [(s.text, s.italic) for s in emphasis("*all of it*")] == [("all of it", True)]


# -- reaching the document --------------------------------------------------


def test_the_footer_is_the_last_thing_on_the_page():
    blocks = render.to_blocks(SUMMARY, footer_lines=["*by the assistant*"])

    assert blocks[-1]["type"] == "paragraph"
    assert blocks[-1]["paragraph"]["rich_text"][0]["text"]["content"] == "by the assistant"
    assert blocks[-1]["paragraph"]["rich_text"][0]["annotations"]["italic"] is True


def test_a_summary_with_no_footer_renders_exactly_as_it_did_before():
    """No studio that has not configured a footer sees its pages change."""
    assert render.to_blocks(SUMMARY) == render.to_blocks(SUMMARY, footer_lines=[])


def test_the_markdown_preview_shows_what_will_be_published():
    """The preview and the page have to agree about what goes out, or the
    review step is reviewing a different document than the one parents read."""
    markdown = render.to_markdown(SUMMARY, footer_lines=["*by the assistant*"])

    assert markdown.rstrip().endswith("*by the assistant*")


def test_publishing_puts_the_disclosure_on_the_document():
    docs = FakeDocStore(blocks={"doc1": []})
    doc_id = "doc1"
    publisher = SummaryPublisher(
        docs,
        PreservePolicy.from_config([]),
        footer=STUDIO,
        timezone="Asia/Bangkok",
        clock=lambda: MOMENT,
    )

    publisher.publish(doc_id, SUMMARY)

    written = [block.text for block in docs.list_blocks(doc_id)]
    assert "หางยาว (27 สิงหาคม 2569 เวลา 02:30)" in written
    assert "powered by chunnylab.com" in written


def test_the_dry_run_plan_counts_the_footer_it_would_write():
    docs = FakeDocStore(blocks={"doc1": []})
    doc_id = "doc1"
    bare = SummaryPublisher(docs, PreservePolicy.from_config([]))
    with_footer = SummaryPublisher(
        docs,
        PreservePolicy.from_config([]),
        footer=STUDIO,
        timezone="Asia/Bangkok",
        clock=lambda: MOMENT,
    )

    assert with_footer.plan(doc_id, SUMMARY)["would_append"] == (
        bare.plan(doc_id, SUMMARY)["would_append"] + len(STUDIO.lines)
    )


# -- and back out again, when the next lesson reads this page ----------------


def test_the_footer_pattern_matches_what_the_footer_writes():
    """Derived from the lines rather than hand-written, so editing the
    disclosure cannot leave a stale regex that silently stops stripping it."""
    pattern = STUDIO.pattern()
    assert pattern is not None

    written = "\n".join(line.replace("*", "") for line in STUDIO.render(MOMENT))
    assert pattern.fullmatch(written)


def test_the_pattern_matches_whatever_the_clock_said():
    """A page published last month has a different stamp and the same shape."""
    pattern = STUDIO.pattern()
    older = datetime(2026, 3, 1, 19, 5, tzinfo=ZoneInfo("Asia/Bangkok"))

    written = "\n".join(line.replace("*", "") for line in STUDIO.render(older))
    assert pattern is not None
    assert pattern.fullmatch(written)


def test_no_footer_configured_means_no_pattern():
    assert Footer.from_config({}).pattern() is None


def test_prep_strips_the_disclosure_out_of_a_section():
    """The footer is about the tool, not the lesson. Left in, it becomes
    "context" that the next summary is written against."""
    from pathlib import Path

    from baton.adapters.docs.base import Block
    from baton.core.config import Config
    from baton.domain.prep import SectionRules

    config = Config(
        data={
            "summary": {
                "footer": {
                    "lines": list(STUDIO.lines),
                    "date_format": STUDIO.date_format,
                    "era": STUDIO.era,
                    "months": list(STUDIO.months),
                }
            }
        },
        config_file=Path("baton.yaml"),
        profile_dir=Path("."),
    )
    rules = SectionRules.from_config(config)

    blocks = [
        Block(id="h", type="heading_2", text="ภาพรวมการเรียน"),
        Block(id="b", type="paragraph", text="เล่นได้ตลอดเพลงแล้ว"),
        *[
            Block(id=f"f{index}", type="paragraph", text=line.replace("*", ""))
            for index, line in enumerate(STUDIO.render(MOMENT))
        ],
    ]

    overview = rules.read(blocks).get("overview", "")
    assert "เล่นได้ตลอดเพลงแล้ว" in overview
    assert "หางยาว" not in overview
    assert "chunnylab" not in overview
