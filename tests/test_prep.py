"""`baton prep` and the sectioned reading its gate stands on.

Real SQLite, a stubbed document store, a stubbed calendar — reaching Notion
or Google in CI is not a test of anything Baton controls. The page blocks are
the studio's own template shape, headings and all, because the reading is by
headings and a page of plain paragraphs would prove nothing about it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.cal.base import CalendarEvent
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeCalendar, FakeDocStore
from baton.cli.app import run
from baton.domain.prep import DEFAULT_FOOTER, DEFAULT_SECTIONS, SectionRules, missing_fields
from baton.exits import Exit

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

RULES = SectionRules(
    keywords=dict(DEFAULT_SECTIONS),
    homework_types=frozenset({"to_do"}),
    footer=re.compile(DEFAULT_FOOTER, re.DOTALL),
    max_chars=400,
)

#: A summarised page in the studio's template shape.
SECTIONED = [
    Block(id="h0", type="heading_2", text="📌 ภาพรวมการเรียน (Overview)"),
    Block(id="p0", type="paragraph", text="ก้าวหน้าดี จับจังหวะได้ดีขึ้นชัดเจน"),
    Block(id="h1", type="heading_2", text="🥁 เนื้อหาและเทคนิคที่เรียน"),
    Block(id="p1", type="paragraph", text="Paradiddle ช้าๆ คู่กับ metronome"),
    Block(id="h2", type="heading_2", text="🛠 จุดที่ต้องโฟกัส (Focus Areas)"),
    Block(id="p2", type="paragraph", text="ข้อมือขวายังตึงเมื่อเร่งจังหวะ"),
    Block(id="h3", type="heading_3", text="🎯 เป้าหมายการซ้อม (Practice Goals)"),
    Block(id="p3", type="paragraph", text="ซ้อม 15 นาทีต่อวัน"),
    Block(id="h4", type="heading_3", text="📌 ครั้งถัดไป"),
    Block(id="p4", type="paragraph", text="เริ่มเพลงใหม่"),
    Block(id="t1", type="to_do", text="ซ้อม grid 80 bpm", raw={"to_do": {"checked": False}}),
    Block(id="t2", type="to_do", text="อ่านโน้ตบทเพลง", raw={"to_do": {"checked": True}}),
    Block(id="f1", type="paragraph", text="หางยาว (3 นาที) สรุปนี้มาจากผู้ช่วย AI"),
]

#: The same page without anything homework-shaped: no checklist, no practice
#: goals — the page a teacher half-wrote before closing the laptop.
_HOMEWORK_SHAPED = {"t1", "t2", "h3", "p3", "f1"}
NO_HOMEWORK = [block for block in SECTIONED if block.id not in _HOMEWORK_SHAPED]


# -- reading a page -----------------------------------------------------------


def test_blocks_land_under_their_heading():
    sections = RULES.read(SECTIONED)
    assert sections["overview"] == "ก้าวหน้าดี จับจังหวะได้ดีขึ้นชัดเจน"
    assert "Paradiddle" in sections["content"]
    assert "ข้อมือขวา" in sections["focus"]
    assert sections["next_goal"] == "เริ่มเพลงใหม่"


def test_a_checklist_is_homework_wherever_it_sits():
    sections = RULES.read(SECTIONED)
    assert "[ ] ซ้อม grid 80 bpm" in sections["homework"]
    assert "[x] อ่านโน้ตบทเพลง" in sections["homework"]


def test_the_credit_line_is_never_part_of_a_section():
    sections = RULES.read(SECTIONED)
    assert "หางยาว" not in sections["next_goal"]
    assert "ผู้ช่วย AI" not in "".join(sections.values())


def test_a_section_caps_at_the_configured_length():
    page = [
        Block(id="h1", type="heading_2", text="🥁 เนื้อหาและเทคนิคที่เรียน"),
        Block(id="p1", type="paragraph", text="x" * 1000),
    ]
    assert len(RULES.read(page)["content"]) == 400


def test_every_configured_section_appears_even_when_empty():
    sections = RULES.read(
        [Block(id="p", type="paragraph", text="ก่อน heading แรก ไม่เข้า section ไหน")]
    )
    assert set(sections) == set(DEFAULT_SECTIONS)
    assert not any(sections.values())


def test_practice_goals_read_back_as_homework_when_there_is_no_checklist():
    sections = RULES.read(
        [
            Block(id="h3", type="heading_3", text="🎯 เป้าหมายการซ้อม (Practice Goals)"),
            Block(id="p3", type="paragraph", text="ซ้อม 15 นาทีต่อวัน"),
        ]
    )
    assert sections["homework"] == ""
    assert missing_fields({"homework": sections["practice_goals"]}, ["homework"]) == []


# -- the report ---------------------------------------------------------------


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
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
              aliases:
                ada: Ada Whitfield
            docs:
              driver: notion
              statuses:
                done: Complete
                in_progress: In progress
                not_started: Not started
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    # Ada's latest done page is fully summarised; Bruno's has no homework.
    fake = FakeDocStore(
        statuses={
            "doc-ada-01": DocStatus(doc_id="doc-ada-01", status="Complete", date="2026-05-01"),
            "doc-ada-02": DocStatus(
                doc_id="doc-ada-02",
                status="Complete",
                date="2026-06-01",
                titles="Paradiddle Basics",
            ),
            "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="Not started"),
            "doc-bruno-01": DocStatus(
                doc_id="doc-bruno-01", status="Complete", date="2026-07-01", titles="Scales"
            ),
            "doc-bruno-02": DocStatus(doc_id="doc-bruno-02", status="Not started"),
        },
        blocks={"doc-ada-02": SECTIONED, "doc-bruno-01": NO_HOMEWORK},
    )
    monkeypatch.setattr("baton.cli.cmd_learner.open_docs", lambda _config: fake)
    monkeypatch.setattr("baton.cli.cmd_calendar.open_docs", lambda _config: fake)

    calendar = FakeCalendar()
    monkeypatch.setattr("baton.cli.cmd_calendar.open_calendar", lambda _config: calendar)
    return profile, fake, calendar


def call(studio, *args):
    profile, _, _ = studio
    return run(["--profile", str(profile), "--json", *args])


def _booked(calendar, *titles: str, day: str = "2026-08-22") -> None:
    for index, title in enumerate(titles):
        calendar.create(
            CalendarEvent(
                id="",
                title=title,
                start=f"{day}T1{index}:00:00+00:00",
                end=f"{day}T1{index}:30:00+00:00",
            )
        )


def test_latest_carries_the_page_as_sections(studio, capsys):
    assert call(studio, "learner", "latest", "Ada Whitfield") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    sections = payload["sections"]
    assert "Paradiddle" in sections["content"]
    assert "[ ] ซ้อม grid 80 bpm" in sections["homework"]
    assert sections["next_goal"] == "เริ่มเพลงใหม่"
    assert "ผู้ช่วย AI" not in json.dumps(payload)


def test_prep_reports_a_fully_summarised_learner(studio, capsys):
    _, _, calendar = studio
    _booked(calendar, "Ada Whitfield (week 3)")

    assert call(studio, "prep", "--date", "2026-08-22") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    entry = payload["ready"][0]
    assert entry["learner"] == "Ada Whitfield"
    assert entry["week"] == 2
    assert entry["date"] == "2026-06-01"
    assert entry["notion_link"] == "https://notion.so/doc-ada-02"
    assert "[ ] ซ้อม grid 80 bpm" in entry["homework"]
    assert payload["count"] == 1


def test_prep_blocks_the_learner_whose_page_is_incomplete(studio, capsys):
    _, _, calendar = studio
    _booked(calendar, "Ada Whitfield (week 3)", "Bruno Castell (week 1)")

    assert call(studio, "prep", "--date", "2026-08-22") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert [entry["learner"] for entry in payload["ready"]] == ["Ada Whitfield"]
    assert payload["blocked"] == [{"learner": "Bruno Castell", "missing": ["homework"]}]


def test_prep_exits_five_when_nobody_passes(studio, capsys):
    _, _, calendar = studio
    _booked(calendar, "Bruno Castell (week 1)")

    assert call(studio, "prep", "--date", "2026-08-22") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] == []
    assert payload["blocked"][0]["missing"] == ["homework"]


def test_prep_names_an_event_that_matches_no_learner(studio, capsys):
    _, _, calendar = studio
    _booked(calendar, "Ada Whitfield (week 3)", "ทดสอบระบบ")

    assert call(studio, "prep", "--date", "2026-08-22") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert [item["title"] for item in payload["unmatched_events"]] == ["ทดสอบระบบ"]


def test_prep_with_no_bookings_refuses_rather_than_reporting_nothing(studio, capsys):
    assert call(studio, "prep", "--date", "2026-08-22") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)
    assert payload["details"]["missing"][0]["field"] == "learners"


def test_explicit_learners_skip_the_calendar(studio, capsys):
    assert call(studio, "prep", "--learner", "ada") == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"][0]["learner"] == "Ada Whitfield"


def test_a_missing_next_goal_warns_without_blocking(studio, capsys):
    _, fake, _ = studio
    fake.blocks["doc-ada-02"] = [
        block for block in SECTIONED if block.text not in ("📌 ครั้งถัดไป", "เริ่มเพลงใหม่")
    ]

    assert call(studio, "prep", "--learner", "Ada Whitfield") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["count"] == 1
    assert payload["ready"][0]["warnings"] == ["next_goal"]


def test_practice_goals_standing_in_for_homework_pass_the_gate(studio, capsys):
    _, fake, _ = studio
    fake.blocks["doc-bruno-01"] = [
        Block(id="h0", type="heading_2", text="📌 ภาพรวมการเรียน (Overview)"),
        Block(id="p0", type="paragraph", text="พื้นฐานแน่นขึ้น"),
        Block(id="h1", type="heading_2", text="🥁 เนื้อหาและเทคนิคที่เรียน"),
        Block(id="p1", type="paragraph", text="C major scale"),
        Block(id="h3", type="heading_3", text="🎯 เป้าหมายการซ้อม (Practice Goals)"),
        Block(id="p3", type="paragraph", text="ซ้อม scale ทุกวัน"),
    ]

    assert call(studio, "prep", "--learner", "Bruno Castell") == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"][0]["homework"] == "ซ้อม scale ทุกวัน"


def test_the_report_names_the_page_it_came_from(studio, capsys):
    _, _, calendar = studio
    _booked(calendar, "Ada Whitfield (week 3)")
    profile, _, _ = studio

    human_run = run(["--profile", str(profile), "prep", "--date", "2026-08-22"])
    assert human_run == Exit.OK
    human = capsys.readouterr().out

    assert "Lesson prep — 2026-08-22  (1 of 1 ready)" in human
    assert "Ada Whitfield  (latest week 2 | 2026-06-01)" in human
    assert "titles: Paradiddle Basics" in human
    assert "page: https://notion.so/doc-ada-02" in human
    assert "homework: [ ] ซ้อม grid 80 bpm" in human


def test_a_learner_with_no_finished_session_is_blocked_not_guessed(studio, capsys):
    assert call(studio, "prep", "--learner", "Clara Nguyen") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocked"] == [{"learner": "Clara Nguyen", "missing": ["latest_done"]}]


def test_a_blocked_report_still_says_ok_false(studio, capsys):
    """Every other command's JSON carries `ok`. `prep` hands its report to the
    failure path, which takes an envelope and was given a body, so the field
    vanished on exactly the path a caller most needs to branch on.

    Found by the first prep parity round against the studio's own data: the
    legacy report says `"ok": false` there and Baton said nothing at all.
    """
    _, _, calendar = studio
    _booked(calendar, "Bruno Castell (week 1)")

    assert call(studio, "prep", "--date", "2026-08-22") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["blocked"][0]["learner"] == "Bruno Castell"
