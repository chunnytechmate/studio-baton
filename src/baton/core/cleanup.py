"""The cleanup ledger: clip ids the pipeline owes a real deletion.

Unfiling a clip (removing it from the learner folder because the credential
cannot trash a file it does not own) clears the source, which is what the
run needs. It does not delete anything: the file keeps existing in its
uploader's Drive, and once it has left every folder the pipeline credential
can see, it is unreachable from then on. Production ran that way for a week
before anyone looked, and the only record of *which* clips were only unfiled
was implicit in "the source no longer lists them".

This file is that record. Every ``UNFILED`` trash outcome owes an entry;
``trashed`` and ``gone`` outcomes settle theirs. ``baton video cleanup``
replays the pending entries, ideally with the uploading account's credential
(``media.drive.cleanup_credentials_file``), because Drive lets only the
owner trash.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import jsonio

PENDING = "pending"
CLEARED = "cleared"


@dataclass(frozen=True)
class LedgerEntry:
    """One clip the pipeline unfiled and still owes a deletion."""

    clip_id: str
    learner_folder: str = ""
    learner_name: str = ""
    session_number: int = 0
    status: str = PENDING
    reason: str = ""
    recorded_at: str = ""
    cleared_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "learner_folder": self.learner_folder,
            "learner_name": self.learner_name,
            "session_number": self.session_number,
            "status": self.status,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
            "cleared_at": self.cleared_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        return cls(
            clip_id=str(data.get("clip_id", "")),
            learner_folder=str(data.get("learner_folder", "")),
            learner_name=str(data.get("learner_name", "")),
            session_number=int(data.get("session_number", 0) or 0),
            status=str(data.get("status", PENDING)),
            reason=str(data.get("reason", "")),
            recorded_at=str(data.get("recorded_at", "")),
            cleared_at=str(data.get("cleared_at", "")),
        )


class CleanupLedger:
    """Pending and settled unfiled-clip ids, one JSON file under the state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, LedgerEntry] | None = None

    @classmethod
    def for_state(cls, state_dir: Path) -> CleanupLedger:
        return cls(Path(state_dir) / "video" / "cleanup.json")

    # -- persistence -------------------------------------------------------

    def _load(self) -> dict[str, LedgerEntry]:
        if self._entries is None:
            data = jsonio.read_json(self.path, None)
            raw = data.get("entries", {}) if isinstance(data, dict) else {}
            self._entries = {str(key): LedgerEntry.from_dict(value) for key, value in raw.items()}
        return self._entries

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        jsonio.write_json(
            self.path,
            {"version": 1, "entries": {key: item.to_dict() for key, item in self._load().items()}},
        )

    # -- writes ------------------------------------------------------------

    def record_unfiled(self, clip_ids: list[str], *, job: Any, now: str) -> list[LedgerEntry]:
        """Owe a deletion for these clips. Existing entries keep their history."""
        entries = self._load()
        fresh: list[LedgerEntry] = []
        for clip_id in clip_ids:
            existing = entries.get(clip_id)
            if existing is not None and existing.status == PENDING:
                continue
            entry = LedgerEntry(
                clip_id=clip_id,
                learner_folder=getattr(job, "learner_folder", ""),
                learner_name=getattr(job, "learner_name", ""),
                session_number=int(getattr(job, "session_number", 0) or 0),
                status=PENDING,
                reason="",
                recorded_at=now,
            )
            entries[clip_id] = entry
            fresh.append(entry)
        self._save()
        return fresh

    def mark_cleared(self, clip_ids: list[str], *, now: str) -> None:
        """Settle entries whose clips reached the trash or were already gone.

        Ids with no entry (trashed on the first try, before any ledger debt
        existed) are ignored: settled is settled.
        """
        entries = self._load()
        changed = False
        for clip_id in clip_ids:
            entry = entries.get(clip_id)
            if entry is None or entry.status == CLEARED:
                continue
            entries[clip_id] = LedgerEntry(
                **{**entry.to_dict(), "status": CLEARED, "cleared_at": now}
            )
            changed = True
        if changed:
            self._save()

    def mark_blocked(self, clip_ids: list[str], *, reason: str) -> None:
        """Keep a pending entry, noting why this attempt could not clear it."""
        entries = self._load()
        changed = False
        for clip_id in clip_ids:
            entry = entries.get(clip_id)
            if entry is None or entry.status == CLEARED:
                continue
            updated = {**entry.to_dict(), "reason": reason}
            entries[clip_id] = LedgerEntry(**updated)
            changed = True
        if changed:
            self._save()

    # -- reads -------------------------------------------------------------

    def pending(self) -> list[LedgerEntry]:
        return [item for item in self._load().values() if item.status == PENDING]

    def summary(self) -> dict[str, int]:
        entries = list(self._load().values())
        return {
            "pending": sum(1 for item in entries if item.status == PENDING),
            "cleared": sum(1 for item in entries if item.status == CLEARED),
        }
