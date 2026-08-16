"""Assembling a lesson message and refusing to send an incomplete one.

The fail-closed gate is the most valuable thing the original system had, and it
is preserved exactly: **a missing required field blocks the send, and there is
no override flag.** The fix is to supply the data. An override would mean a
message reaching a parent with no link to the lesson — which is worse than no
message, because the parent believes they were sent something.

What counts as required is configuration, not code, so a studio decides its own
standard of completeness. The block is not negotiable.

Every field the gate checks is gathered here, from Baton's own records, so the
caller cannot assemble a plausible-looking context by hand. What is sent is
what was published — the message is the one stored at publish time, and the
link is the document's real URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..adapters.chat.base import Messenger
from ..adapters.db.base import LearnerStore
from ..errors import GateError

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


def gather_context(
    store: LearnerStore,
    learner_id: str,
    published: dict[str, Any],
    *,
    video_link: str = "",
) -> SendContext:
    """Build the context from a published record.

    The video link is the one thing not stored at publish time — recordings
    often land on the document later — so it is supplied by the caller, which
    reads it from the document.
    """
    learner = store.get_learner(learner_id)
    practice_track = ""
    if learner is not None and learner.current_piece_id:
        piece = store.get_piece(learner.current_piece_id)
        practice_track = piece.practice_track if piece else ""

    return SendContext(
        learner_name=str(published.get("learner_name", learner.name if learner else "")),
        session_number=int(published.get("session_number", 0) or 0),
        short_message=str(published.get("short_message", "")),
        doc_url=str(published.get("doc_url", "")),
        doc_id=str(published.get("doc_id", "")),
        video_link=video_link,
        practice_track=practice_track,
    )


def gate_check(
    context: SendContext, *, required: list[str], optional: list[str]
) -> tuple[list, list]:
    """Evaluate the send gate.

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

    if missing:
        names = ", ".join(item["field"] for item in missing)
        raise GateError(
            f"Refusing to send the lesson message for {context.learner_name}: missing {names}.",
            missing=missing,
            remedy="Nothing was sent. Supply the missing items, then re-run. "
            "There is no flag to bypass this check.",
        )

    return missing, warnings


def compose_message(context: SendContext, *, footer_links: bool = True) -> str:
    """Build the final message: the stored summary plus Baton's own links.

    The summary was composed at publish time, so this adds only what Baton can
    vouch for — the document URL and, when present, the recording. A model
    never writes these; that is why links are forbidden inside the summary
    itself, where they would either duplicate these or be dead.
    """
    lines = [context.short_message]
    if footer_links:
        if context.doc_url:
            lines.append(f"Lesson notes: {context.doc_url}")
        if context.video_link:
            lines.append(f"Recording: {context.video_link}")
    return "\n".join(lines)


def send_lesson(
    messenger: Messenger,
    store: LearnerStore,
    *,
    recipient_id: str,
    learner_id: str,
    published: dict[str, Any],
    video_link: str = "",
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

    context = gather_context(store, learner_id, published, video_link=video_link)
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
