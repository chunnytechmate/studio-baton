"""The date on a lesson message, written the way the studio's families read it.

The rewrite carried the document's raw date into the LINE header — "2026-08-23"
where the old sender wrote "23 ส.ค. 2569" — while a docstring nearby claimed
the message matched the old format byte for byte. The rest of the message
really did, which is how one field regressed unnoticed.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from baton.domain.localdate import DateFormat, DateFormatError
from baton.domain.models import Work
from baton.pipelines.recording import compose_recording
from baton.pipelines.send import SendContext, compose_message

THAI = DateFormat(
    format="%-d {month} %Y",
    era="buddhist",
    months=(
        "ม.ค.",
        "ก.พ.",
        "มี.ค.",
        "เม.ย.",
        "พ.ค.",
        "มิ.ย.",
        "ก.ค.",
        "ส.ค.",
        "ก.ย.",
        "ต.ค.",
        "พ.ย.",
        "ธ.ค.",
    ),
)


def test_the_studio_date_is_abbreviated_thai_with_a_buddhist_year():
    assert THAI.of_text("2026-08-23") == "23 ส.ค. 2569"


def test_a_datetime_with_a_time_part_still_formats():
    assert THAI.of_text("2026-08-23T19:00:00+07:00") == "23 ส.ค. 2569"


def test_a_hand_typed_value_passes_through_unchanged():
    """The document's date property is deliberately flexible. A phrase typed
    by a person is left alone rather than blanked or mangled."""
    assert THAI.of_text("สัปดาห์ที่แล้ว") == "สัปดาห์ที่แล้ว"


def test_an_empty_date_stays_empty():
    assert THAI.of_text("") == ""


def test_no_format_configured_passes_the_value_through():
    default = DateFormat()

    assert not default
    assert default.of_text("2026-08-23") == "2026-08-23"


def test_a_month_placeholder_without_month_names_is_refused():
    with pytest.raises(DateFormatError, match="no month names"):
        DateFormat(format="%-d {month}").of_text("2026-08-23")


def test_twelve_months_or_none():
    with pytest.raises(DateFormatError, match="Twelve month names"):
        DateFormat(format="%-d {month}", months=("ม.ค.",))


def test_an_unknown_era_is_refused_at_construction():
    with pytest.raises(DateFormatError, match="Unknown era"):
        DateFormat(era="julian")


def test_the_gregorian_default_does_not_offset_the_year():
    assert DateFormat(format="%Y").render(date(2026, 8, 23)) == "2026"


# -- reaching the messages ---------------------------------------------------


def test_the_lesson_message_carries_the_studio_date():
    """The line families actually read: opening, name, then the date."""
    context = SendContext(
        learner_name="น้องปิติ",
        session_number=12,
        short_message="• ซ้อมได้ครบทั้งเพลง",
        doc_url="https://example.invalid/doc",
        doc_id="doc-1",
        date=THAI.of_text("2026-08-23"),
    )

    header = compose_message(context).splitlines()[0]

    assert "23 ส.ค. 2569" in header
    assert "2026-08-23" not in header


def test_the_recording_message_carries_the_studio_date():
    work = Work(
        id="w1",
        learner_id="1",
        title="Blackbird",
        performed_date="2026-08-23",
        video_link="https://example.invalid/watch",
    )

    message = compose_recording(work, learner_name="น้องชูใจ", date=THAI.of_text(work.performed_date))

    assert "23 ส.ค. 2569" in message
    assert "2026-08-23" not in message


def test_the_recording_message_without_a_date_argument_uses_the_record():
    work = Work(
        id="w1",
        learner_id="1",
        title="Blackbird",
        performed_date="2026-08-23",
        video_link="https://example.invalid/watch",
    )

    assert "2026-08-23" in compose_recording(work)


# -- and the footer uses the same formatter -----------------------------------


def test_the_footer_stamp_and_the_message_date_come_from_one_formatter():
    """One era decision, one set of month names. If the footer says 2569 and
    the message says 2026, parents read two different calendars in one
    sitting."""
    from baton.domain.footer import Footer

    footer = Footer(
        lines=("{date}",),
        date_format="%-d {month} %Y",
        era="buddhist",
        months=THAI.months,
    )

    moment = datetime(2026, 8, 23, 19, 0, tzinfo=ZoneInfo("Asia/Bangkok"))

    assert footer.render(moment) == [THAI.render(moment.date())]
