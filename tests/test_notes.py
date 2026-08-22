"""Markdown conversion and note creation.

This replaces the one skill that had no code — a model was handed the API
shape and asked to build the JSON. Every test here is a line that would have
been dropped, mangled, or split wrongly by hand.
"""

from __future__ import annotations

import json
import textwrap

import pytest

from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.errors import UpstreamError
from baton.exits import Exit
from baton.render import markdown


def kinds(blocks):
    return [block["type"] for block in blocks]


def text_of(block):
    body = block[block["type"]]
    return "".join(run["text"]["content"] for run in body.get("rich_text", []))


# -- conversion --------------------------------------------------------------


def test_headings_become_headings():
    blocks = markdown.to_blocks("# One\n## Two\n### Three")

    assert kinds(blocks) == ["heading_1", "heading_2", "heading_3"]
    assert text_of(blocks[0]) == "One"


@pytest.mark.parametrize("marker", ["-", "*", "+"])
def test_every_bullet_marker_is_recognised(marker):
    blocks = markdown.to_blocks(f"{marker} an item")

    assert kinds(blocks) == ["bulleted_list_item"]
    assert text_of(blocks[0]) == "an item"


@pytest.mark.parametrize("line", ["1. first", "2) second"])
def test_numbered_lists_are_recognised(line):
    assert kinds(markdown.to_blocks(line)) == ["numbered_list_item"]


def test_task_items_carry_their_checked_state():
    blocks = markdown.to_blocks("- [ ] todo\n- [x] done")

    assert kinds(blocks) == ["to_do", "to_do"]
    assert blocks[0]["to_do"]["checked"] is False
    assert blocks[1]["to_do"]["checked"] is True


def test_a_task_item_is_not_mistaken_for_a_bullet():
    """Order matters in the parser: the bullet pattern also matches `- [ ]`."""
    assert kinds(markdown.to_blocks("- [ ] a task")) == ["to_do"]


def test_quotes_and_dividers():
    blocks = markdown.to_blocks("> quoted\n\n---")

    assert kinds(blocks) == ["quote", "divider"]


@pytest.mark.parametrize("rule", ["---", "***", "___", "  ----  "])
def test_divider_forms(rule):
    assert kinds(markdown.to_blocks(rule)) == ["divider"]


def test_a_bullet_is_not_mistaken_for_a_divider():
    assert kinds(markdown.to_blocks("- item")) == ["bulleted_list_item"]


def test_fenced_code_keeps_its_language_and_blank_lines():
    """Inside a fence, blank lines and indentation are the content."""
    note = textwrap.dedent(
        """
        ```python
        def f():

            return 1
        ```
        """
    ).strip()

    blocks = markdown.to_blocks(note)

    assert kinds(blocks) == ["code"]
    assert blocks[0]["code"]["language"] == "python"
    assert text_of(blocks[0]) == "def f():\n\n    return 1"


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("js", "javascript"),
        ("JS", "javascript"),
        ("py", "python"),
        ("ts", "typescript"),
        ("sh", "shell"),
        ("yml", "yaml"),
        ("cpp", "c++"),
        ("csharp", "c#"),
        ("dockerfile", "docker"),
        ("psql", "sql"),
    ],
)
def test_a_short_language_name_becomes_the_one_notion_knows(tag, expected):
    """Notion validates this field against a fixed list and rejects the whole
    request otherwise, so ```js used to cost the entire note."""
    blocks = markdown.to_blocks(f"```{tag}\nx\n```")

    assert blocks[0]["code"]["language"] == expected


def test_a_language_notion_does_not_know_becomes_plain_text():
    """A fence tag is whatever the writer typed. One unrecognised word must
    not be the reason a page fails to publish."""
    blocks = markdown.to_blocks("```wolfram-ish\nx\n```")

    assert blocks[0]["code"]["language"] == "plain text"


def test_a_language_notion_does_know_is_left_alone():
    blocks = markdown.to_blocks("```mermaid\ngraph TD\n```")

    assert blocks[0]["code"]["language"] == "mermaid"


def test_an_unclosed_fence_still_yields_its_content():
    """A typo is not a reason to lose what was written."""
    blocks = markdown.to_blocks("```\nstill here")

    assert kinds(blocks) == ["code"]
    assert text_of(blocks[0]) == "still here"


def test_markdown_inside_a_fence_is_not_interpreted():
    blocks = markdown.to_blocks("```\n# not a heading\n- not a bullet\n```")

    assert kinds(blocks) == ["code"]


def test_anything_unrecognised_becomes_a_paragraph_rather_than_vanishing():
    """A note that silently loses a line is worse than one rendered plainly."""
    blocks = markdown.to_blocks("| a | table |\nplain words")

    assert kinds(blocks) == ["paragraph", "paragraph"]
    assert text_of(blocks[0]) == "| a | table |"


def test_blank_lines_outside_a_fence_are_dropped():
    assert len(markdown.to_blocks("one\n\n\ntwo")) == 2


def test_every_line_produces_exactly_one_block():
    note = "# H\n- a\n- b\n1. c\n> q\nplain"

    assert len(markdown.to_blocks(note)) == 6


def test_a_very_long_line_is_split_into_acceptable_runs():
    """The store rejects a run past its limit; splitting is the only correct
    handling of a long paragraph, which is not an error."""
    blocks = markdown.to_blocks("x" * 5000)

    runs = blocks[0]["paragraph"]["rich_text"]
    assert len(runs) == 3
    assert all(len(run["text"]["content"]) <= 2000 for run in runs)
    assert "".join(run["text"]["content"] for run in runs) == "x" * 5000


def test_empty_input_produces_no_blocks():
    assert markdown.to_blocks("") == []


def test_thai_text_survives_conversion():
    blocks = markdown.to_blocks("# บันทึกวันนี้\n- ซ้อมกลอง")

    assert text_of(blocks[0]) == "บันทึกวันนี้"
    assert text_of(blocks[1]) == "ซ้อมกลอง"


# -- chunking ----------------------------------------------------------------


def test_chunking_respects_the_request_limit():
    blocks = markdown.to_blocks("\n".join(f"- item {n}" for n in range(250)))

    batches = markdown.chunk(blocks)

    assert [len(batch) for batch in batches] == [100, 100, 50]


def test_a_short_note_is_one_batch():
    assert len(markdown.chunk(markdown.to_blocks("one line"))) == 1


# -- creating pages ----------------------------------------------------------


@pytest.fixture
def studio(profile, monkeypatch):
    (profile / "baton.yaml").write_text(
        "version: 1\ntimezone: Asia/Bangkok\nnotes:\n  parent_id_env: BATON_NOTES_PARENT\n",
        encoding="utf-8",
    )
    docs = FakeDocStore()
    monkeypatch.setattr("baton.cli.cmd_notes.open_docs", lambda _config: docs)
    monkeypatch.setenv("BATON_NOTES_PARENT", "parent-1")
    return profile, docs


def call(studio, *args):
    profile, _ = studio
    return run(["--profile", str(profile), "--json", "notes", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_push_creates_a_page_with_the_converted_blocks(studio, capsys):
    _, docs = studio

    assert call(studio, "push", "--title", "Today", "--text", "# H\n- a\n- b") == Exit.OK

    payload = out(capsys)
    assert payload["blocks"] == 3
    assert docs.created_pages[0]["title"] == "Today"
    assert docs.created_pages[0]["parent_id"] == "parent-1"


def test_a_long_note_is_split_across_requests_rather_than_refused(studio, capsys):
    """The store rejects more than 100 children per request. The original
    asked a model to split the payload; here the adapter does it, and every
    block still arrives."""
    _, docs = studio
    note = "\n".join(f"- item {n}" for n in range(250))

    assert call(studio, "push", "--title", "Long", "--text", note) == Exit.OK

    assert out(capsys)["blocks"] == 250
    assert len(docs.list_blocks(docs.created_pages[0]["id"])) == 250
    # Three requests, not one oversized one.
    assert [batch["count"] for batch in docs.appended] == [100, 100, 50]


def test_a_note_under_the_limit_is_a_single_request(studio, capsys):
    _, docs = studio

    call(studio, "push", "--text", "\n".join(f"- {n}" for n in range(30)))

    assert [batch["count"] for batch in docs.appended] == [30]


def test_the_title_defaults_to_the_first_heading(studio, capsys):
    _, docs = studio

    call(studio, "push", "--text", "# Practice notes\nsome text")

    assert docs.created_pages[0]["title"] == "Practice notes"


def test_the_title_falls_back_to_the_date_for_an_untitled_note(studio, capsys):
    """A note with no title is still a note; demanding one turns a quick
    capture into a form."""
    _, docs = studio

    call(studio, "push", "--text", "- just a bullet")

    assert docs.created_pages[0]["title"] == "just a bullet"


def test_dry_run_creates_nothing(studio, capsys):
    _, docs = studio

    assert call(studio, "push", "--text", "# H\n- a", "--dry-run") == Exit.OK

    payload = out(capsys)
    assert payload["dry_run"] is True
    assert payload["blocks"] == 2
    assert docs.created_pages == []


def test_an_empty_note_is_refused(studio, capsys):
    assert call(studio, "push", "--text", "   ") == Exit.USAGE


def test_passing_both_text_and_file_is_refused(studio, capsys, tmp_path):
    path = tmp_path / "n.md"
    path.write_text("x", encoding="utf-8")

    assert call(studio, "push", "--text", "y", "--file", str(path)) == Exit.USAGE


def test_a_missing_parent_names_the_variable_to_set(studio, capsys, monkeypatch):
    monkeypatch.delenv("BATON_NOTES_PARENT", raising=False)

    assert call(studio, "push", "--text", "hello") == Exit.USAGE
    assert "BATON_NOTES_PARENT" in out(capsys)["remedy"]


def test_push_reads_a_file(studio, capsys, tmp_path):
    _, docs = studio
    path = tmp_path / "note.md"
    path.write_text("# From a file\n- one", encoding="utf-8")

    assert call(studio, "push", "--file", str(path)) == Exit.OK
    assert docs.created_pages[0]["title"] == "From a file"


def test_an_upstream_failure_surfaces_as_upstream(studio, capsys):
    _, docs = studio
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert call(studio, "push", "--text", "hello") == Exit.UPSTREAM


def test_preview_converts_without_touching_the_store(studio, capsys):
    _, docs = studio

    assert call(studio, "preview", "--text", "# H\n- a\n- b") == Exit.OK

    payload = out(capsys)
    assert payload["count"] == 3
    assert payload["types"]["bulleted_list_item"] == 2
    assert docs.created_pages == []
