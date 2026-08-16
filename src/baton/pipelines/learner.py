"""Reading a learner's history across both stores.

The database knows which sessions exist and which document each one has. The
document knows whether that session happened. Neither knows both, and the
original system's worst bugs came from guessing rather than joining them.

Two rules are enforced here, and they are the reason this module exists rather
than each command doing its own lookup:

**The latest session is the newest *done* one — never the highest number.**
Sessions get skipped: a learner is ill, a week is cancelled, a page is created
in advance. Session 12 existing says nothing about whether session 12 happened,
so "latest" means status *done*, ordered by the date on the document.

**The next free session must be both unstarted and empty.** A page marked "not
started" that already has blocks on it is somebody's work in progress, and
writing a summary onto it would overwrite that.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..adapters.db.base import LearnerStore
from ..adapters.docs.base import DocStatus, DocStore
from ..domain.models import Learner, Session
from ..domain.status import DONE, IN_PROGRESS, NOT_STARTED, UNKNOWN, StatusVocabulary
from ..errors import BatonError, UpstreamError


@dataclass(frozen=True)
class SessionView:
    """One session joined with the state of its document."""

    session: Session
    doc: DocStatus
    state: str  # canonical: done | in_progress | not_started | ""
    unreadable: str = ""
    """Why this session's document could not be read, if it could not.

    An unreadable session is reported, never guessed at. Its state stays
    unknown, which the rules below already treat as neither done nor free.
    """

    @property
    def number(self) -> int:
        return self.session.number

    @property
    def is_empty(self) -> bool:
        """Whether the document has no content on it yet."""
        return self.doc.block_count == 0

    def to_dict(self, vocabulary: StatusVocabulary) -> dict[str, Any]:
        payload = {
            "number": self.session.number,
            "doc_id": self.session.doc_id,
            "state": self.state,
            "status": self.doc.status or vocabulary.label(self.state),
            "date": self.doc.date,
            "titles": self.doc.titles,
            "block_count": self.doc.block_count,
            "url": self.doc.url,
        }
        if self.unreadable:
            payload["unreadable"] = self.unreadable
        return payload


class LearnerHistory:
    """Joins the two stores for one profile.

    Document reads are fetched concurrently because a learner with twenty
    sessions would otherwise mean twenty serial round trips. The pool is small
    on purpose: document APIs rate-limit, and the retry layer handles the 429s
    that a wider pool would cause more of.
    """

    def __init__(
        self,
        store: LearnerStore,
        docs: DocStore,
        vocabulary: StatusVocabulary,
        *,
        max_parallel_reads: int = 4,
    ) -> None:
        self.store = store
        self.docs = docs
        self.vocabulary = vocabulary
        self.max_parallel_reads = max(1, int(max_parallel_reads))

    # -- joining -----------------------------------------------------------

    def _view(self, session: Session) -> SessionView:
        if not session.doc_id:
            # A session row with no document yet: real, but nothing to read.
            return SessionView(session=session, doc=DocStatus(doc_id=""), state=NOT_STARTED)
        try:
            doc = self.docs.get_status(session.doc_id)
        except BatonError as exc:
            # One unreadable document must not make a learner's whole history
            # unusable. A single malformed page id — a truncated one, a page
            # deleted in the app — would otherwise take eleven good sessions
            # down with it. The state stays unknown, which `latest_done` and
            # `next_empty` already refuse to act on, and the reason is carried
            # so it is reported rather than silently swallowed.
            return SessionView(
                session=session,
                doc=DocStatus(doc_id=session.doc_id),
                state=UNKNOWN,
                unreadable=exc.message,
            )
        return SessionView(session=session, doc=doc, state=self.vocabulary.canonical(doc.status))

    def sessions(self, learner: Learner) -> list[SessionView]:
        """Every session for a learner, joined with its document, by number.

        Raises:
            UpstreamError: Every document failed to read. One failure is a bad
                row and is degraded; all of them is an outage, and reporting
                that as "no free session" would be a confident wrong answer at
                exactly the wrong moment.
        """
        rows = self.store.list_sessions(learner.id)
        if not rows:
            return []

        if len(rows) == 1:
            views = [self._view(rows[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(self.max_parallel_reads, len(rows))) as pool:
                views = list(pool.map(self._view, rows))

        with_documents = [view for view in views if view.session.doc_id]
        if with_documents and all(view.unreadable for view in with_documents):
            raise UpstreamError(
                f"None of {learner.name}'s {len(with_documents)} session documents "
                f"could be read: {with_documents[0].unreadable}",
                service="docs",
                remedy="This looks like an outage rather than bad data. Check the "
                "document store, then re-run.",
            )

        return sorted(views, key=lambda view: view.number)

    # -- the two rules -----------------------------------------------------

    def latest_done(self, views: list[SessionView]) -> SessionView | None:
        """The most recent session that actually happened.

        Ordered by the document's date, with the session number breaking ties
        — a studio that leaves the date blank still gets a sensible answer, and
        one that backfills dates gets the right one even when the numbers are
        out of order.
        """
        done = [view for view in views if view.state == DONE]
        if not done:
            return None
        return max(done, key=lambda view: (view.doc.date or "", view.number))

    def next_empty(self, views: list[SessionView]) -> SessionView | None:
        """The lowest-numbered session that is unstarted *and* has no content.

        Both conditions matter. A "not started" page carrying blocks is work
        someone has already begun; handing it back as free would overwrite it.
        """
        candidates = [view for view in views if view.state == NOT_STARTED and view.is_empty]
        return min(candidates, key=lambda view: view.number) if candidates else None

    def in_progress(self, views: list[SessionView]) -> list[SessionView]:
        """Sessions currently marked in progress, by number."""
        return [view for view in views if view.state == IN_PROGRESS]

    # -- across everyone ---------------------------------------------------

    def everyone_in_progress(self) -> list[tuple[Learner, SessionView]]:
        """Every learner with a session in progress, by learner name.

        Reads every learner's sessions, which is the most expensive call Baton
        makes. It is also the one a teacher runs each morning, so it is worth
        the round trips rather than the answer being approximate.
        """
        found: list[tuple[Learner, SessionView]] = []
        for learner in self.store.list_learners():
            for view in self.in_progress(self.sessions(learner)):
                found.append((learner, view))
        return found

    # -- summaries ---------------------------------------------------------

    def summarise(self, learner: Learner, views: list[SessionView]) -> dict[str, Any]:
        """Everything a caller usually wants about one learner, in one shape."""
        latest = self.latest_done(views)
        upcoming = self.next_empty(views)
        active = self.in_progress(views)

        piece = None
        if learner.current_piece_id:
            found = self.store.get_piece(learner.current_piece_id)
            piece = found.to_dict() if found else None

        return {
            "learner": learner.to_dict(),
            "current_piece": piece,
            "sessions": {
                "total": len(views),
                "done": sum(1 for view in views if view.state == DONE),
                "unreadable": [
                    {"number": view.number, "doc_id": view.session.doc_id, "why": view.unreadable}
                    for view in views
                    if view.unreadable
                ],
                "in_progress": [view.to_dict(self.vocabulary) for view in active],
                "latest_done": latest.to_dict(self.vocabulary) if latest else None,
                "next_empty": upcoming.to_dict(self.vocabulary) if upcoming else None,
            },
        }
