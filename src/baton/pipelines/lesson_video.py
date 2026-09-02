"""Sending the latest lesson's video by itself.

A parent asking for the video means the video, not the whole published
summary re-sent with the link at the bottom. The old skill registry kept this
request deliberately separate from both "สรุปการเรียน" (the summary) and
"ผลงาน/record" (a recorded work); the rewrite carried over neither the
distinction nor the command, so the nearest answer re-sent everything.

The message is deterministic, like the recording message and unlike the lesson
message's varied phrasing: a re-send (a parent lost the link) reads the same
as the first send rather than sounding freshly cheerful about repeating
oneself.
"""

from __future__ import annotations

from typing import Any

from ..adapters.chat.base import Messenger
from ..errors import GateError
from .send import _instrument_icon

#: How much of the lesson's summary rides along. The full summary is one tap
#: away on the session page; this is the taste that says which lesson the
#: video belongs to.
_SNIPPET_CHARS = 150

#: Sections that make a useful taste of a lesson, in preference order:
#: overview first, because it is written to be read first.
_SNIPPET_SECTIONS = ("overview", "content", "focus")


def snippet(sections: dict[str, str], *, limit: int = _SNIPPET_CHARS) -> str:
    """A compact taste of one lesson, from its read-back sections.

    Lines are taken whole until the limit and then cut with an ellipsis:
    truncating mid-line would say something the page never said. The first
    section with content wins, in a fixed order, so the same page always
    tastes the same.
    """
    for name in _SNIPPET_SECTIONS:
        text = (sections.get(name) or "").strip()
        if not text:
            continue
        lines: list[str] = []
        used = 0
        for line in text.splitlines():
            if used + len(line) > limit and lines:
                return "\n".join(lines) + "…"
            lines.append(line)
            used += len(line)
        return "\n".join(lines)
    return ""


def compose_video_message(
    *,
    learner_name: str,
    instrument: str,
    session_number: int,
    date: str,
    titles: str,
    video_link: str,
    summary_sections: dict[str, str],
    session_label: str = "week",
) -> str:
    """The message a parent asked for: the video, framed by its lesson.

    Raises:
        GateError: There is no video link. This command *is* the video, so an
            empty one is refused rather than sent around: the same fail-closed
            stance as every other send gate, and a different refusal from
            `send recording`'s, which is about a work with no links.
    """
    if not video_link:
        raise GateError(
            f"{learner_name}'s {session_label} {session_number} has no video on it yet.",
            missing=[
                {
                    "field": "video_link",
                    "reason": "the session document holds no video link",
                    "how_to_fix": "run the video pipeline, or add the link to the page",
                }
            ],
            remedy="Nothing was sent. Put the recording on the session page, then re-run.",
        )

    icon = _instrument_icon(instrument)
    header = f"{icon} วิดีโอบทเรียนของ{learner_name}"
    if instrument:
        header += f" ({instrument})"

    when = (
        f"{session_label} {session_number} ({date})"
        if session_number and date
        else (f"{session_label} {session_number}" if session_number else date)
    )

    lines = [header]
    if when:
        lines.append(when)
    if titles:
        lines.append(f"\n🎵 {titles}")
    taste = snippet(summary_sections)
    if taste:
        lines.append(f"\n{taste}")
    lines.append(f"\n🎬 {video_link}")
    return "\n".join(lines)


def send_video(
    messenger: Messenger,
    *,
    recipient_id: str,
    learner_name: str,
    instrument: str,
    session_number: int,
    date: str,
    titles: str,
    video_link: str,
    summary_sections: dict[str, str],
    session_label: str = "week",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compose the video message and (unless ``dry_run``) deliver it."""
    message = compose_video_message(
        learner_name=learner_name,
        instrument=instrument,
        session_number=session_number,
        date=date,
        titles=titles,
        video_link=video_link,
        summary_sections=summary_sections,
        session_label=session_label,
    )
    if dry_run:
        return {
            "dry_run": True,
            "learner": learner_name,
            "recipient": recipient_id,
            "message": message,
            "sent": False,
        }

    outcome = messenger.send(recipient_id, message)
    return {
        "learner": learner_name,
        "recipient": recipient_id,
        "message": message,
        **outcome.to_dict(),
    }
