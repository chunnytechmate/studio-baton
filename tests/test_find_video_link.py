"""Recognising a recording link wherever the page holds it.

Notion's UI turns a pasted URL into a bookmark, so a recording added by hand
is a bookmark — and a reader that matched `video` blocks alone made such a
page look like it had no recording. Under the required send gate that page
blocks the whole lesson message while looking, to a person reading it, like
it has its video.
"""

from __future__ import annotations

from baton.adapters.docs.base import Block, find_video_link
from baton.adapters.fakes import FakeDocStore


def _page(*blocks: Block) -> FakeDocStore:
    return FakeDocStore(blocks={"doc1": list(blocks)})


def test_a_bookmarked_recording_is_found():
    page = _page(
        Block(id="h", type="heading_2", text="Overview"),
        Block(id="bm", type="bookmark", url="https://youtu.be/dQw4w9WgXcQ"),
    )

    assert find_video_link(page, "doc1") == "https://youtu.be/dQw4w9WgXcQ"


def test_an_embedded_recording_is_found():
    page = _page(Block(id="e", type="embed", url="https://www.youtube.com/watch?v=abc"))

    assert find_video_link(page, "doc1").startswith("https://www.youtube.com/")


def test_a_bookmark_that_is_not_a_recording_is_not_found():
    """A bookmark can be anything a person saved — the sheet, an article. Only
    the shapes that mean a recording count, and outside `video` blocks only
    video-host URLs do."""
    page = _page(
        Block(id="sheet", type="bookmark", url="https://musescore.com/score/12345"),
    )

    assert find_video_link(page, "doc1") == ""


def test_a_video_block_still_accepts_any_host():
    """A `video` block's URL was chosen as a recording — a Drive-hosted file is
    as much the lesson's recording as a YouTube one."""
    page = _page(Block(id="v", type="video", url="https://drive.google.com/file/x/view"))

    assert find_video_link(page, "doc1") == "https://drive.google.com/file/x/view"


def test_the_newest_link_wins_across_shapes():
    page = _page(
        Block(id="old", type="video", url="https://youtu.be/first"),
        Block(id="new", type="bookmark", url="https://youtu.be/second"),
    )

    assert find_video_link(page, "doc1") == "https://youtu.be/second"


def test_a_page_with_no_links_at_all_reads_empty():
    page = _page(Block(id="p", type="paragraph", text="no recordings yet"))

    assert find_video_link(page, "doc1") == ""


def test_an_empty_doc_id_short_circuits():
    assert find_video_link(_page(), "") == ""


def test_the_shapes_are_configurable():
    page = _page(Block(id="bm", type="bookmark", url="https://youtu.be/x"))

    assert find_video_link(page, "doc1", blocks=("video",)) == ""
    assert find_video_link(page, "doc1", blocks=("video", "bookmark")) == "https://youtu.be/x"


def test_a_document_outage_degrades_to_no_link():
    from baton.errors import UpstreamError

    docs = FakeDocStore(blocks={"doc1": []})
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert find_video_link(docs, "doc1") == ""
