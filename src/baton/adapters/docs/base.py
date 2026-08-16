"""What a session-document store must do.

A "document" is the page a learner and their family actually read: what was
covered, what to practise, the recording. Baton needs four things from it —
read the status and date, list the blocks, replace the summary without
destroying anything else, and append.

The fourth is the one that matters. :class:`PreservePolicy` encodes the rule
that used to live in prose ("never clear the whole page — the videos and sheet
links are on it"), so a rewrite physically cannot delete an uploaded recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class DocStatus:
    """The state of one session document."""

    doc_id: str
    status: str = ""
    date: str = ""
    titles: str = ""
    block_count: int = 0
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "status": self.status,
            "date": self.date,
            "titles": self.titles,
            "block_count": self.block_count,
            "url": self.url,
        }


@dataclass(frozen=True)
class Block:
    """One block on a document, in the store's own shape.

    ``raw`` is kept whole so a block can be classified and then left alone
    without Baton having to model every block type a studio might use.
    """

    id: str
    type: str
    text: str = ""
    icon: str = ""
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class PreserveRule:
    """One condition under which a block survives a rewrite."""

    type: str
    icon: str = ""
    startswith: str = ""

    def matches(self, block: Block) -> bool:
        """Whether this rule protects ``block``.

        All stated conditions must hold. A rule naming only a type protects
        every block of that type; adding ``icon`` or ``startswith`` narrows it,
        which is how a studio protects its practice-track callouts without also
        freezing every other callout on the page.
        """
        if block.type != self.type:
            return False
        if self.icon and block.icon != self.icon:
            return False
        return not (self.startswith and not block.text.startswith(self.startswith))


@dataclass(frozen=True)
class PreservePolicy:
    """The set of rules deciding what a summary rewrite may remove."""

    rules: tuple[PreserveRule, ...]

    @classmethod
    def from_config(cls, raw: Any) -> PreservePolicy:
        """Build from the ``docs.preserve`` list.

        Raises:
            ConfigError: The list is malformed.
        """
        from ...errors import ConfigError

        if raw is None:
            return cls(rules=())
        if not isinstance(raw, list):
            raise ConfigError(
                "`docs.preserve` must be a list of rules.",
                remedy="Each entry is a mapping with a `type`, optionally an "
                "`icon` or `startswith`.",
            )
        rules = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict) or "type" not in entry:
                raise ConfigError(
                    f"`docs.preserve[{index}]` must be a mapping with a `type`.",
                    remedy="For example: `- {type: video}`.",
                )
            rules.append(
                PreserveRule(
                    type=str(entry["type"]),
                    icon=str(entry.get("icon", "")),
                    startswith=str(entry.get("startswith", "")),
                )
            )
        return cls(rules=tuple(rules))

    def preserves(self, block: Block) -> bool:
        """Whether any rule protects this block."""
        return any(rule.matches(block) for rule in self.rules)

    def partition(self, blocks: list[Block]) -> tuple[list[Block], list[Block]]:
        """Split blocks into ``(preserved, replaceable)``.

        Returns:
            Two lists. Everything not explicitly preserved is replaceable —
            the policy is an allowlist, so a block type nobody thought about
            is treated as summary text rather than silently protected.
        """
        preserved = [b for b in blocks if self.preserves(b)]
        replaceable = [b for b in blocks if not self.preserves(b)]
        return preserved, replaceable


@runtime_checkable
class DocStore(Protocol):
    """Read and update session documents."""

    def get_status(self, doc_id: str) -> DocStatus:
        """Status, date, titles, and block count for one document."""
        ...

    def set_status(self, doc_id: str, status: str) -> None:
        """Set the document's status property to a configured value."""
        ...

    def list_blocks(self, doc_id: str) -> list[Block]:
        """Every top-level block, in order."""
        ...

    def append_blocks(self, doc_id: str, blocks: list[dict[str, Any]]) -> None:
        """Append blocks, chunking to respect the store's per-request limit."""
        ...

    def create_page(self, parent_id: str, title: str, blocks: list[dict[str, Any]]) -> DocStatus:
        """Create a sub-page under ``parent_id`` and return its identity."""
        ...

    def delete_blocks(self, block_ids: list[str]) -> int:
        """Delete blocks by id. Returns how many were removed."""
        ...

    def health(self) -> None:
        """Prove the store is reachable and the credentials work."""
        ...
