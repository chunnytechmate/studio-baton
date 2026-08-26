"""Offering a learner's recorded works, and sending the chosen links.

A studio records its learners — YouTube for sharing, Drive beside it for the
copy parents keep. Both live on the ``works`` row, and a parent asking "วีดีโอ
ครั้งก่อนหน่อยครับ" means one specific recording, not whatever came out last.
So sending is two steps and never guesses: list what exists, let a person pick
one by number, then deliver exactly that one's links.

The first step ends with :class:`~baton.errors.NeedsHumanError` because "which
recording?" genuinely is a person's decision — the database orders by date,
but the teacher knows whether the parent wanted Week 3 or the recital cut.
That refusal carries the whole candidate list, so whoever drives Baton can
relay it and answer with ``--pick N`` without anything being remembered.
"""

from __future__ import annotations

from typing import Any

from ..adapters.chat.base import Messenger
from ..domain.models import Work
from ..errors import GateError
from .send import _instrument_icon


def list_candidates(works: list[Work]) -> list[dict[str, Any]]:
    """Recorded works as numbered choices, newest first.

    The already-fetched list is passed in rather than queried here, so the
    list a person reads is byte-for-byte the one ``--pick N`` indexes — one
    read, one order, and no window where the store changes between listing
    and sending. Order stands as :meth:`LearnerStore.list_works` returned it:
    ``performed_date`` descending where the profile maps one, otherwise id
    descending.
    """
    return [
        {
            "n": index,
            "id": work.id,
            "name": work.title,
            "type": work.type,
            "performed_date": work.performed_date,
            "video_link": work.video_link,
            "drive_link": work.drive_link,
        }
        for index, work in enumerate(works, start=1)
    ]


#: The two homes a recording may have, with the label each gets in a message.
#: One missing is ordinary — some sessions were filmed once; that side simply
#: does not appear.
_LINK_LABELS = (
    ("video_link", "📹 YouTube:"),
    ("drive_link", "📁 Drive:"),
)


def compose_recording(
    work: Work,
    *,
    learner_name: str = "",
    instrument: str = "",
    date: str | None = None,
    doc_url: str = "",
) -> str:
    """The message families receive for one recording.

    Deterministic by design, unlike :func:`baton.pipelines.send.compose_message`
    which varies its opening and closing phrases: a links-only message sent a
    second time (say, a parent lost it) reads the same rather than sounding
    freshly cheerful about repeating oneself.

    Raises:
        GateError: When neither home holds a link — a message announcing a
            recording with none in it is worse than no message, so the block
            is fail-closed like every other send gate.
    """
    present = [
        (label, getattr(work, name)) for name, label in _LINK_LABELS if getattr(work, name).strip()
    ]
    if not present:
        raise GateError(
            f"The work “{work.title}” has no recording link to send.",
            missing=[{"field": name, "reason": f"`{name}` is empty"} for name, _ in _LINK_LABELS],
            remedy="Record the links on the work — `baton learner add-work "
            "--video-link/--drive-link` writes a new one; the existing row can "
            "be edited where it lives.",
        )

    icon = _instrument_icon(instrument) if instrument else "🎵"
    header = f"{icon} ผลงานบันทึกการเรียน"
    if learner_name:
        header += f"ของ {learner_name}"

    parts = [work.title]
    if work.type:
        parts.append(f"({work.type})")
    performed = work.performed_date if date is None else date
    if performed:
        parts.append(performed)
    body = "".join(
        [
            f"\n\n📌 {' '.join(parts)}",
            *(f"\n\n{label}\n{value}" for label, value in present),
        ]
    )
    if doc_url:
        # Without this line the message is a dead end: links to the recording,
        # nothing saying which lesson it came from. Fail-open by design — the
        # old sender attached it when it could and sent without it when it
        # could not, and a link that cannot be found must not block the links
        # that exist.
        body += f"\n\n📝 รายละเอียด Notion: {doc_url}"
    return header + body


def send_recording(
    messenger: Messenger,
    *,
    recipient_id: str,
    work: Work,
    learner_name: str,
    instrument: str = "",
    date: str | None = None,
    doc_url: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compose the links message and (unless ``dry_run``) deliver it.

    Args:
        date: The performed date already written the studio's way. ``None``
            uses the work's own value, so a caller with no date configuration
            gets exactly what the record holds.
        doc_url: The lesson page the recording belongs to, appended when given.
            Empty omits the line — it is never a gate.
    """
    message = compose_recording(
        work, learner_name=learner_name, instrument=instrument, date=date, doc_url=doc_url
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
