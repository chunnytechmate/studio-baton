"""`send video`: the video on its own, not the summary again.

A parent asking for the video meant the video. The old skill registry kept
this request separate from both the summary and the recorded works; the
rewrite carried over neither the distinction nor the command, and the nearest
Baton answer re-sent the whole published summary with the link at the bottom.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

import baton
from baton.adapters.chat.base import SendOutcome
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit
from baton.pipelines.lesson_video import compose_video_message, snippet

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

SECTIONS = {
    "overview": "จับจังหวะได้ตลอดทั้งเพลงแล้ว และเปลี่ยนคอร์ดได้ทันขึ้น",
    "content": "เนื้อหา",
    "focus": "",
}


class FakeMessenger:
    driver = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def resolve(self, name: str) -> str:
        return {"teacher": "U-teacher", "me": "U-teacher"}[name]

    def send(self, recipient_id: str, text: str) -> SendOutcome:
        self.sent.append((recipient_id, text))
        return SendOutcome(sent=True, recipient=recipient_id)


@pytest.fixture
def studio(profile, monkeypatch):
    """A published session whose page carries a video and an overview."""
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.execute("INSERT INTO learners (id, name) VALUES ('9', 'Ben NoWork')")
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
            chat:
              driver: line
              contacts:
                teacher:
                  id_env: BATON_TEACHER
                  aliases: [me]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    docs = FakeDocStore(
        statuses={
            "doc-ada-03": DocStatus(
                doc_id="doc-ada-03", status="Complete", date="2026-08-23", titles="Blackbird"
            )
        },
        blocks={
            "doc-ada-03": [
                Block(id="h", type="heading_2", text="Overview"),
                Block(id="o", type="paragraph", text=SECTIONS["overview"]),
                Block(id="v", type="video", url="https://youtu.be/dQw4w9WgXcQ"),
            ]
        },
    )
    messenger = FakeMessenger()
    monkeypatch.setattr("baton.cli.cmd_send.open_docs", lambda _config: docs)
    monkeypatch.setattr("baton.cli.cmd_send.open_chat", lambda _config: messenger)
    monkeypatch.setenv("BATON_TEACHER", "U-teacher")

    record = {
        "learner_id": "1",
        "learner_name": "Ada Whitfield",
        "session_number": 3,
        "doc_id": "doc-ada-03",
        "doc_url": "https://example.invalid/lesson-3",
        "titles": "Blackbird",
        "short_message": "• …",
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = profile / "state" / "published" / "1-3.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    return profile, messenger, docs


def call(studio_fixture, *args):
    return run(["--profile", str(studio_fixture[0]), "--json", "send", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


# -- composing ---------------------------------------------------------------


def test_the_video_leads_and_the_summary_rides_along():
    message = compose_video_message(
        learner_name="น้องปิติ",
        instrument="กลอง",
        session_number=12,
        date="23 ส.ค. 2569",
        titles="ขุนแผนมนต์พระกาฬ",
        video_link="https://youtu.be/CSLzpVffEoI",
        summary_sections=SECTIONS,
    )

    lines = message.splitlines()
    assert lines[0] == "🥁 วีดีโอบทเรียนของน้องปิติ (กลอง)"
    assert lines[1] == "week 12 (23 ส.ค. 2569)"
    assert "🎵 ขุนแผนมนต์พระกาฬ" in message
    assert SECTIONS["overview"] in message
    assert message.rstrip().endswith("🎬 https://youtu.be/CSLzpVffEoI")
    # The video is the point: it sits after the framing, never buried mid-message.
    assert message.index("🎬") > message.index("week 12")


def test_no_video_is_refused_not_sent_empty():
    """This command *is* the video, so an empty one is a gate, and a
    different refusal from `send recording`'s, which is about a work."""
    import pytest

    from baton.errors import GateError

    with pytest.raises(GateError, match="has no video on it yet"):
        compose_video_message(
            learner_name="น้องปิติ",
            instrument="กลอง",
            session_number=12,
            date="",
            titles="",
            video_link="",
            summary_sections={},
        )


def test_the_snippet_takes_whole_lines_and_cuts_with_an_ellipsis():
    sections = {"overview": "\n".join(f"บรรทัดที่ {n}" for n in range(1, 30))}

    taste = snippet(sections, limit=20)

    assert taste.endswith("…")
    assert "บรรทัดที่ 1" in taste
    # Whole lines only: a cut line would say something the page never said.
    for line in taste.rstrip("…").splitlines():
        assert line.startswith("บรรทัดที่ ")


def test_the_snippet_falls_through_the_sections_in_order():
    assert snippet({"overview": "", "content": "เนื้อหาสัปดาห์นี้", "focus": ""}) == "เนื้อหาสัปดาห์นี้"
    assert snippet({"overview": "", "content": "", "focus": "โฟกัส"}) == "โฟกัส"
    assert snippet({}) == ""


def test_a_short_section_is_not_cut():
    assert snippet({"overview": "สั้นๆ"}, limit=150) == "สั้นๆ"


# -- the command ---------------------------------------------------------------


def test_sends_the_latest_sessions_video(studio, capsys):
    _profile, messenger, _docs = studio

    assert call(studio, "video", "Ada Whitfield", "--to", "teacher") == Exit.OK

    payload = out(capsys)
    assert payload["sent"] is True
    text = messenger.sent[0][1]
    assert "วีดีโอบทเรียนของAda Whitfield" in text
    assert "https://youtu.be/dQw4w9WgXcQ" in text


def test_the_date_arrives_in_the_studios_format(studio, capsys):
    """The same chat.date formatting the lesson message uses: 23 ส.ค. 2569,
    not the document's raw 2026-08-23."""
    profile = studio[0]
    (profile / "baton.yaml").write_text(
        (profile / "baton.yaml").read_text(encoding="utf-8")
        + textwrap.dedent(
            """
            chat:
              date:
                format: "%-d {month} %Y"
                era: buddhist
                months: [ม.ค., ก.พ., มี.ค., เม.ย., พ.ค., มิ.ย., ก.ค., ส.ค., ก.ย., ต.ค., พ.ย., ธ.ค.]
            """
        ),
        encoding="utf-8",
    )

    assert call(studio, "video", "Ada Whitfield", "--to", "teacher") == Exit.OK

    text = studio[1].sent[0][1]
    assert "23 ส.ค. 2569" in text
    assert "2026-08-23" not in text


def test_a_session_without_a_video_blocks_the_send(studio, capsys, monkeypatch):
    """A published session whose page never got its recording: the parent's
    request is refused with the reason, not answered with an empty message."""
    _profile, messenger, docs = studio
    docs.blocks["doc-ada-03"] = [
        Block(id="h", type="heading_2", text="Overview"),
        Block(id="o", type="paragraph", text="no recording this week"),
    ]

    assert call(studio, "video", "Ada Whitfield", "--to", "teacher") == Exit.GATE

    payload = out(capsys)
    assert "no video on it yet" in payload["message"]
    assert messenger.sent == []


def test_an_unpublished_learner_is_a_usage_error(studio, capsys):
    assert call(studio, "video", "Ben NoWork", "--to", "teacher") == Exit.USAGE

    payload = out(capsys)
    assert "Nothing has been published" in payload["message"]


def test_a_bookmarked_video_counts(studio, capsys):
    """The F18 tolerance, seen from the command that needs it most: a link
    added by hand is a bookmark, and it is still this command's video."""
    docs = studio[2]
    docs.blocks["doc-ada-03"] = [
        Block(id="h", type="heading_2", text="Overview"),
        Block(id="o", type="paragraph", text=SECTIONS["overview"]),
        Block(id="bm", type="bookmark", url="https://youtu.be/hand-added"),
    ]

    assert call(studio, "video", "Ada Whitfield", "--to", "teacher") == Exit.OK

    assert "https://youtu.be/hand-added" in studio[1].sent[0][1]


def test_dry_run_composes_without_sending(studio, capsys):
    _profile, messenger, _docs = studio

    assert call(studio, "video", "Ada Whitfield", "--to", "teacher", "--dry-run") == Exit.OK

    payload = out(capsys)
    assert payload["dry_run"] is True
    assert payload["message"]
    assert messenger.sent == []


def test_an_explicit_session_is_honoured(studio, capsys):
    assert call(studio, "video", "Ada Whitfield", "--to", "teacher", "--session", "9") == Exit.USAGE

    payload = out(capsys)
    assert "week 9" in payload["message"]
