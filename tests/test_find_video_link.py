"""Recognising a recording link wherever the page holds it.

Notion's UI turns a pasted URL into a bookmark, so a recording added by hand
is a bookmark, and a reader that matched `video` blocks alone made such a
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
    """A bookmark can be anything a person saved: the sheet, an article. Only
    the shapes that mean a recording count, and outside `video` blocks only
    video-host URLs do."""
    page = _page(
        Block(id="sheet", type="bookmark", url="https://musescore.com/score/12345"),
    )

    assert find_video_link(page, "doc1") == ""


def test_a_video_block_still_accepts_any_host():
    """A `video` block's URL was chosen as a recording: a Drive-hosted file is
    as much the lesson's recording as a YouTube one."""
    page = _page(Block(id="v", type="video", url="https://drive.google.com/file/x/view"))

    assert find_video_link(page, "doc1") == "https://drive.google.com/file/x/view"


def test_a_video_block_beats_a_bookmark_further_down_the_page():
    """This reverses the earlier "newest link on the page wins" rule, and the
    reversal is the fix.

    The studio keeps the song being learnt on the lesson's own page. Notion
    turns a pasted song URL into a bookmark and an embed, and those sit below
    the `video` block the pipeline wrote, so "newest wins" read the song and
    a parent was sent the link to a record label's music video instead of
    their child's lesson.

    Only the pipeline and a deliberate hand write a `video` block; a bookmark
    is whatever Notion made of a URL somebody dropped on the page.
    """
    page = _page(
        Block(id="recording", type="video", url="https://youtu.be/first"),
        Block(id="song", type="bookmark", url="https://youtu.be/second"),
    )

    assert find_video_link(page, "doc1") == "https://youtu.be/first"


def test_the_newest_wins_among_blocks_of_the_same_standing():
    """Precedence is between shapes, not within one. Two recordings on a page
    still means the later one."""
    videos = _page(
        Block(id="old", type="video", url="https://youtu.be/first"),
        Block(id="new", type="video", url="https://youtu.be/second"),
    )
    bookmarks = _page(
        Block(id="old", type="bookmark", url="https://youtu.be/first"),
        Block(id="new", type="bookmark", url="https://youtu.be/second"),
    )

    assert find_video_link(videos, "doc1") == "https://youtu.be/second"
    assert find_video_link(bookmarks, "doc1") == "https://youtu.be/second"


# -- the piece's own source is never the recording ---------------------------


def test_an_excluded_url_is_not_a_recording_in_any_shape():
    """The song the lesson works on is on YouTube like the recording is, and
    nothing in the URL tells them apart. The caller knows which one is the
    song, so it says so."""
    for shape in ("bookmark", "embed", "video"):
        page = _page(Block(id="song", type=shape, url="https://youtu.be/dQw4w9WgXcQ"))

        assert find_video_link(page, "doc1", exclude=["https://youtu.be/dQw4w9WgXcQ"]) == ""


def test_the_song_is_excluded_however_its_url_is_written():
    """Notion rewrites between `youtu.be/ID`, `watch?v=ID` and `/embed/ID`
    freely, so a string comparison lets the same song back in under another
    spelling."""
    page = _page(Block(id="song", type="embed", url="https://www.youtube.com/embed/dQw4w9WgXcQ"))

    assert find_video_link(page, "doc1", exclude=["https://youtu.be/dQw4w9WgXcQ"]) == ""


def test_a_page_holding_only_the_song_reads_as_having_no_recording():
    """Fail closed. An empty answer here is what makes the send gate refuse a
    message whose recording has not landed yet: the alternative, which
    production actually did, is sending the wrong link to a parent."""
    page = _page(
        Block(id="h", type="heading_2", text="🎵 Die With a Smile"),
        Block(id="bm", type="bookmark", url="https://www.youtube.com/watch?v=aSongVideo1"),
        Block(id="em", type="embed", url="https://www.youtube.com/watch?v=aSongVideo1"),
    )

    excluded = ["https://www.youtube.com/watch?v=aSongVideo1"]
    assert find_video_link(page, "doc1", exclude=excluded) == ""


def test_the_recording_is_still_found_beside_the_song():
    """The whole point: both are on the page, and the right one goes out."""
    page = _page(
        Block(id="recording", type="video", url="https://youtu.be/aLessonRec1"),
        Block(id="bm", type="bookmark", url="https://www.youtube.com/watch?v=aSongVideo2"),
        Block(id="em", type="embed", url="https://www.youtube.com/watch?v=aSongVideo2"),
    )

    excluded = ["https://www.youtube.com/watch?v=aSongVideo2"]
    assert find_video_link(page, "doc1", exclude=excluded) == "https://youtu.be/aLessonRec1"


def test_a_hand_pasted_recording_survives_an_exclusion_list():
    """Excluding the song must not cost the studio the case this reader was
    widened for in the first place: a recording somebody pasted by hand, which
    Notion stored as a bookmark."""
    page = _page(
        Block(id="song", type="embed", url="https://youtu.be/song0000000"),
        Block(id="recording", type="bookmark", url="https://youtu.be/take0000000"),
    )

    assert (
        find_video_link(page, "doc1", exclude=["https://youtu.be/song0000000"])
        == "https://youtu.be/take0000000"
    )


def test_an_empty_exclusion_entry_excludes_nothing():
    """A piece with no source link at all is the ordinary case, and it must
    not blank out every URL-less block comparison."""
    page = _page(Block(id="bm", type="bookmark", url="https://youtu.be/x"))

    assert find_video_link(page, "doc1", exclude=["", ""]) == "https://youtu.be/x"


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
