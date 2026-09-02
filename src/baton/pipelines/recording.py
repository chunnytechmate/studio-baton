"""Offering a learner's recorded works, and sending the chosen links.

A studio records its learners: YouTube for sharing, Drive beside it for the
copy parents keep. Both live on the ``works`` row, and a parent asking "วีดีโอ
ครั้งก่อนหน่อยครับ" means one specific recording, not whatever came out last.
So sending is two steps and never guesses: list what exists, let a person pick
one by number, then deliver exactly that one's links.

The first step ends with :class:`~baton.errors.NeedsHumanError` because "which
recording?" genuinely is a person's decision: the database orders by date,
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
    list a person reads is byte-for-byte the one ``--pick N`` indexes: one
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
#: One missing is ordinary: some sessions were filmed once; that side simply
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
        GateError: When neither home holds a link: a message announcing a
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
            remedy="Record the links on the work: `baton learner add-work "
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
        # nothing saying which lesson it came from. Fail-open by design: the
        # old sender attached it when it could and sent without it when it
        # could not, and a link that cannot be found must not block the links
        # that exist.
        body += f"\n\n📝 รายละเอียดการเรียน: {doc_url}"
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
            Empty omits the line: it is never a gate.
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


#: The heading a work's recording is filed under on a session page, the same
#: words the studio's old push wrote: a family looking for the clip finds it
#: under the same heading it has always been under.
_RECORDS_HEADING = "🎬 ผลงาน Record"


def recording_blocks(work: Work) -> list[dict[str, Any]]:
    """The section one work's recording becomes on a session page.

    The same shape the old push wrote: the heading, a bold title, the YouTube
    side as a video block and the Drive side as a bookmark. Only the sides the
    row actually has appear.
    """
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": _RECORDS_HEADING}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "📌 "}},
                    {
                        "type": "text",
                        "text": {"content": work.title},
                        "annotations": {"bold": True},
                    },
                ]
            },
        },
    ]
    if work.video_link:
        blocks.append(
            {
                "object": "block",
                "type": "video",
                "video": {"type": "external", "external": {"url": work.video_link}},
            }
        )
    if work.drive_link:
        blocks.append({"object": "block", "type": "bookmark", "bookmark": {"url": work.drive_link}})
    return blocks


def _url_of(block: dict[str, Any]) -> str:
    """The URL a link block carries, whatever shape it was built in."""
    body = block.get(block.get("type", ""), {})
    if isinstance(body, dict) and "external" in body:
        return str(body["external"].get("url", ""))
    return str(body.get("url", "")) if isinstance(body, dict) else ""


def attach_work(docs: Any, doc_id: str, work: Work) -> dict[str, Any]:
    """Put a recorded work onto a session page, without duplicating it.

    The old script cleared every video and bookmark on the page before
    writing. That rule dates from when it was the only writer; today the
    video pipeline puts the lesson's own recording on the same page, and
    clearing all video blocks would take that off with it. The guard is the
    URL instead: a link already on the page is not written twice, and
    anything else on the page is not Baton's to remove.

    Returns what was appended, and the sides that were already there.
    """
    if not work.video_link and not work.drive_link:
        raise GateError(
            f"The work “{work.title}” has no link to put on the page.",
            missing=[{"field": name, "reason": f"`{name}` is empty"} for name, _ in _LINK_LABELS],
            remedy="Record the link on the work first: `baton learner add-work` "
            "writes a new one; the existing row can be edited where it lives.",
        )

    on_page = {block.url for block in docs.list_blocks(doc_id) if block.url}

    # The link blocks carry the URLs; the heading and the title do not, so
    # they are added as a pair only when at least one link is going on. A
    # section heading with no link under it would be a label pointing at
    # nothing, and re-adding the pair when both links are already there is
    # the duplication this exists to prevent.
    wanted = recording_blocks(work)
    heading, title, *links = wanted
    fresh_links = [block for block in links if _url_of(block) not in on_page]
    already = [_url_of(block) for block in links if _url_of(block) in on_page]
    if not fresh_links:
        return {"doc_id": doc_id, "appended": 0, "already_on_page": already}

    blocks = [heading, title, *fresh_links]
    docs.append_blocks(doc_id, blocks)
    return {"doc_id": doc_id, "appended": len(blocks), "already_on_page": already}
