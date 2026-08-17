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

**The next free session is where a new lesson may land.** A not-started page
with no content is free. A page in progress is the lesson that is happening
now — the studio's own flow books a lesson, the page turns "In progress", and
the summary is written onto *that* page — so a fresh one is the target, exactly
as the system this replaces always answered. Only a page still in progress
after its day has passed by more than ``next_stale_days`` is treated as
abandoned: one missed lesson must not hold every later week hostage. A
"not started" page that already has blocks on it is somebody's work in
progress, and is never handed back as free.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
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
        next_stale_days: int | None = 1,
    ) -> None:
        self.store = store
        self.docs = docs
        self.vocabulary = vocabulary
        self.max_parallel_reads = max(1, int(max_parallel_reads))
        # None means a page in progress is never abandoned — the legacy
        # system's behaviour.
        self.next_stale_days = None if next_stale_days is None else max(0, int(next_stale_days))

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

    def next_empty(
        self, views: list[SessionView], *, today: date | None = None
    ) -> SessionView | None:
        """The lowest-numbered session a new lesson may land on.

        A not-started page with no content is free. A page in progress is the
        target while it is fresh: the studio's flow books a lesson, the page
        turns "In progress", and the summary is written onto that page —
        skipping it would file the lesson against the wrong week. Only a page
        still in progress more than ``next_stale_days`` past its date is
        treated as abandoned and passed over, so one missed lesson cannot
        hold every later week hostage. ``next_stale_days: null`` never
        abandons a page — the legacy system's behaviour.

        A "not started" page carrying blocks is work someone has already
        begun; handing it back as free would overwrite it. Unreadable pages
        (unknown state) are never guessed at.
        """
        reference = today or date.today()
        cutoff = (
            None
            if self.next_stale_days is None
            else (reference - timedelta(days=self.next_stale_days)).isoformat()
        )
        for view in sorted(views, key=lambda view: view.number):
            if view.state == IN_PROGRESS:
                when = (view.doc.date or "")[:10]
                # A page with no date cannot be proven stale; it stays the
                # target rather than being quietly skipped.
                if cutoff is None or not when or when >= cutoff:
                    return view
                continue
            if view.state == NOT_STARTED and view.is_empty:
                return view
        return None

    def in_progress(self, views: list[SessionView]) -> list[SessionView]:
        """Sessions currently marked in progress, by number."""
        return [view for view in views if view.state == IN_PROGRESS]

    # -- across everyone ---------------------------------------------------
    # The morning question — "who still owes a summary?" — is answered from
    # the calendar window by Scheduler.in_progress, which reads one document
    # per candidate instead of every session page of every learner. Scanning
    # every page here was the most expensive call Baton made.

    # -- summaries ---------------------------------------------------------

    def summarise(
        self, learner: Learner, views: list[SessionView], *, today: date | None = None
    ) -> dict[str, Any]:
        """Everything a caller usually wants about one learner, in one shape."""
        latest = self.latest_done(views)
        upcoming = self.next_empty(views, today=today)
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
