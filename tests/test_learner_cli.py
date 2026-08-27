"""`baton learner` end to end: real SQLite, a stubbed document store.

The database is the real driver against the shipped migration; only the
document store is substituted, because reaching Notion in CI is not a test of
anything Baton controls.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

# Ada has sessions 1-3; Bruno 1-2; Clara 1; Devon 1. Seeded doc ids are
# predictable, so the document states can be scripted precisely.
DOC_STATES = {
    "doc-ada-01": DocStatus(doc_id="doc-ada-01", status="Complete", date="2026-05-01"),
    "doc-ada-02": DocStatus(doc_id="doc-ada-02", status="Complete", date="2026-06-01"),
    "doc-ada-03": DocStatus(doc_id="doc-ada-03", status="Not started"),
    "doc-bruno-01": DocStatus(doc_id="doc-bruno-01", status="In progress"),
    "doc-bruno-02": DocStatus(doc_id="doc-bruno-02", status="Not started"),
    "doc-clara-01": DocStatus(doc_id="doc-clara-01", status="In progress"),
    "doc-devon-01": DocStatus(doc_id="doc-devon-01", status="Not started"),
}


@pytest.fixture
def studio(profile, monkeypatch):
    """A seeded profile whose document store is a scripted fake."""
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
            labels:
              learner: student
              session: lesson
              piece: piece
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

    fake = FakeDocStore(
        statuses=dict(DOC_STATES),
        # Devon's page is "not started" but already has content on it.
        blocks={"doc-devon-01": [Block(id="x", type="paragraph", text="draft")]},
    )
    monkeypatch.setattr("baton.cli.cmd_learner.open_docs", lambda _config: fake)
    return profile, fake


def call(studio, *args):
    profile, _ = studio
    return run(["--profile", str(profile), "--json", "learner", *args])


def test_list_returns_every_learner(studio, capsys):
    assert call(studio, "list") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 4
    assert payload["learners"][0]["name"] == "Ada Whitfield"


def test_latest_reports_the_newest_done_session(studio, capsys):
    assert call(studio, "latest", "Ada Whitfield") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["latest_done"]["number"] == 2
    assert payload["latest_done"]["date"] == "2026-06-01"


def test_latest_is_not_the_highest_number(studio, capsys):
    """Ada's session 3 exists and is unstarted; the answer must still be 2."""
    call(studio, "latest", "Ada Whitfield")

    assert json.loads(capsys.readouterr().out)["latest_done"]["number"] != 3


def test_next_skips_a_page_that_already_has_content(studio, capsys):
    """Devon's only session is unstarted but has a draft on it, so there is
    no free session — reporting one would invite overwriting the draft."""
    assert call(studio, "next", "Devon Marsh") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["next_empty"] is None
    assert payload["highest_number"] == 1


def test_next_finds_a_genuinely_empty_session(studio, capsys):
    assert call(studio, "next", "Ada Whitfield") == Exit.OK

    assert json.loads(capsys.readouterr().out)["next_empty"]["number"] == 3


def test_in_progress_spans_learners(studio, capsys, monkeypatch):
    """The morning check reads the calendar window, then only those learners'
    pages. Bruno's second lesson is on the calendar too, but its page is Not
    started — the page is the truth, so he owes one summary, not two."""
    from datetime import datetime, timedelta

    from baton.adapters.cal.base import CalendarEvent
    from baton.adapters.fakes import FakeCalendar

    _profile, fake = studio
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT17:00:00")
    calendar = FakeCalendar(
        [
            CalendarEvent(
                id="e1", title="Bruno Castell (lesson 1)", start=yesterday, end=yesterday
            ),
            CalendarEvent(id="e2", title="Clara Nguyen (lesson 1)", start=yesterday, end=yesterday),
            CalendarEvent(
                id="e3", title="Bruno Castell (lesson 2)", start=yesterday, end=yesterday
            ),
        ]
    )
    monkeypatch.setattr("baton.cli.cmd_calendar.open_calendar", lambda _config: calendar)
    monkeypatch.setattr("baton.cli.cmd_calendar.open_docs", lambda _config: fake)

    assert call(studio, "in-progress") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    names = [row["learner"]["name"] for row in payload["in_progress"]]
    assert names == ["Bruno Castell", "Clara Nguyen"]


def test_show_joins_both_stores(studio, capsys):
    assert call(studio, "show", "Ada Whitfield") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["current_piece"]["title"] == "Blackbird"
    assert payload["sessions"]["done"] == 2
    assert payload["sessions"]["next_empty"]["number"] == 3


def test_sessions_lists_each_one_with_its_state(studio, capsys):
    assert call(studio, "sessions", "Ada Whitfield") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert [s["number"] for s in payload["sessions"]] == [1, 2, 3]
    assert [s["state"] for s in payload["sessions"]] == ["done", "done", "not_started"]


# -- the resolution gate, reached through the CLI ----------------------------


def test_an_ambiguous_name_exits_needs_human_with_candidates(studio, capsys):
    assert call(studio, "show", "a") == Exit.NEEDS_HUMAN

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "needs_human"
    assert len(payload["details"]["candidates"]) > 1


def test_a_unique_partial_still_refuses_to_resolve(studio, capsys):
    """ "Whitfield" matches one person. It still must not resolve."""
    assert call(studio, "show", "Whitfield") == Exit.NEEDS_HUMAN

    payload = json.loads(capsys.readouterr().out)
    assert [c["name"] for c in payload["details"]["candidates"]] == ["Ada Whitfield"]


def test_a_configured_alias_resolves(studio, capsys):
    assert call(studio, "latest", "ada") == Exit.OK

    assert json.loads(capsys.readouterr().out)["learner"]["name"] == "Ada Whitfield"


def test_an_unknown_name_returns_the_roster(studio, capsys):
    assert call(studio, "show", "Nobody") == Exit.NEEDS_HUMAN

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["details"]["candidates"]) == 4


# -- writes ------------------------------------------------------------------


def test_add_work_records_and_reads_back(studio, capsys):
    assert (
        call(
            studio,
            "add-work",
            "Bruno Castell",
            "--title",
            "Take Five",
            "--type",
            "cover",
            "--date",
            "2026-08-01",
        )
        == Exit.OK
    )
    assert json.loads(capsys.readouterr().out)["work"]["title"] == "Take Five"

    call(studio, "works", "Bruno Castell")
    assert [w["title"] for w in json.loads(capsys.readouterr().out)["works"]] == ["Take Five"]


def test_add_work_dry_run_writes_nothing(studio, capsys):
    call(studio, "add-work", "Bruno Castell", "--title", "Nope", "--dry-run")
    assert json.loads(capsys.readouterr().out)["dry_run"] is True

    call(studio, "works", "Bruno Castell")
    assert json.loads(capsys.readouterr().out)["works"] == []


def test_assign_sets_and_clears_the_current_piece(studio, capsys):
    assert call(studio, "assign", "Devon Marsh", "--piece", "1") == Exit.OK
    assert json.loads(capsys.readouterr().out)["assigned"] == "1"

    call(studio, "show", "Devon Marsh")
    assert json.loads(capsys.readouterr().out)["current_piece"]["title"] == "Autumn Leaves"

    assert call(studio, "assign", "Devon Marsh") == Exit.OK
    capsys.readouterr()
    call(studio, "show", "Devon Marsh")
    assert json.loads(capsys.readouterr().out)["current_piece"] is None


def test_assigning_an_unknown_piece_is_a_usage_error(studio, capsys):
    assert call(studio, "assign", "Devon Marsh", "--piece", "999") == Exit.USAGE

    payload = json.loads(capsys.readouterr().out)
    assert "999" in payload["message"]


def test_assign_dry_run_changes_nothing(studio, capsys):
    call(studio, "assign", "Devon Marsh", "--piece", "1", "--dry-run")
    capsys.readouterr()

    call(studio, "show", "Devon Marsh")
    assert json.loads(capsys.readouterr().out)["current_piece"] is None


def test_pieces_lists_the_catalogue(studio, capsys):
    assert call(studio, "pieces") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 4
    assert payload["pieces"][0]["title"] == "Autumn Leaves"


def test_labels_from_the_profile_reach_human_output(profile, studio, capsys):
    """The profile renames "week" to "lesson"; output must follow."""
    p, _ = studio
    run(["--profile", str(p), "learner", "next", "Ada Whitfield"])

    assert "lesson" in capsys.readouterr().out


def test_subcommand_is_required(studio, capsys):
    p, _ = studio
    assert run(["--profile", str(p), "learner"]) == Exit.USAGE


def test_in_progress_can_show_recording_readiness(studio, capsys, monkeypatch):
    """The teacher's morning column: which unfinished lessons already have
    their recording on the page. The old report answered this by scanning
    every page of every learner; this reads only the window's candidates."""
    from datetime import datetime, timedelta

    from baton.adapters.cal.base import CalendarEvent
    from baton.adapters.fakes import FakeCalendar

    _profile, fake = studio
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT17:00:00")
    calendar = FakeCalendar(
        [
            CalendarEvent(
                id="e1", title="Bruno Castell (lesson 1)", start=yesterday, end=yesterday
            ),
            CalendarEvent(id="e2", title="Clara Nguyen (lesson 1)", start=yesterday, end=yesterday),
        ]
    )
    monkeypatch.setattr("baton.cli.cmd_calendar.open_calendar", lambda _config: calendar)
    monkeypatch.setattr("baton.cli.cmd_calendar.open_docs", lambda _config: fake)

    # Bruno's unfinished lesson already has its recording; Clara's does not.
    fake.blocks["doc-bruno-01"] = [
        Block(id="v", type="video", url="https://youtu.be/bruno-1"),
    ]

    assert call(studio, "in-progress", "--videos") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    by_name = {row["learner"]["name"]: row for row in payload["in_progress"]}
    assert by_name["Bruno Castell"]["video_link"] == "https://youtu.be/bruno-1"
    assert by_name["Clara Nguyen"]["video_link"] == ""


def test_in_progress_without_the_flag_reads_no_blocks(studio, capsys, monkeypatch):
    """The default report stays as cheap as it was: no block reads, no field."""
    from datetime import datetime, timedelta

    from baton.adapters.cal.base import CalendarEvent
    from baton.adapters.fakes import FakeCalendar

    _profile, fake = studio
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT17:00:00")
    calendar = FakeCalendar(
        [CalendarEvent(id="e1", title="Bruno Castell (lesson 1)", start=yesterday, end=yesterday)]
    )
    monkeypatch.setattr("baton.cli.cmd_calendar.open_calendar", lambda _config: calendar)
    monkeypatch.setattr("baton.cli.cmd_calendar.open_docs", lambda _config: fake)

    assert call(studio, "in-progress") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert "video_link" not in payload["in_progress"][0]
    assert fake.blocks.get("doc-bruno-01") is None
