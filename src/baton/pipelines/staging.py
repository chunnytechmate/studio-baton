"""Lesson drafts on their way to being published.

A lesson is staged, then summarised, then published — often across separate
commands, sometimes across separate days, and occasionally across a crash. Each
draft is one atomically-written file under ``<state>/lessons/``.

Publishing records what succeeded per target. Re-running skips a target that is
already done, which is what makes ``publish`` safe to retry: appending the same
summary twice leaves a document with two copies of it, and nothing in Notion
will tell you which is which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import jsonio
from ..errors import UsageError

#: Draft lifecycle. `summarised` means a validated summary is attached.
STAGED = "staged"
SUMMARISED = "summarised"
PUBLISHED = "published"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    """A filesystem-safe key for a learner id.

    Ids come from a studio's own database and may be anything at all, so they
    are never used as a path component unescaped.
    """
    cleaned = _SAFE_NAME.sub("_", str(value)).strip("._") or "unknown"
    return cleaned[:100]


@dataclass
class LessonDraft:
    """One lesson being prepared."""

    learner_id: str
    learner_name: str
    session_number: int
    doc_id: str = ""
    titles: str = ""
    context: str = ""
    """Raw notes the teacher gave: what the model summarises from."""
    previous_context: str = ""
    """The previous session's summary, for scope rather than for copying."""
    summary: dict[str, Any] | None = None
    """The validated structure. ``None`` until `ingest` accepts one."""
    status: str = STAGED
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Per-target publish state: ``{"docs": {"status": "ok", "at": ...}}``."""

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "learner_id": self.learner_id,
            "learner_name": self.learner_name,
            "session_number": self.session_number,
            "doc_id": self.doc_id,
            "titles": self.titles,
            "context": self.context,
            "previous_context": self.previous_context,
            "summary": self.summary,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "targets": self.targets,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LessonDraft:
        return cls(
            learner_id=str(data.get("learner_id", "")),
            learner_name=str(data.get("learner_name", "")),
            session_number=int(data.get("session_number", 0)),
            doc_id=str(data.get("doc_id", "")),
            titles=str(data.get("titles", "")),
            context=str(data.get("context", "")),
            previous_context=str(data.get("previous_context", "")),
            summary=data.get("summary"),
            status=str(data.get("status", STAGED)),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            targets=dict(data.get("targets", {})),
        )

    # -- summaries ---------------------------------------------------------

    def summary_view(self) -> dict[str, Any]:
        """A compact description for listings."""
        return {
            "learner_id": self.learner_id,
            "learner_name": self.learner_name,
            "session_number": self.session_number,
            "status": self.status,
            "has_summary": self.summary is not None,
            "targets": {name: state.get("status") for name, state in self.targets.items()},
            "updated_at": self.updated_at,
        }

    # -- publish state -----------------------------------------------------

    def target_done(self, name: str) -> bool:
        """Whether a target already succeeded and must not be repeated."""
        return self.targets.get(name, {}).get("status") == "ok"

    def record_target(self, name: str, status: str, **extra: Any) -> None:
        """Record the outcome of publishing to one target."""
        state = dict(self.targets.get(name, {}))
        state["status"] = status
        state["at"] = _now()
        state["attempts"] = int(state.get("attempts", 0)) + 1
        state.update(extra)
        self.targets[name] = state
        self.updated_at = _now()


class StagingStore:
    """Drafts on disk, one file per learner."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, learner_id: str) -> Path:
        return self.root / f"{_slug(learner_id)}.json"

    # -- reads -------------------------------------------------------------

    def get(self, learner_id: str) -> LessonDraft | None:
        data = jsonio.read_json(self._path(learner_id), None)
        return LessonDraft.from_dict(data) if isinstance(data, dict) else None

    def require(self, learner_id: str, learner_name: str) -> LessonDraft:
        """Fetch a draft, or explain how to create one.

        Raises:
            UsageError: No draft is staged for this learner.
        """
        draft = self.get(learner_id)
        if draft is None:
            raise UsageError(
                f"No lesson is staged for {learner_name}.",
                remedy=f'Run `baton lesson stage "{learner_name}"` first.',
            )
        return draft

    def list(self) -> list[LessonDraft]:
        """Every draft, oldest first."""
        if not self.root.is_dir():
            return []
        drafts = []
        for path in sorted(self.root.glob("*.json")):
            data = jsonio.read_json(path, None)
            if isinstance(data, dict):
                drafts.append(LessonDraft.from_dict(data))
        return sorted(drafts, key=lambda draft: draft.created_at)

    # -- writes ------------------------------------------------------------

    def save(self, draft: LessonDraft) -> None:
        draft.updated_at = _now()
        jsonio.write_json(self._path(draft.learner_id), draft.to_dict())

    def remove(self, learner_id: str) -> bool:
        """Delete one draft. Returns whether there was anything to delete."""
        path = self._path(learner_id)
        existed = path.exists()
        for candidate in (path, jsonio.backup_path(path)):
            candidate.unlink(missing_ok=True)
        return existed

    def clear(self) -> int:
        """Delete every draft. Returns how many were removed."""
        removed = 0
        for draft in self.list():
            if self.remove(draft.learner_id):
                removed += 1
        return removed


class PublishedRecord:
    """What was published, kept after the draft is cleared.

    The message a parent receives is composed at publish time, and the command
    that sends it runs later — often much later. Keeping the rendered message
    here means the send step never has to re-derive it, and so can never
    produce a different message from the one that was reviewed.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, learner_id: str, session_number: int) -> Path:
        return self.root / f"{_slug(learner_id)}-{int(session_number)}.json"

    def save(self, draft: LessonDraft, *, short_message: str, doc_url: str = "") -> None:
        jsonio.write_json(
            self._path(draft.learner_id, draft.session_number),
            {
                "learner_id": draft.learner_id,
                "learner_name": draft.learner_name,
                "session_number": draft.session_number,
                "doc_id": draft.doc_id,
                "doc_url": doc_url,
                "titles": draft.titles,
                "short_message": short_message,
                "published_at": _now(),
            },
        )

    def get(self, learner_id: str, session_number: int) -> dict[str, Any] | None:
        data = jsonio.read_json(self._path(learner_id, session_number), None)
        return data if isinstance(data, dict) else None

    def latest(self, learner_id: str) -> dict[str, Any] | None:
        """The most recently published session for a learner."""
        if not self.root.is_dir():
            return None
        prefix = f"{_slug(learner_id)}-"
        records = []
        for path in self.root.glob(f"{prefix}*.json"):
            data = jsonio.read_json(path, None)
            if isinstance(data, dict):
                records.append(data)
        if not records:
            return None
        return max(records, key=lambda item: (str(item.get("published_at", "")),))
