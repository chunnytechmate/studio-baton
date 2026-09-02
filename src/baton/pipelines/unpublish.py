"""Taking a published summary back off the page it went to.

Publishing appends and never destroys what the preserve policy protects;
unpublishing is the mirror image and the same discipline applies with the
sign flipped: it removes only what there is evidence Baton wrote, and stops
to ask whenever the evidence runs out. A block a person typed or edited is
never silently deleted.

Three ways to attribute blocks, in order of trust:

* **recorded**: publishes since 0.7.0 store the ids of the blocks they
  appended in the published record. An id that is gone from the page already
  counts as removed; an id whose type or text no longer matches what was
  recorded was edited by a person, and that stops the whole unpublish.
* **legacy**: records written before the block list exists are matched
  against a fresh rendering of the summary the record still holds, using the
  same renderer configuration the publish used. The footer cannot be
  re-rendered (it says when the clock ran), so footer lines are matched by
  the clock-independent pattern instead. A replaceable block that matches
  nothing is ambiguous (edited, or added by hand), and ambiguity stops the
  unpublish rather than being resolved by a guess.
* **whole page**: an explicit ``--whole-page --force`` removes everything.
  It exists because the other two modes deliberately fail safe, and
  "everything goes" is sometimes exactly the recovery a person has in mind
  for a page that went to the wrong recipient. That is why it demands the
  same double opt-in as the other destructive commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..adapters.docs.base import Block, DocStore
from ..render import piece as render_piece
from ..render import summary as render_summary
from .publish import _without_preserved_resource_duplicates

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .publish import SummaryPublisher
    from .staging import PieceSnapshot


@dataclass
class UnpublishPlan:
    """What taking a summary off a page would do, before any of it is done."""

    doc_id: str
    mode: str
    """``recorded``, ``legacy``, or ``whole_page``: how blocks were attributed."""

    delete_blocks: list[dict[str, Any]] = field(default_factory=list)
    """Blocks with evidence they are Baton's; these are removed."""

    keep_blocks: list[dict[str, Any]] = field(default_factory=list)
    """Blocks that stay: preserve-policy protected, or simply not ours."""

    missing: list[dict[str, Any]] = field(default_factory=list)
    """Recorded blocks already absent from the page. Not an error: someone
    removed them first, which is the outcome this command was going to reach."""

    edited: list[dict[str, Any]] = field(default_factory=list)
    """Recorded blocks whose current content differs from what was recorded."""

    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    """Legacy-mode replaceable blocks no rendering accounts for."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "mode": self.mode,
            "would_delete": len(self.delete_blocks),
            "delete_blocks": list(self.delete_blocks),
            "would_keep": len(self.keep_blocks),
            "already_gone": len(self.missing),
            "edited": list(self.edited),
            "ambiguous": list(self.ambiguous),
        }

    @property
    def needs_human(self) -> bool:
        """Whether a person must decide before anything can be removed."""
        return bool(self.edited or self.ambiguous)


def _entry(block: Block) -> dict[str, Any]:
    return {"id": block.id, "type": block.type, "text": block.text}


def plan_whole_page(docs: DocStore, doc_id: str) -> UnpublishPlan:
    """Every block on the page, attributed by the operator's explicit choice."""
    live = docs.list_blocks(doc_id) if doc_id else []
    return UnpublishPlan(doc_id=doc_id, mode="whole_page", delete_blocks=[_entry(b) for b in live])


def plan_recorded(
    docs: DocStore, doc_id: str, recorded: Sequence[Mapping[str, Any]]
) -> UnpublishPlan:
    """Match the page against the block list the publish recorded.

    Everything on the page that no recorded id accounts for is kept: with
    ids in hand there is no need to guess, and a ``--force`` re-publish that
    appended a second copy leaves the earlier copy here rather than deleting
    something no record names. ``--whole-page`` is the mode for that cleanup.
    """
    live: dict[str, Block] = {}
    if doc_id:
        live = {block.id: block for block in docs.list_blocks(doc_id)}

    plan = UnpublishPlan(doc_id=doc_id, mode="recorded")
    for item in recorded:
        block_id = str(item.get("id", ""))
        current = live.get(block_id)
        if current is None:
            plan.missing.append(dict(item))
            continue
        # A ticked to-do still matches: the checkbox lives in the block's raw
        # state, not its text, and ticking a goal is the reader's half of the
        # block Baton wrote, not an edit that rewrites its words.
        if current.type != str(item.get("type", "")) or current.text != str(item.get("text", "")):
            plan.edited.append(_entry(current))
            continue
        plan.delete_blocks.append(_entry(current))
        live.pop(block_id, None)

    plan.keep_blocks = [_entry(block) for block in live.values()]
    return plan


def _matches_payload(block: Block, payloads: list[dict[str, Any]]) -> bool:
    """Whether a stored block is byte-for-byte one of these rendered payloads.

    `render_piece.stored_matches_payload` recognises the piece renderer's own
    narrow shapes; this covers the summary renderer's simpler ones, whose
    payloads are text-bearing blocks of whatever type.
    """
    for payload in payloads:
        if render_piece.stored_matches_payload(block, payload):
            return True
        kind = payload.get("type")
        body = payload.get(kind) if isinstance(kind, str) else None
        text = render_piece._payload_text(body) if isinstance(body, dict) else None
        if text is not None and block.type == kind and block.text.strip() == text:
            return True
    return False


def plan_legacy(
    publisher: SummaryPublisher,
    doc_id: str,
    *,
    summary: dict[str, Any],
    piece_snapshot: PieceSnapshot | None,
    callout_texts: dict[str, str] | None = None,
) -> UnpublishPlan:
    """Attribute blocks by re-rendering the summary the record still holds.

    Rendering is deterministic, so every non-footer block the publish wrote
    reproduces byte-for-byte and matches exactly. The footer cannot (it
    carries the moment of publishing), so its lines match by pattern instead.
    Anything else replaceable is reported as ambiguous for a person to rule
    on; deleting it on the theory that it "must be ours" is the one guess
    this module refuses to make.
    """
    live = publisher.docs.list_blocks(doc_id) if doc_id else []
    preserved, replaceable = publisher.preserve.partition(live)

    # Rendered with the publisher's own configuration but deliberately
    # footer-less: a footer rendered now would carry this moment's clock, and
    # the page carries the moment of publishing. The footer's blocks are
    # matched by pattern instead, which is why the re-render must not produce
    # payload text that would never have been on the page.
    payloads: list[dict[str, Any]] = (
        render_piece.to_blocks(piece_snapshot) if piece_snapshot is not None else []
    )
    payloads += render_summary.to_blocks(
        summary,
        sections=publisher.sections,
        callout_texts=callout_texts,
        callout_icon=publisher.callout_icon,
        footer_lines=None,
    )
    payloads = _without_preserved_resource_duplicates(payloads, preserved)
    footer_patterns = publisher.footer.line_patterns() if publisher.footer else []

    plan = UnpublishPlan(doc_id=doc_id, mode="legacy", keep_blocks=[_entry(b) for b in preserved])
    for block in replaceable:
        matched = _matches_payload(block, payloads) or any(
            pattern.fullmatch(block.text.strip()) for pattern in footer_patterns
        )
        if matched:
            plan.delete_blocks.append(_entry(block))
        else:
            plan.ambiguous.append(_entry(block))
    return plan


def apply_plan(docs: DocStore, plan: UnpublishPlan) -> int:
    """Remove exactly the blocks the plan attributes to the publish.

    Returns:
        How many blocks were removed.
    """
    ids = [str(block["id"]) for block in plan.delete_blocks]
    return docs.delete_blocks(ids) if ids else 0
