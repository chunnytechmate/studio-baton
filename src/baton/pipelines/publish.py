"""Writing a summary onto a session document without destroying what is there.

The original system's standing warning was "never clear the whole page — the
recordings and sheet links are on it". That was advice, and advice is only as
reliable as whoever is following it. Here it is mechanism: the update deletes
only the blocks the profile's preserve policy does not protect, and it appends
before it deletes so a failure halfway leaves the page with too much on it
rather than too little.

Ordering matters and is deliberate:

1. read the existing blocks
2. append the new summary
3. delete the old, replaceable blocks

Appending first costs one duplicated section if step 3 fails. Deleting first
would cost the lesson summary entirely if step 2 failed — and the recordings
would be gone with no way to tell what had been there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adapters.docs.base import Block, DocStore, PreservePolicy
from ..domain.status import DONE
from ..render import summary as render


@dataclass
class PublishResult:
    """What one document update did."""

    doc_id: str
    appended: int
    deleted: int
    preserved: int
    replaced: bool
    doc_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_url": self.doc_url,
            "appended": self.appended,
            "deleted": self.deleted,
            "preserved": self.preserved,
            "replaced": self.replaced,
        }


class SummaryPublisher:
    """Renders a validated summary onto a document."""

    def __init__(
        self,
        docs: DocStore,
        preserve: PreservePolicy,
        *,
        sections: dict[str, Any] | None = None,
        callout_icon: str = "",
    ) -> None:
        self.docs = docs
        self.preserve = preserve
        self.sections = sections
        self.callout_icon = callout_icon

    def plan(self, doc_id: str, summary: dict[str, Any], *, callout_texts=None) -> dict[str, Any]:
        """Work out what publishing would do, without doing any of it.

        Returns:
            Counts and the block ids that would be removed, for ``--dry-run``.
        """
        existing = self.docs.list_blocks(doc_id) if doc_id else []
        preserved, replaceable = self.preserve.partition(existing)
        blocks = render.to_blocks(
            summary,
            sections=self.sections,
            callout_texts=callout_texts,
            callout_icon=self.callout_icon,
        )
        return {
            "doc_id": doc_id,
            "would_append": len(blocks),
            "would_delete": len(replaceable),
            "would_preserve": len(preserved),
            "preserved_types": sorted({block.type for block in preserved}),
            "delete_ids": [block.id for block in replaceable],
        }

    def publish(
        self,
        doc_id: str,
        summary: dict[str, Any],
        *,
        callout_texts: dict[str, str] | None = None,
        replace: bool = True,
    ) -> PublishResult:
        """Render and write the summary.

        Args:
            doc_id: Target document.
            summary: A validated lesson summary.
            callout_texts: Theory id to text, resolved by the caller.
            replace: Remove the previous, replaceable blocks. ``False`` appends
                only, for a document being written for the first time.

        Returns:
            A :class:`PublishResult` describing what changed.
        """
        existing: list[Block] = self.docs.list_blocks(doc_id) if replace else []
        preserved, replaceable = self.preserve.partition(existing)

        blocks = render.to_blocks(
            summary,
            sections=self.sections,
            callout_texts=callout_texts,
            callout_icon=self.callout_icon,
        )

        # Append first: see the module docstring on why this order is not
        # arbitrary.
        self.docs.append_blocks(doc_id, blocks)

        deleted = 0
        if replaceable:
            deleted = self.docs.delete_blocks([block.id for block in replaceable])

        status = self.docs.get_status(doc_id)
        return PublishResult(
            doc_id=doc_id,
            appended=len(blocks),
            deleted=deleted,
            preserved=len(preserved),
            replaced=replace,
            doc_url=status.url,
        )

    def complete(self, doc_id: str, *, date: str = "", titles: str = "") -> dict[str, str]:
        """Mark the session finished on its own document.

        A summary on the page is not the same as a session that is over. The
        status property is what the rest of the system reads: `next` treats a
        fresh in-progress session as the one to write next, `prep` requires a
        finished one, and `send` describes a lesson that has happened. Leaving
        the status alone after publishing means the same session stays the
        target of the next summary, so this write is part of publishing rather
        than something to remember afterwards.

        ``date`` and ``titles`` only fill blanks. The studio's own value —
        typed by hand, or written when the lesson was booked — is the better
        record of when the lesson happened and what was played, so it is never
        overwritten by what can be inferred at publish time.

        Returns:
            The properties actually written, keyed as in ``docs.properties``.
        """
        current = self.docs.get_status(doc_id)
        values = {"status": DONE}
        if date and not current.date:
            values["date"] = date
        if titles and not current.titles:
            values["titles"] = titles
        written = self.docs.set_properties(doc_id, values)
        return {key: values[key] for key in written if key in values}
