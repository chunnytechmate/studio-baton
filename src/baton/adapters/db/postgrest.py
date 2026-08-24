"""Learner records over a PostgREST API.

Covers both drivers: Supabase *is* PostgREST with an ``apikey`` header, so the
`supabase` driver is this class with different headers rather than a second
implementation. The original system had a hand-rolled Supabase client and a
separate local-PostgREST fallback client that drifted apart; one class with two
header sets is the fix.

Every request goes through :func:`baton.core.retry.http_request`, so rate limits
and 5xx are retried with backoff and a lost connection becomes a typed
``UpstreamError`` rather than a traceback.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ...core.config import Config
from ...core.retry import http_request
from ...domain.models import Learner, Piece, Session, Work
from ...errors import ConfigError, UpstreamError
from .base import FieldMap
from .mapping import Schema


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return False


class PostgrestStore:
    """A :class:`~baton.adapters.db.base.LearnerStore` over PostgREST."""

    driver = "postgrest"

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        schema: Schema,
        *,
        driver_name: str = "postgrest",
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers
        self.schema = schema
        self.driver = driver_name
        self.timeout = timeout

    # -- construction ------------------------------------------------------

    @classmethod
    def from_config(cls, config: Config) -> PostgrestStore:
        url = config.secret("db.postgrest.url_env")
        jwt = config.secret("db.postgrest.jwt_env", required=False)
        headers = {"Accept": "application/json"}
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        schema_name = str(config.get("db.postgrest.schema", "public"))
        if schema_name and schema_name != "public":
            headers["Accept-Profile"] = schema_name
            headers["Content-Profile"] = schema_name
        return cls(str(url), headers, Schema.from_config(config), driver_name="postgrest")

    @classmethod
    def from_supabase_config(cls, config: Config) -> PostgrestStore:
        url = str(config.secret("db.supabase.url_env")).rstrip("/")
        key = str(config.secret("db.supabase.key_env"))
        headers = {
            "Accept": "application/json",
            "apikey": key,
            "Authorization": f"Bearer {key}",
        }
        return cls(
            f"{url}/rest/v1",
            headers,
            Schema.from_config(config),
            driver_name="supabase",
        )

    # -- transport ---------------------------------------------------------

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: str = "",
        json_body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.base_url}/{quote(table)}"
        if params:
            url = f"{url}?{params}"
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        response = http_request(
            method,
            url,
            service=self.driver,
            headers=headers,
            json=json_body,
            timeout=self.timeout,
        )

        if response.status_code in (401, 403):
            raise ConfigError(
                f"{self.driver} rejected the credentials ({response.status_code}).",
                remedy="Check the API key or JWT named in baton.yaml, and that "
                "the role may read these tables.",
            )
        if response.status_code == 404:
            raise ConfigError(
                f"{self.driver} has no table `{table}`.",
                remedy="Correct db.tables in baton.yaml, or run the migration "
                "in migrations/postgres.sql.",
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            # PostgREST reports an unknown column as 400 with the name in the
            # body — the single most likely misconfiguration, so name it.
            raise ConfigError(
                f"{self.driver} rejected the query: {detail}",
                remedy="Usually a column named in db.fields that does not "
                "exist. `baton doctor` checks the whole schema at once.",
                details={"status": response.status_code, "body": detail},
            )

        if not response.content:
            return []
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"{self.driver} returned a response that is not JSON.",
                service=self.driver,
                status=response.status_code,
            ) from exc

    def _select_params(self, fields: FieldMap, filters: str = "", order: str = "") -> str:
        columns = ",".join(sorted(set(fields.columns.values())))
        parts = [f"select={columns}"]
        if filters:
            parts.append(filters)
        if order:
            parts.append(f"order={order}")
        return "&".join(parts)

    def _rows(self, fields: FieldMap, filters: str = "", order: str = "") -> list[dict[str, Any]]:
        data = self._request(
            "GET", fields.table, params=self._select_params(fields, filters, order)
        )
        return data if isinstance(data, list) else []

    def _get(self, row: dict[str, Any], fields: FieldMap, name: str, default: Any = "") -> Any:
        return row.get(fields.column(name), default) if fields.has(name) else default

    # -- learners ----------------------------------------------------------

    def _learner(self, row: dict[str, Any]) -> Learner:
        fields = self.schema.learners
        current = self._get(row, fields, "current_piece_id", None)
        return Learner(
            id=_text(row.get(fields.column("id"))),
            name=_text(row.get(fields.column("name"))),
            instrument=_text(self._get(row, fields, "instrument")),
            tone=_text(self._get(row, fields, "tone")),
            has_instrument=_to_bool(self._get(row, fields, "has_instrument", False)),
            current_piece_id=None if current in (None, "") else _text(current),
            raw=row,
        )

    def list_learners(self) -> list[Learner]:
        fields = self.schema.learners
        rows = self._rows(fields, order=f"{fields.column('name')}.asc")
        return [self._learner(row) for row in rows]

    def get_learner(self, learner_id: str) -> Learner | None:
        fields = self.schema.learners
        rows = self._rows(fields, filters=f"{fields.column('id')}=eq.{quote(str(learner_id))}")
        return self._learner(rows[0]) if rows else None

    def set_current_piece(self, learner_id: str, piece_id: str | None) -> None:
        fields = self.schema.learners
        if not fields.has("current_piece_id"):
            raise ConfigError(
                "This profile does not map a current piece column.",
                remedy="Add db.fields.learner.current_piece_id to baton.yaml.",
            )
        self._request(
            "PATCH",
            fields.table,
            params=f"{fields.column('id')}=eq.{quote(str(learner_id))}",
            json_body={fields.column("current_piece_id"): piece_id},
            prefer="return=minimal",
        )

    # -- sessions ----------------------------------------------------------

    def _session(self, row: dict[str, Any]) -> Session:
        fields = self.schema.sessions
        return Session(
            id=_text(row.get(fields.column("id"))),
            learner_id=_text(row.get(fields.column("learner_id"))),
            number=int(row.get(fields.column("number")) or 0),
            doc_id=_text(self._get(row, fields, "doc_id")),
            raw=row,
        )

    def list_sessions(self, learner_id: str) -> list[Session]:
        fields = self.schema.sessions
        rows = self._rows(
            fields,
            filters=f"{fields.column('learner_id')}=eq.{quote(str(learner_id))}",
            order=f"{fields.column('number')}.asc",
        )
        return [self._session(row) for row in rows]

    def get_session(self, learner_id: str, number: int) -> Session | None:
        fields = self.schema.sessions
        rows = self._rows(
            fields,
            filters=(
                f"{fields.column('learner_id')}=eq.{quote(str(learner_id))}"
                f"&{fields.column('number')}=eq.{int(number)}"
            ),
        )
        return self._session(rows[0]) if rows else None

    # -- pieces ------------------------------------------------------------

    def _piece(self, row: dict[str, Any]) -> Piece:
        fields = self.schema.pieces
        return Piece(
            id=_text(row.get(fields.column("id"))),
            title=_text(row.get(fields.column("title"))),
            source_link=_text(self._get(row, fields, "source_link")),
            practice_track=_text(self._get(row, fields, "practice_track")),
            sheet_link=_text(self._get(row, fields, "sheet_link")),
            raw=row,
        )

    def list_pieces(self) -> list[Piece]:
        fields = self.schema.pieces
        rows = self._rows(fields, order=f"{fields.column('title')}.asc")
        return [self._piece(row) for row in rows]

    def get_piece(self, piece_id: str) -> Piece | None:
        fields = self.schema.pieces
        rows = self._rows(fields, filters=f"{fields.column('id')}=eq.{quote(str(piece_id))}")
        return self._piece(rows[0]) if rows else None

    # -- works -------------------------------------------------------------

    def _work(self, row: dict[str, Any]) -> Work:
        fields = self.schema.works
        return Work(
            id=_text(row.get(fields.column("id"))),
            learner_id=_text(row.get(fields.column("learner_id"))),
            title=_text(row.get(fields.column("title"))),
            type=_text(self._get(row, fields, "type", "performance")) or "performance",
            video_link=_text(self._get(row, fields, "video_link")),
            performed_date=_text(self._get(row, fields, "performed_date")),
            raw=row,
        )

    def list_works(self, learner_id: str) -> list[Work]:
        fields = self.schema.works
        order_column = "performed_date" if fields.has("performed_date") else "id"
        rows = self._rows(
            fields,
            filters=f"{fields.column('learner_id')}=eq.{quote(str(learner_id))}",
            order=f"{fields.column(order_column)}.desc",
        )
        return [self._work(row) for row in rows]

    def add_work(self, work: Work) -> Work:
        fields = self.schema.works
        payload: dict[str, Any] = {
            fields.column("learner_id"): work.learner_id,
            fields.column("title"): work.title,
        }
        for name, value in (
            ("type", work.type),
            ("video_link", work.video_link),
            ("performed_date", work.performed_date),
        ):
            if fields.has(name) and value:
                payload[fields.column(name)] = value

        created = self._request(
            "POST", fields.table, json_body=payload, prefer="return=representation"
        )
        rows = created if isinstance(created, list) else [created]
        if not rows or not isinstance(rows[0], dict):
            # The server ignored Prefer: return=representation. The row may
            # well be written, so handing back the caller's Work (id unset)
            # would hide that — the next add would duplicate it.
            raise UpstreamError(
                f"{self.driver} accepted the work but returned no "
                f"representation to read the assigned id from.",
                service=self.driver,
                remedy="The row may exist on the server — check for it before "
                "retrying, or this add will run twice.",
            )
        return self._work(rows[0])

    # -- lifecycle ---------------------------------------------------------

    def health(self) -> None:
        """Read one row from every configured table."""
        for fields in (
            self.schema.learners,
            self.schema.sessions,
            self.schema.pieces,
            self.schema.works,
        ):
            self._request("GET", fields.table, params=f"{self._select_params(fields)}&limit=1")

    def close(self) -> None:
        """Nothing to release: requests are stateless."""
