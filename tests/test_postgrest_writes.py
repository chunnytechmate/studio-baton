"""Every PostgrestStore write must fail loudly when the server is silent.

Every POST asks for ``Prefer: return=representation`` so the created row (and
its database-assigned id) can be handed back. A server that answers 2xx with
an empty body has still written the row: silently returning the caller's own
object (id unset) hides that, and the next command that trusts the id reads
as a failure or duplicates the row. The typed contract error is the honest
outcome. `update_piece`/`delete_piece` have the same silence from the other
direction: a 200 with an empty array means the filter matched nothing, which
PostgREST itself does not treat as an error: the original script read that
as a successful edit.
"""

from __future__ import annotations

import pytest

import baton.adapters.db.postgrest as postgrest_module
from baton.adapters.db.base import FieldMap
from baton.adapters.db.mapping import Schema
from baton.adapters.db.postgrest import PostgrestStore
from baton.domain.models import Learner, Piece, Session, Work
from baton.errors import ConfigError, UpstreamError


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


# -- the other writers share the same missing-representation contract --------


def test_add_learner_raises_without_a_representation(monkeypatch):
    store = _store()
    monkeypatch.setattr(postgrest_module, "http_request", lambda *_a, **_k: _Reply(201, b"", None))

    with pytest.raises(UpstreamError):
        store.add_learner(Learner(id="", name="New Person"))


def test_add_learner_returns_the_created_row(monkeypatch):
    store = _store()
    row = {"id": "l-9", "name": "New Person"}
    monkeypatch.setattr(
        postgrest_module, "http_request", lambda *_a, **_k: _Reply(201, b"[{}]", [row])
    )

    created = store.add_learner(Learner(id="", name="New Person"))

    assert created.id == "l-9"
    assert created.name == "New Person"


def test_add_session_returns_the_created_row(monkeypatch):
    store = _store()
    row = {"id": "s-4", "learner_id": "l-1", "number": 3}
    monkeypatch.setattr(
        postgrest_module, "http_request", lambda *_a, **_k: _Reply(201, b"[{}]", [row])
    )

    created = store.add_session(Session(id="", learner_id="l-1", number=3))

    assert created.id == "s-4"
    assert created.number == 3


def test_add_piece_returns_the_created_row(monkeypatch):
    store = _store()
    row = {"id": "p-2", "title": "Nocturne"}
    monkeypatch.setattr(
        postgrest_module, "http_request", lambda *_a, **_k: _Reply(201, b"[{}]", [row])
    )

    created = store.add_piece(Piece(id="", title="Nocturne"))

    assert created.id == "p-2"


def test_update_piece_on_an_unmatched_filter_returns_none(monkeypatch):
    """PostgREST answers 200 with an empty array when nothing matched the
    filter: a silent success the legacy script reported as a real edit."""
    store = _store()
    monkeypatch.setattr(postgrest_module, "http_request", lambda *_a, **_k: _Reply(200, b"[]", []))

    assert store.update_piece("999", {"title": "Nope"}) is None


def test_update_piece_returns_the_updated_row(monkeypatch):
    store = _store()
    row = {"id": "p-2", "title": "Renamed"}
    monkeypatch.setattr(
        postgrest_module, "http_request", lambda *_a, **_k: _Reply(200, b"[{}]", [row])
    )

    updated = store.update_piece("p-2", {"title": "Renamed"})

    assert updated.title == "Renamed"


def test_delete_piece_reports_whether_a_row_was_removed(monkeypatch):
    store = _store()
    monkeypatch.setattr(postgrest_module, "http_request", lambda *_a, **_k: _Reply(200, b"[]", []))

    assert store.delete_piece("999") is False


def test_delete_piece_reports_success(monkeypatch):
    store = _store()
    row = {"id": "p-2", "title": "Nocturne"}
    monkeypatch.setattr(
        postgrest_module, "http_request", lambda *_a, **_k: _Reply(200, b"[{}]", [row])
    )

    assert store.delete_piece("p-2") is True


def test_an_unmapped_extra_field_raises_before_any_request(monkeypatch):
    store = _store()
    calls: list[object] = []
    monkeypatch.setattr(
        postgrest_module,
        "http_request",
        lambda *args, **kwargs: calls.append(args) or _Reply(201, b"[{}]", [{}]),
    )

    with pytest.raises(ConfigError):
        store.add_learner(Learner(id="", name="Ghost"), extra={"prompt_level": 2})

    assert calls == []
