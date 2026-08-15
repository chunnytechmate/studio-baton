"""Building the four :class:`FieldMap` objects from a profile's config.

Shared by every driver: SQLite and PostgREST disagree about how to *query* a
schema, but not about what the schema is called. Keeping the resolution here
means a renamed column is configured once and honoured everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.config import Config
from .base import FieldMap


@dataclass(frozen=True)
class Schema:
    """The complete mapping from Baton's model onto a studio's own tables."""

    learners: FieldMap
    sessions: FieldMap
    pieces: FieldMap
    works: FieldMap

    @classmethod
    def from_config(cls, config: Config) -> Schema:
        """Resolve and validate every table and column name.

        Raises:
            ConfigError: A name is missing or is not a legal identifier.
        """
        tables = config.section("db.tables")
        fields = config.section("db.fields")

        return cls(
            learners=FieldMap.build(
                table=str(tables.get("learners", "learners")),
                table_setting="db.tables.learners",
                columns=fields.get("learner", {}),
                columns_setting="db.fields.learner",
                required=("id", "name"),
            ),
            sessions=FieldMap.build(
                table=str(tables.get("sessions", "sessions")),
                table_setting="db.tables.sessions",
                columns=fields.get("session", {}),
                columns_setting="db.fields.session",
                required=("id", "learner_id", "number"),
            ),
            pieces=FieldMap.build(
                table=str(tables.get("pieces", "pieces")),
                table_setting="db.tables.pieces",
                columns=fields.get("piece", {}),
                columns_setting="db.fields.piece",
                required=("id", "title"),
            ),
            works=FieldMap.build(
                table=str(tables.get("works", "works")),
                table_setting="db.tables.works",
                columns=fields.get("work", {}),
                columns_setting="db.fields.work",
                required=("id", "learner_id", "title"),
            ),
        )
