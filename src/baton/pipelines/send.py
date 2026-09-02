"""Assembling a lesson message and refusing to send an incomplete one.

The fail-closed gate is the most valuable thing the original system had, and it
is preserved exactly: **a missing required field blocks the send, and there is
no override flag.** The fix is to supply the data. An override would mean a
message reaching a parent with no link to the lesson, which is worse than no
message, because the parent believes they were sent something.

One field has a way past the block, and it goes through a person rather than a
flag on the gate: when a session has no recording link, `send lesson` stops on
exit 3 and asks, and only a person's confirmed `--without-video` sends it:
with no video section in the message. The pipeline here plays no part in that
decision; the CLI applies the confirmation as a required list with `video_link`
moved to optional, which is the same shape as a studio configuring its own
standard. Every other required field keeps the hard, unoverridable block.

What counts as required is configuration, not code, so a studio decides its own
standard of completeness. The block is not negotiable.

Every field the gate checks is gathered here, from Baton's own records, so the
caller cannot assemble a plausible-looking context by hand. What is sent is
what was published: the message is the one stored at publish time, and the
link is the document's real URL.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..adapters.chat.base import Messenger
from ..adapters.db.base import LearnerStore
from ..domain.models import Learner
from ..errors import GateError
from .staging import PieceSnapshot

#: The studio's own phrasing, carried over verbatim from the message the
#: original `push.py` sent: parents already know this voice.
OPENING_PHRASES = (
    "สรุปการเรียนของ",
    "สรุปเนื้อหาการเรียนของ",
    "สรุปการเรียนดนตรีของ",
    "บันทึกการเรียนของ",
)
CLOSING_PHRASES = (
    "พบกันใหม่ครั้งหน้านะครับ🥳",
    "เก่งมาก ไว้พบกันใหม่ครั้งหน้านะครับ🥳",
    "แล้วพบกันใหม่ครั้งหน้านะครับ 🥳",
    "ยอดเยี่ยม! ไว้พบกันใหม่ครั้งหน้านะครับ🥳",
)
#: Substring match against the learner's instrument, first hit wins: same
#: rule the original used, so "กลองและกีตาร์" still gets both icons.
INSTRUMENT_ICONS = (
    ("กลอง", "กีตาร์", "🥁🎸"),
    ("กีตาร์", None, "🎸"),
    ("กลอง", None, "🥁"),
    ("เปียโน", None, "🎹"),
    ("พิคโคโล", None, "🎹"),
    ("ร้อง", None, "🎤"),
    ("แซกโซโฟน", None, "🎷"),
    ("แซโซ", None, "🎷"),
    ("ไวโอลีน", None, "🎻"),
    ("ไวโอลิน", None, "🎻"),
)
DEFAULT_INSTRUMENT_ICON = "🎸"


def _instrument_icon(instrument: str) -> str:
    for first, second, icon in INSTRUMENT_ICONS:
        if first in instrument and (second is None or second in instrument):
            return icon
    return DEFAULT_INSTRUMENT_ICON


#: Human phrasing for each field the gate knows about, used in the message a
#: person reads when a send is blocked.
FIELD_HINTS = {
    "doc_link": "the link to the session document (set at publish time)",
    "short_summary": "the parent-facing message (written at publish time)",
    "session_number": "the session number (from the session record)",
    "video_link": "the recording link (add a video block to the document)",
    "practice_track": "the practice track (assign the learner a piece with one)",
}


@dataclass
class SendContext:
    """Everything one message needs, gathered from Baton's own records."""

    learner_name: str
    session_number: int
    short_message: str
    doc_url: str
    doc_id: str
    video_link: str = ""
    practice_track: str = ""
    instrument: str = ""
    #: The document's own date and titles: these live on the document, not
    #: the database (see `Session`), so the caller reads them from there.
    date: str = ""
    titles: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def fields(self) -> dict[str, str]:
        """The gate's view of this context: field name -> present value."""
        return {
            "doc_link": self.doc_url,
            "short_summary": self.short_message,
            "session_number": str(self.session_number) if self.session_number else "",
            "video_link": self.video_link,
            "practice_track": self.practice_track,
        }


def _practice_track(
    store: LearnerStore, learner: Learner | None, published: Mapping[str, Any]
) -> str:
    """The practice track as it stood for the lesson being sent.

    Reading the learner's *current* piece here is the drift issue #29
    describes: a summary published on Monday and sent on Friday, after the
    learner moved on, would carry Friday's track under Monday's lesson. The
    piece is snapshotted when the lesson is staged, so the record being sent
    already knows which one was taught.

    Only a record written before snapshots existed (`unavailable`) falls back
    to the live lookup, because for those there is nothing better to consult,
    and a stale track is still closer to the truth than no track at all. A
    snapshot of `none` is not missing information: it says the learner had no
    piece assigned when the lesson was staged, so nothing is sent.
    """
    snapshot = PieceSnapshot.from_record(published)
    if snapshot.status == "captured":
        return snapshot.piece.practice_track if snapshot.piece else ""
    if snapshot.status == "none":
        return ""

    if learner is not None and learner.current_piece_id:
        piece = store.get_piece(learner.current_piece_id)
        return piece.practice_track if piece else ""
    return ""


def gather_context(
    store: LearnerStore,
    learner_id: str,
    published: dict[str, Any],
    *,
    video_link: str = "",
    date: str = "",
    titles: str = "",
) -> SendContext:
    """Build the context from a published record.

    The video link, date, and titles are not stored at publish time: they
    live on the document itself and can change afterwards (a recording lands
    later; a date gets corrected), so all three are supplied by the caller,
    which reads them from the document.

    The practice track goes the other way: it belongs to the lesson that was
    taught, so it comes from the snapshot taken when that lesson was staged.
    """
    learner = store.get_learner(learner_id)
    practice_track = _practice_track(store, learner, published)

    return SendContext(
        learner_name=str(published.get("learner_name", learner.name if learner else "")),
        session_number=int(published.get("session_number", 0) or 0),
        short_message=str(published.get("short_message", "")),
        doc_url=str(published.get("doc_url", "")),
        doc_id=str(published.get("doc_id", "")),
        video_link=video_link,
        practice_track=practice_track,
        instrument=learner.instrument if learner is not None else "",
        date=date,
        titles=titles,
    )


def evaluate(
    context: SendContext, *, required: list[str], optional: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The gate's verdict as data: every gap, each paired with how to fix it.

    This is the one place the gate's logic lives. ``gate_check`` turns the
    verdict into a refusal; `baton send readiness` turns the same verdict
    into a report, so what the report names as missing is exactly what the
    send would refuse on, never a second opinion that could drift.
    """
    fields = context.fields()

    missing = [
        {
            "field": name,
            "reason": f"`{name}` is empty",
            "how_to_fix": FIELD_HINTS.get(name, f"supply a value for `{name}`"),
        }
        for name in required
        if not fields.get(name, "").strip()
    ]

    warnings = [
        {
            "field": name,
            "reason": f"`{name}` is empty (optional)",
        }
        for name in optional
        if not fields.get(name, "").strip()
    ]

    return missing, warnings


def gate_check(
    context: SendContext, *, required: list[str], optional: list[str]
) -> tuple[list, list]:
    """Evaluate the send gate and refuse the send when it fails.

    Args:
        context: The gathered context.
        required: Fields that must be present. Any gap blocks the send.
        optional: Fields that only warrant a warning when absent.

    Returns:
        ``(missing, warnings)``. An empty ``missing`` means the send may go.

    Raises:
        GateError: With every gap, each paired with how to supply it. Raised
            here rather than returned so the exit code is bound to the verdict
            at the only place the verdict is made.
    """
    missing, warnings = evaluate(context, required=required, optional=optional)

    if missing:
        names = ", ".join(item["field"] for item in missing)
        raise GateError(
            f"Refusing to send the lesson message for {context.learner_name}: missing {names}.",
            missing=missing,
            remedy="Nothing was sent. Supply the missing items, then re-run. "
            "There is no flag to bypass this check.",
        )

    return missing, warnings


def compose_message(context: SendContext) -> str:
    """Build the final message the studio's families already know the voice of.

    The summary was composed at publish time; everything around it (the
    opening line, the instrument icon, the document and recording links) is
    added here from Baton's own records. A model never writes these; that is
    why links are forbidden inside the summary itself, where they would
    either duplicate these or be dead.

    Format matches the studio's original `push.py` byte-for-byte where the
    same fields are available, chosen over Baton's own plainer format once a
    side-by-side comparison showed parents would notice the difference.
    """
    icon = _instrument_icon(context.instrument)
    opening = random.choice(OPENING_PHRASES)  # noqa: S311 - phrase variety, not a security decision
    closing = random.choice(CLOSING_PHRASES)  # noqa: S311 - phrase variety, not a security decision
    instrument_part = f" ({context.instrument})" if context.instrument else ""
    date_part = f" - {context.date}" if context.date else ""

    lines = [f"{icon} {opening}{context.learner_name}{instrument_part}{date_part}"]

    if context.titles:
        lines.append(f"\n🎵 {context.titles}")
    if context.short_message:
        lines.append(f"\n📝 {context.short_message}")

    week = context.session_number if context.session_number else "ล่าสุด"
    lines.append(f"\n📌 Week {week} รายละเอียดการเรียนและวีดีโอ:\n{context.doc_url}")

    if context.practice_track:
        lines.append(f"\n🎧 Track สำหรับซ้อม:\n{context.practice_track}")
    if context.video_link:
        lines.append(f"\nเฉพาะ Video: {context.video_link}")

    lines.append(f"\n{closing}")
    return "\n".join(lines)


def send_lesson(
    messenger: Messenger,
    store: LearnerStore,
    *,
    recipient_id: str,
    learner_id: str,
    published: dict[str, Any],
    video_link: str = "",
    date: str = "",
    titles: str = "",
    required: list[str] | None = None,
    optional: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Gate, compose, and (unless ``dry_run``) deliver one lesson message.

    Returns:
        A result dict: the gate verdict, the composed message, and the send
        outcome when one was attempted.
    """
    required = ["doc_link", "short_summary", "session_number"] if required is None else required
    optional = ["practice_track", "video_link"] if optional is None else optional

    context = gather_context(
        store, learner_id, published, video_link=video_link, date=date, titles=titles
    )
    _, warnings = gate_check(context, required=required, optional=optional)
    message = compose_message(context)

    if dry_run:
        return {
            "dry_run": True,
            "learner": context.learner_name,
            "session_number": context.session_number,
            "recipient": recipient_id,
            "message": message,
            "warnings": warnings,
            "sent": False,
        }

    outcome = messenger.send(recipient_id, message)
    return {
        "learner": context.learner_name,
        "session_number": context.session_number,
        "recipient": recipient_id,
        "message": message,
        "warnings": warnings,
        **outcome.to_dict(),
    }
