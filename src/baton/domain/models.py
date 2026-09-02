"""The things a teaching studio keeps track of.

Deliberately small. A learner, the numbered sessions they work through, the
pieces they are studying, and the recordings that come out of it: that is the
whole model, and every adapter maps its own storage onto exactly this.

Each model keeps a ``raw`` dict of the source record. Studios have columns
Baton knows nothing about, and dropping them on the floor would make Baton a
lossy layer over the user's own data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Learner:
    """A person being taught."""

    id: str
    name: str
    instrument: str = ""
    tone: str = ""
    has_instrument: bool = False
    current_piece_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "instrument": self.instrument,
            "tone": self.tone,
            "has_instrument": self.has_instrument,
            "current_piece_id": self.current_piece_id,
        }


@dataclass(frozen=True)
class Session:
    """One numbered session, and the document that records it.

    ``status`` and ``date`` are absent here on purpose: they live on the
    document, not in the database, and reading them means asking the
    :class:`~baton.adapters.docs.base.DocStore`. Copying them into the database
    is what let the two disagree in the original system.
    """

    id: str
    learner_id: str
    number: int
    doc_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "learner_id": self.learner_id,
            "number": self.number,
            "doc_id": self.doc_id,
        }


@dataclass(frozen=True)
class Piece:
    """Something being studied: a song, an étude, an exam piece."""

    id: str
    title: str
    source_link: str = ""
    practice_track: str = ""
    sheet_link: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source_link": self.source_link,
            "practice_track": self.practice_track,
            "sheet_link": self.sheet_link,
        }


@dataclass(frozen=True)
class Work:
    """A finished performance or recording worth keeping."""

    id: str
    learner_id: str
    title: str
    type: str = "performance"
    video_link: str = ""
    #: A second home of the same recording: the Drive file beside the YouTube
    #: upload. Studios that keep only one link leave it empty.
    drive_link: str = ""
    performed_date: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "learner_id": self.learner_id,
            "title": self.title,
            "type": self.type,
            "video_link": self.video_link,
            "drive_link": self.drive_link,
            "performed_date": self.performed_date,
        }
