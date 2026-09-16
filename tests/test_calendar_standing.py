"""The standing weekly series a schedule sync keeps on a calendar.

Everything runs on the fake calendar and the fake learner store: the real
driver is exercised exactly once, by hand, with a clearly-named zz series
that is deleted in the same session.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from datetime import date
from pathlib import Path

import pytest

import baton
from baton.adapters.fakes import FakeCalendar, FakeLearnerStore
from baton.cli.app import run
from baton.domain.models import Learner, LessonSlot
from baton.errors import UsageError
from baton.exits import Exit
from baton.pipelines.schedule import StandingSync

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"


def learner(id_: str, name: str, *, instrument: str = "กีตาร์", active: bool = True) -> Learner:
    return Learner(id=id_, name=name, instrument=instrument, is_active=active)


@pytest.fixture
def store():
    built = FakeLearnerStore(
        learners=[
            learner("1", "น้องเอ"),
            learner("2", "น้องบี", instrument="กลอง"),
            learner("3", "น้องซี", active=False),
        ],
        slots=[
            LessonSlot(id="s1", learner_id="1", weekday="Monday", start="16:00"),
            LessonSlot(id="s2", learner_id="1", weekday="Thursday", start="17:00"),
            LessonSlot(id="s3", learner_id="2", weekday="Wednesday", start="15:00"),
            # น้องซีเลิกเรียนแล้ว: คาบของเธอต้องไม่ไปโผล่บนปฏิทิน
            LessonSlot(id="s4", learner_id="3", weekday="Friday", start="09:00"),
        ],
    )
    return built


@pytest.fixture
def calendar():
    return FakeCalendar()


@pytest.fixture
def sync(calendar):
    return StandingSync(calendar, timezone="Asia/Bangkok")


def titles(calendar):
    return [event.title for event in calendar.list_standing()]


def test_a_full_sync_creates_one_series_per_active_slot(store, calendar, sync):
    result = sync.sync(store)

    assert result.created == 3
    assert sorted(titles(calendar)) == [
        "น้องบี · คาบประจำ",
        "น้องเอ · คาบประจำ",
        "น้องเอ · คาบประจำ",
    ]
    starts = sorted(event.start for event in calendar.list_standing())
    assert starts[0].startswith("2")  # ISO dates, not times of day alone


def test_series_start_the_next_occurrence_and_today_never_counts(sync):
    # 2026-09-15 เป็นวันอังคาร: คาบวันจันทร์ต้องเริ่มจันทร์ถัดไป (21)
    first = sync._first_date("Monday", today=date(2026, 9, 15))
    assert first == date(2026, 9, 21)
    # คาบวันอังคารก็ไม่เริ่มวันนี้ แม้วันนี้จะเป็นอังคาร
    assert sync._first_date("Tuesday", today=date(2026, 9, 15)) == date(2026, 9, 22)


def test_a_title_never_carries_the_booking_marker_shape(store, sync):
    sync.sync(store)

    for title in titles(sync.calendar):
        assert " (" not in title


def test_inactive_learners_leave_the_calendar_via_a_scoped_sync(store, calendar, sync):
    sync.sync(store)
    inactive = store.get_learner("3")
    assert inactive is not None

    result = sync.sync(store, learner=inactive)

    assert result.deleted == 0  # น้องซีไม่เคยมีซีรีส์ (คาบของ inactive ไม่สร้างตั้งแต่แรก)
    assert result.created == 0
    # ทางกลับ: คนที่ active มีซีรีส์อยู่แล้ว ของเขาไม่โดนแตะ
    assert len(calendar.list_standing()) == 3


def test_a_scoped_sync_replaces_only_that_learner(store, calendar, sync):
    sync.sync(store)
    scoped = store.get_learner("1")
    assert scoped is not None
    store.set_slots("1", [("Monday", "16:00"), ("Monday", "17:00")])

    result = sync.sync(store, learner=scoped)

    assert result.deleted == 2  # จันทร์เก่า + พฤหัสเก่าของน้องเอ
    assert result.created == 2  # จันทร์สองชั่วโมงใหม่
    assert len(calendar.list_standing()) == 3  # รวมของน้องบีที่ไม่โดนแตะ
    others = calendar.list_standing("2")
    assert [event.title for event in others] == ["น้องบี · คาบประจำ"]


def test_running_a_sync_twice_lands_on_the_same_answer(store, calendar, sync):
    first = sync.sync(store)
    second = sync.sync(store)

    assert (first.created, second.created) == (3, 3)
    assert second.deleted == 3  # ชุดเก่าถูกลบก่อนสร้างใหม่ทุกครั้ง


def test_standing_series_stay_out_of_the_booking_world(store, calendar, sync):
    from baton.adapters.cal.base import CalendarEvent

    sync.sync(store)
    calendar.create(
        CalendarEvent(
            id="bk1",
            title="น้องเอ (Week 4)",
            start="2026-09-21T16:00:00+07:00",
            end="2026-09-21T17:00:00+07:00",
        )
    )

    booked = calendar.list_between("2026-09-21T00:00:00+07:00", "2026-09-22T00:00:00+07:00")

    # คาบที่จองจริงอยู่ คาบประจำหายไปจากโลกของการจอง
    assert [event.title for event in booked] == ["น้องเอ (Week 4)"]


def test_the_end_time_follows_the_configured_minutes(store, sync):
    sync.default_minutes = 90
    spec = sync._spec(
        store.get_learner("1"), LessonSlot(id="x", learner_id="1", weekday="Monday", start="16:00")
    )

    assert spec.end == "17:30"


def test_a_slot_that_crosses_midnight_is_refused(store, sync):
    with pytest.raises(UsageError, match=r"crossing midnight"):
        sync._spec(
            store.get_learner("1"),
            LessonSlot(id="x", learner_id="1", weekday="Monday", start="23:30"),
        )


def test_a_dry_run_touches_nothing(store, calendar, sync):
    result = sync.sync(store, dry_run=True)

    assert result.deleted == 0 and result.created == 0
    assert calendar.list_standing() == []
    assert len(result.standing) == 3  # แผนอยู่ใน payload ไม่ใช่บนปฏิทิน


# -- the command, end to end on real SQLite -----------------------------------


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
            docs:
              driver: notion
            calendar:
              driver: google
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    fake = FakeCalendar()
    monkeypatch.setattr("baton.cli.cmd_calendar.open_calendar", lambda _config: fake)
    return profile, fake


def call(studio, *args):
    profile, _ = studio
    return run(["--profile", str(profile), "--json", "calendar", *args])


def payload(capsys):
    return json.loads(capsys.readouterr().out)


def test_the_cli_syncs_and_lists(studio, capsys):
    assert call(studio, "standing-sync") == Exit.OK
    body = payload(capsys)
    # seed: Ada จันทร์ + Bruno พุธสองคาบ + Clara ศุกร์ = 4 ซีรีส์
    assert body["scope"] == "all"
    assert body["created"] == 4 and body["deleted"] == 0
    assert len(body["standing"]) == 4

    assert call(studio, "standing") == Exit.OK
    assert payload(capsys)["count"] == 4


def test_the_cli_syncs_one_learner(studio, capsys):
    assert call(studio, "standing-sync", "--name", "Ada Whitfield") == Exit.OK
    body = payload(capsys)
    assert body["scope"] == "Ada Whitfield"
    assert body["created"] == 1

    capsys.readouterr()
    assert call(studio, "standing") == Exit.OK
    assert payload(capsys)["count"] == 1


def test_the_cli_dry_run_changes_nothing(studio, capsys):
    assert call(studio, "standing-sync", "--dry-run") == Exit.OK
    body = payload(capsys)
    assert body["dry_run"] is True
    assert len(body["standing"]) == 4  # would_create สี่รายการ

    capsys.readouterr()
    assert call(studio, "standing") == Exit.OK
    assert payload(capsys)["count"] == 0  # ยังไม่มีอะไรบนปฏิทิน
