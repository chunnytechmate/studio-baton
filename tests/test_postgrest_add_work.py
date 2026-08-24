"""add_work must fail loudly when PostgREST ignores return=representation.

The POST asks for ``Prefer: return=representation`` so the created row — and
its database-assigned id — can be handed back. A server that answers 2xx with
an empty body has still written the row: silently returning the caller's own
Work (id unset) hides that, and the next command that trusts the id reads as
a failure or duplicates the row. The typed contract error is the honest
outcome.
"""

from __future__ import annotations

import pytest

import baton.adapters.db.postgrest as postgrest_module
from baton.adapters.db.base import FieldMap
from baton.adapters.db.mapping import Schema
from baton.adapters.db.postgrest import PostgrestStore
from baton.domain.models import Work
from baton.errors import UpstreamError


class _Reply:
    """The slices of an HTTP response that PostgrestStore._request reads."""

    def __init__(self, status_code: int, body: bytes, parsed: object) -> None:
        self.status_code = status_code
        self.content = body
        self.text = body.decode("utf-8", "replace")
        self._parsed = parsed

    def json(self) -> object:
        return self._parsed


def _store() -> PostgrestStore:
    def fields(table: str, **columns: str) -> FieldMap:
        return FieldMap(table=table, columns=dict(columns))

    return PostgrestStore(
        "https://example.invalid/rest",
        {"Accept": "application/json"},
        Schema(
            learners=fields("learners", id="id", name="name"),
            sessions=fields("sessions", id="id", learner_id="learner_id", number="number"),
            pieces=fields("pieces", id="id", title="title"),
            works=fields(
                "works",
                id="id",
                learner_id="learner_id",
                title="title",
                type="type",
                video_link="video_link",
                performed_date="performed_date",
            ),
        ),
    )


def _a_work() -> Work:
    return Work(id="", learner_id="l-1", title="Spring recital")


def test_empty_representation_raises_instead_of_returning_an_id_less_work(monkeypatch):
    store = _store()

    def empty_body(*_args, **_kwargs):
        return _Reply(201, b"", None)

    monkeypatch.setattr(postgrest_module, "http_request", empty_body)

    with pytest.raises(UpstreamError):
        store.add_work(_a_work())


def test_null_representation_raises_the_same_way(monkeypatch):
    """A `null` body is the same silence: no row to read an id from."""
    store = _store()

    def null_body(*_args, **_kwargs):
        return _Reply(201, b"null", None)

    monkeypatch.setattr(postgrest_module, "http_request", null_body)

    with pytest.raises(UpstreamError):
        store.add_work(_a_work())


def test_success_path_still_returns_the_created_row(monkeypatch):
    store = _store()
    row = {
        "id": "w-77",
        "learner_id": "l-1",
        "title": "Spring recital",
        "type": "performance",
        "video_link": "",
        "performed_date": "",
    }

    def represented(*_args, **_kwargs):
        return _Reply(201, b"[{}]", [row])

    monkeypatch.setattr(postgrest_module, "http_request", represented)

    created = store.add_work(_a_work())

    assert created.id == "w-77"
    assert created.title == "Spring recital"
