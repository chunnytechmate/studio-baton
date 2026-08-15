"""In-memory adapters for tests.

Shipped inside the package rather than kept in ``tests/`` on purpose: they are
part of the contract. Anyone writing a driver can run their implementation
against the same expectations, and anyone extending Baton can test a pipeline
end to end without a Notion token or a network.

They are deliberately strict — a fake that is more forgiving than the real
thing produces tests that pass while production fails.
"""

from __future__ import annotations

import itertools
from typing import Any

from ..domain.models import Learner, Piece, Session, Work
from ..errors import ConfigError, UpstreamError
from .db.base import LearnerStore
from .docs.base import Block, DocStatus, PreservePolicy


class FakeLearnerStore:
    """A :class:`LearnerStore` held entirely in memory."""

    driver = "fake"

    def __init__(
        self,
        learners: list[Learner] | None = None,
        sessions: list[Session] | None = None,
        pieces: list[Piece] | None = None,
        works: list[Work] | None = None,
    ) -> None:
        self.learners = list(learners or [])
        self.sessions = list(sessions or [])
        self.pieces = list(pieces or [])
        self.works = list(works or [])
        self.closed = False
        #: Set to an exception to make every call raise — for testing the
        #: failover and error paths without an actual outage.
        self.fail_with: Exception | None = None
        self._ids = itertools.count(start=1000)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    # -- learners ----------------------------------------------------------

    def list_learners(self) -> list[Learner]:
        self._check()
        return sorted(self.learners, key=lambda item: item.name)

    def get_learner(self, learner_id: str) -> Learner | None:
        self._check()
        return next((item for item in self.learners if item.id == str(learner_id)), None)

    def set_current_piece(self, learner_id: str, piece_id: str | None) -> None:
        self._check()
        for index, learner in enumerate(self.learners):
            if learner.id == str(learner_id):
                self.learners[index] = Learner(
                    id=learner.id,
                    name=learner.name,
                    instrument=learner.instrument,
                    tone=learner.tone,
                    has_instrument=learner.has_instrument,
                    current_piece_id=piece_id,
                    raw=learner.raw,
                )
                return
        raise ConfigError(f"No learner with id {learner_id}.")

    # -- sessions ----------------------------------------------------------

    def list_sessions(self, learner_id: str) -> list[Session]:
        self._check()
        found = [item for item in self.sessions if item.learner_id == str(learner_id)]
        return sorted(found, key=lambda item: item.number)

    def get_session(self, learner_id: str, number: int) -> Session | None:
        self._check()
        return next(
            (
                item
                for item in self.sessions
                if item.learner_id == str(learner_id) and item.number == int(number)
            ),
            None,
        )

    # -- pieces ------------------------------------------------------------

    def list_pieces(self) -> list[Piece]:
        self._check()
        return sorted(self.pieces, key=lambda item: item.title)

    def get_piece(self, piece_id: str) -> Piece | None:
        self._check()
        return next((item for item in self.pieces if item.id == str(piece_id)), None)

    # -- works -------------------------------------------------------------

    def list_works(self, learner_id: str) -> list[Work]:
        self._check()
        found = [item for item in self.works if item.learner_id == str(learner_id)]
        return sorted(found, key=lambda item: item.performed_date, reverse=True)

    def add_work(self, work: Work) -> Work:
        self._check()
        created = Work(
            id=str(next(self._ids)),
            learner_id=work.learner_id,
            title=work.title,
            type=work.type,
            video_link=work.video_link,
            performed_date=work.performed_date,
        )
        self.works.append(created)
        return created

    # -- lifecycle ---------------------------------------------------------

    def health(self) -> None:
        self._check()

    def close(self) -> None:
        self.closed = True


class FakeDocStore:
    """A :class:`~baton.adapters.docs.base.DocStore` held in memory.

    Enforces the two real constraints that bite in production: appending more
    than 100 children at once is rejected, and deleting an unknown block id
    fails rather than silently succeeding.
    """

    driver = "fake"
    MAX_CHILDREN_PER_REQUEST = 100

    def __init__(
        self,
        statuses: dict[str, DocStatus] | None = None,
        blocks: dict[str, list[Block]] | None = None,
        preserve: PreservePolicy | None = None,
    ) -> None:
        self.statuses = dict(statuses or {})
        self.blocks = {key: list(value) for key, value in (blocks or {}).items()}
        self.preserve = preserve or PreservePolicy(rules=())
        self.appended: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        self._ids = itertools.count(start=1)

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def get_status(self, doc_id: str) -> DocStatus:
        self._check()
        current = self.statuses.get(doc_id, DocStatus(doc_id=doc_id))
        return DocStatus(
            doc_id=current.doc_id or doc_id,
            status=current.status,
            date=current.date,
            titles=current.titles,
            block_count=len(self.blocks.get(doc_id, [])),
            url=current.url,
        )

    def set_status(self, doc_id: str, status: str) -> None:
        self._check()
        current = self.get_status(doc_id)
        self.statuses[doc_id] = DocStatus(
            doc_id=doc_id,
            status=status,
            date=current.date,
            titles=current.titles,
            url=current.url,
        )

    def list_blocks(self, doc_id: str) -> list[Block]:
        self._check()
        return list(self.blocks.get(doc_id, []))

    def append_blocks(self, doc_id: str, blocks: list[dict[str, Any]]) -> None:
        self._check()
        if len(blocks) > self.MAX_CHILDREN_PER_REQUEST:
            raise UpstreamError(
                f"Cannot append {len(blocks)} blocks in one request "
                f"(limit {self.MAX_CHILDREN_PER_REQUEST}).",
                service="fake",
            )
        self.appended.append({"doc_id": doc_id, "count": len(blocks)})
        created = [
            Block(
                id=f"new-{next(self._ids)}",
                type=str(block.get("type", "paragraph")),
                text=_plain_text(block),
            )
            for block in blocks
        ]
        self.blocks.setdefault(doc_id, []).extend(created)

    def delete_blocks(self, block_ids: list[str]) -> int:
        self._check()
        known = {block.id for blocks in self.blocks.values() for block in blocks}
        unknown = [block_id for block_id in block_ids if block_id not in known]
        if unknown:
            raise UpstreamError(
                f"Cannot delete unknown block(s): {', '.join(unknown)}", service="fake"
            )
        removing = set(block_ids)
        for doc_id, blocks in self.blocks.items():
            self.blocks[doc_id] = [block for block in blocks if block.id not in removing]
        return len(removing)

    def health(self) -> None:
        self._check()


def _plain_text(block: dict[str, Any]) -> str:
    """Pull text out of a Notion-shaped block payload."""
    kind = str(block.get("type", ""))
    body = block.get(kind, {}) or {}
    return "".join(part.get("text", {}).get("content", "") for part in body.get("rich_text", []))


__all__ = ["FakeDocStore", "FakeLearnerStore", "LearnerStore"]
