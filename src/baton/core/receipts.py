"""Proof that a message already went out, so it does not go out twice.

The hole this fills is not a Baton bug — it is what a *harness* does to a
correct program. An agent runs `baton send lesson …`; the platform accepts the
message; before the JSON envelope reaches stdout the harness's per-call time
limit expires and kills the process. The agent sees a killed command, concludes
the send failed, and sends again. A parent gets the same lesson summary twice,
and nothing in the logs looks wrong.

LINE was already safe: `adapters.chat.drivers` computes a deterministic
``X-Line-Retry-Key`` from the token, recipient, and exact text, so LINE itself
collapses the repeat — even across processes. Telegram and the generic webhook
have no equivalent, and the legacy studio scripts this package replaced list
"no idempotency key (risk of duplicate messages)" among their known issues. So
the record is kept locally instead, where it protects every driver equally.

What is stored is a digest, never the message: the state directory sits next to
a studio's real learner data and a plain-text archive of every message sent to a
parent is not something a tool should create as a side effect of being careful.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..errors import DuplicateSendError
from . import jsonio

#: Filename under the state directory.
RECEIPTS_FILE = "send-receipts.json"

#: How long an identical message stays "already sent". Long enough to cover a
#: teaching day and the evening a summary is written up in; short enough that a
#: genuinely repeated weekly message next week is nobody's business but the
#: studio's.
DEFAULT_WINDOW_HOURS = 12.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Receipts:
    """The record of what has already been delivered from this profile.

    Args:
        path: The receipts file. Created on first write.
        window_hours: How long a receipt suppresses an identical send.
    """

    path: Path
    window_hours: float = DEFAULT_WINDOW_HOURS

    @classmethod
    def for_state(cls, state_dir: Path, window_hours: float = DEFAULT_WINDOW_HOURS) -> Receipts:
        return cls(Path(state_dir) / RECEIPTS_FILE, window_hours=window_hours)

    @staticmethod
    def digest(service: str, recipient_id: str, material: str) -> str:
        """A stable fingerprint of one delivery.

        Args:
            service: Driver name, so a studio that switches from LINE to
                Telegram is not told its first message there is a repeat.
            recipient_id: The platform id. The same summary to two households
                is two sends, not one.
            material: What identifies *this* message. Callers that can name the
                thing being sent pass an identity — ``"lesson|17|3"`` — and
                callers that cannot pass the message text.

        Identity beats text wherever it is available, and `send lesson` is why:
        its composer picks an opening and closing phrase at random, so two sends
        of one summary never produce the same bytes. A text digest would have
        agreed that they were different messages every single time, which is
        the one answer that makes this whole module useless.
        """
        return hashlib.sha256(f"{service}|{recipient_id}|{material}".encode()).hexdigest()

    # -- reading -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        raw = jsonio.read_json(self.path, default={})
        entries = raw.get("receipts") if isinstance(raw, dict) else None
        return entries if isinstance(entries, dict) else {}

    def find(self, key: str) -> dict[str, Any] | None:
        """The receipt for ``key`` if one was written inside the window."""
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        when = _parse(str(entry.get("sent_at", "")))
        if when is None:
            return None
        if _now() - when > timedelta(hours=self.window_hours):
            return None
        return entry

    # -- writing -----------------------------------------------------------

    def record(self, key: str, **fields: Any) -> None:
        """Write the receipt for a delivery that just happened.

        Old entries are dropped in the same pass. Nothing else prunes this
        file, and a studio that never prunes is the normal case.
        """
        entries = self._load()
        cutoff = _now() - timedelta(hours=self.window_hours)
        kept = {
            existing_key: value
            for existing_key, value in entries.items()
            if isinstance(value, dict)
            and (_parse(str(value.get("sent_at", ""))) or cutoff) > cutoff
        }
        kept[key] = {"sent_at": _now().isoformat(timespec="seconds"), **fields}
        jsonio.write_json(self.path, {"version": 1, "receipts": kept})

    # -- the gate ----------------------------------------------------------

    def guard(self, key: str, *, what: str, again: bool) -> None:
        """Refuse a send that already happened, unless a person insists.

        Args:
            key: Digest from :meth:`digest`.
            what: Human description of the message, for the refusal.
            again: The caller passed ``--again``. Skips the check entirely.

        Raises:
            DuplicateSendError: An identical message went out inside the window.

        ``--again`` exists because the one thing this cannot know is whether
        the message *arrived*. A person who watched a parent's phone stay silent
        must be able to say so. An agent must not decide that on its own, which
        is why the remedy says so in the words a skill will relay.
        """
        if again:
            return
        seen = self.find(key)
        if seen is None:
            return
        raise DuplicateSendError(
            f"{what} was already sent at {seen.get('sent_at', 'an earlier time')}.",
            already_sent=seen,
            window_hours=self.window_hours,
            remedy="Nothing was sent. If a person has confirmed the first "
            "message never arrived, re-run with --again; otherwise this is the "
            "duplicate that a killed or retried command would have caused.",
        )


__all__ = ["DEFAULT_WINDOW_HOURS", "RECEIPTS_FILE", "Receipts"]
