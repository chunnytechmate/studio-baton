"""What a messenger must do, and how a typed name becomes a recipient.

The gate is the point of this subsystem, so the protocol reflects it:
:meth:`Messenger.resolve` answers "does this recipient exist" *before* anything
is sent, and :meth:`SendOutcome.sent` is a fact the caller records — never an
assumption the caller makes from the absence of an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...core.config import Config
from ...errors import NeedsHumanError


@dataclass(frozen=True)
class SendOutcome:
    """What one attempted send actually did."""

    sent: bool
    recipient: str
    detail: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"sent": self.sent, "recipient": self.recipient}
        if self.detail:
            payload["detail"] = self.detail
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload


@runtime_checkable
class Messenger(Protocol):
    """Delivers a text message to a named recipient."""

    def resolve(self, name: str) -> str:
        """A typed name to a platform recipient id.

        Raises:
            NeedsHumanError: The name matches no configured contact, or more
                than one. The error carries the candidates; a recipient is
                never guessed, because a message about one learner delivered
                to the wrong household is worse than no message.
        """
        ...

    def send(self, recipient_id: str, text: str) -> SendOutcome:
        """Deliver one text message.

        Implementations retry transient faults internally; the outcome records
        the final truth rather than letting a caller infer it.
        """
        ...

    def health(self) -> None:
        """Prove credentials work, without sending anything."""
        ...


def resolve_contact(config: Config, query: str) -> tuple[str, str]:
    """Resolve a typed name against the profile's contacts.

    Same stance as learner resolution, for the same reason: exact or alias
    only. A recipient alias list is short enough that a partial match being
    "helpfully" completed is all it takes to send a child's progress report to
    the wrong person.

    Returns:
        ``(contact_key, recipient_id)`` — the key names the contact in
        configuration, the id is what a driver delivers to.

    Raises:
        NeedsHumanError: No match, or several. Carries the candidates.
    """
    contacts = config.section("chat.contacts")
    if not contacts:
        raise NeedsHumanError(
            "No contacts are configured.",
            candidates=[],
            remedy="Add a `chat.contacts` block to baton.yaml before sending anything.",
        )

    wanted = query.strip().casefold()
    exact: list[tuple[str, dict[str, Any]]] = []
    for key, entry in contacts.items():
        if not isinstance(entry, dict):
            continue
        if str(key).casefold() == wanted:
            exact = [(str(key), entry)]
            break
        for alias in entry.get("aliases", []) or []:
            if str(alias).strip().casefold() == wanted:
                exact.append((str(key), entry))

    if len(exact) == 1:
        key, entry = exact[0]
        recipient_id = str(entry.get("id_env", ""))
        if not recipient_id:
            raise NeedsHumanError(
                f"Contact `{key}` has no id_env.",
                candidates=[],
                remedy=f"Set chat.contacts.{key}.id_env to the environment variable "
                "holding their platform id.",
            )
        return key, recipient_id

    if len(exact) > 1:
        raise NeedsHumanError(
            f"“{query}” matches more than one contact.",
            candidates=[{"name": key} for key, _ in exact],
            remedy="Use the contact's exact name from baton.yaml.",
        )

    raise NeedsHumanError(
        f"No contact matches “{query}”.",
        candidates=[{"name": str(key)} for key in contacts],
        remedy="Check chat.contacts in baton.yaml, or add the person first.",
    )
