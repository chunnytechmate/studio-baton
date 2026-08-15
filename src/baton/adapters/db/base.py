"""What a learner store must do, and how config maps onto a real schema.

The protocol is small and read-heavy. Writes are limited to the few things a
pipeline genuinely has to record, because every write is a way for Baton to
damage data a studio already owns.

:class:`FieldMap` is what makes "adopt an existing schema" a config change
rather than a fork: every table and column name comes from ``baton.yaml``, and
identifiers are validated before they are ever interpolated into SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ...domain.models import Learner, Piece, Session, Work
from ...errors import ConfigError

#: A plain SQL identifier. Anything else is rejected rather than quoted, so a
#: typo in baton.yaml surfaces as a config error instead of broken SQL — and a
#: hand-edited profile cannot smuggle a fragment into a query.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def check_identifier(value: str, *, setting: str) -> str:
    """Validate a table or column name from configuration.

    Args:
        value: The configured name.
        setting: Dotted config path, used in the error message.

    Returns:
        The name, unchanged.

    Raises:
        ConfigError: The name is not a plain SQL identifier.
    """
    if not isinstance(value, str) or not _IDENTIFIER.match(value):
        raise ConfigError(
            f"`{setting}` must be a plain table or column name, got {value!r}.",
            remedy="Use letters, digits, and underscores only, starting with a "
            "letter or underscore.",
            details={"setting": setting, "value": value},
        )
    return value


@dataclass(frozen=True)
class FieldMap:
    """Column names for one entity, resolved from configuration."""

    table: str
    columns: dict[str, str]

    @classmethod
    def build(
        cls,
        *,
        table: str,
        table_setting: str,
        columns: dict[str, Any],
        columns_setting: str,
        required: tuple[str, ...],
    ) -> FieldMap:
        """Validate and freeze the mapping for one entity.

        Raises:
            ConfigError: A required field is unmapped, or a name is not a legal
                identifier.
        """
        check_identifier(table, setting=table_setting)
        resolved: dict[str, str] = {}
        for key, value in columns.items():
            resolved[key] = check_identifier(str(value), setting=f"{columns_setting}.{key}")
        missing = [name for name in required if name not in resolved]
        if missing:
            raise ConfigError(
                f"`{columns_setting}` is missing: {', '.join(missing)}.",
                remedy="Add the missing column mappings to baton.yaml.",
                details={"setting": columns_setting, "missing": missing},
            )
        return cls(table=table, columns=resolved)

    def column(self, name: str) -> str:
        """The configured column for a domain field.

        Raises:
            ConfigError: The field is not mapped.
        """
        try:
            return self.columns[name]
        except KeyError:
            raise ConfigError(
                f"No column is mapped for `{name}` on table `{self.table}`.",
                remedy="Add it under db.fields in baton.yaml.",
            ) from None

    def has(self, name: str) -> bool:
        """Whether a domain field is mapped at all.

        Optional fields — a tone column a studio does not keep, say — are
        simply absent, and readers fall back to a default rather than failing.
        """
        return name in self.columns


@runtime_checkable
class LearnerStore(Protocol):
    """Read and write the records a teaching studio keeps.

    Implementations must not raise for "not found" — they return ``None`` or an
    empty list. Only genuine faults (unreachable service, bad credentials,
    schema mismatch) raise, and those raise the typed errors in
    :mod:`baton.errors` so the CLI maps them onto stable exit codes.
    """

    # -- learners ----------------------------------------------------------

    def list_learners(self) -> list[Learner]:
        """Every learner, ordered by name."""
        ...

    def get_learner(self, learner_id: str) -> Learner | None:
        """One learner by id, or ``None``."""
        ...

    def set_current_piece(self, learner_id: str, piece_id: str | None) -> None:
        """Assign (or clear) the piece a learner is working on."""
        ...

    # -- sessions ----------------------------------------------------------

    def list_sessions(self, learner_id: str) -> list[Session]:
        """A learner's sessions, ordered by number."""
        ...

    def get_session(self, learner_id: str, number: int) -> Session | None:
        """One numbered session, or ``None``."""
        ...

    # -- pieces ------------------------------------------------------------

    def list_pieces(self) -> list[Piece]:
        """Every piece, ordered by title."""
        ...

    def get_piece(self, piece_id: str) -> Piece | None:
        """One piece by id, or ``None``."""
        ...

    # -- works -------------------------------------------------------------

    def list_works(self, learner_id: str) -> list[Work]:
        """A learner's recorded works, newest first."""
        ...

    def add_work(self, work: Work) -> Work:
        """Record a finished work. Returns it with its assigned id."""
        ...

    # -- lifecycle ---------------------------------------------------------

    def health(self) -> None:
        """Prove the store is reachable and the schema matches config.

        Raises:
            BatonError: With the specific reason. Used by ``baton doctor``,
                which is why it checks the schema and not just connectivity —
                a renamed column should be caught before a pipeline hits it.
        """
        ...

    def close(self) -> None:
        """Release any held resources."""
        ...
