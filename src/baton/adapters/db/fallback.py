"""A secondary store used only when the primary is unreachable.

Carried over from the original ``students_client.py``, with its most important
property made explicit rather than incidental: **reads fall over, writes never
do.**

A read served from a stale replica is a slightly out-of-date answer. A write
sent to a replica is a permanent divergence between two stores that nothing
reconciles — the studio ends up with two different truths about who is learning
what. So a write during an outage fails loudly and the operator waits.

Only genuine unreachability triggers a failover. A ``ConfigError`` (bad
credentials, missing column) is raised straight through: retrying that against
a second store just produces the same error twice and hides the real cause.
"""

from __future__ import annotations

from typing import Any

from ...domain.models import Learner, Piece, Session, Work
from ...errors import UpstreamError
from .base import LearnerStore


class FallbackStore:
    """Wraps a primary store and consults a secondary when reads fail."""

    driver = "fallback"

    def __init__(self, primary: LearnerStore, secondary: LearnerStore) -> None:
        self.primary = primary
        self.secondary = secondary
        #: Set once a read has been served by the secondary, so callers (and
        #: `doctor`) can say the data may be stale rather than implying it is
        #: current.
        self.degraded = False

    def _read(self, method: str, *args: Any) -> Any:
        try:
            return getattr(self.primary, method)(*args)
        except UpstreamError:
            result = getattr(self.secondary, method)(*args)
            self.degraded = True
            return result

    def _write(self, method: str, *args: Any) -> Any:
        # No try/except on purpose: a failed write must surface, not divert.
        return getattr(self.primary, method)(*args)

    # -- reads -------------------------------------------------------------

    def list_learners(self) -> list[Learner]:
        return self._read("list_learners")

    def get_learner(self, learner_id: str) -> Learner | None:
        return self._read("get_learner", learner_id)

    def list_sessions(self, learner_id: str) -> list[Session]:
        return self._read("list_sessions", learner_id)

    def get_session(self, learner_id: str, number: int) -> Session | None:
        return self._read("get_session", learner_id, number)

    def list_pieces(self) -> list[Piece]:
        return self._read("list_pieces")

    def get_piece(self, piece_id: str) -> Piece | None:
        return self._read("get_piece", piece_id)

    def list_works(self, learner_id: str) -> list[Work]:
        return self._read("list_works", learner_id)

    # -- writes ------------------------------------------------------------

    def set_current_piece(self, learner_id: str, piece_id: str | None) -> None:
        self._write("set_current_piece", learner_id, piece_id)

    def add_work(self, work: Work) -> Work:
        return self._write("add_work", work)

    # -- lifecycle ---------------------------------------------------------

    def health(self) -> None:
        """Check the primary only.

        A green health report while the primary is down would defeat the point:
        the operator needs to know the outage is happening, not be reassured
        that something answered.
        """
        self.primary.health()

    def close(self) -> None:
        self.primary.close()
        self.secondary.close()
