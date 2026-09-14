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
from baton.errors import ConfigError, StateError, UpstreamError


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


# -- set_active ----------------------------------------------------------------


def test_set_active_patches_the_mapped_column(monkeypatch):
    """The write path the deactivation checklist and CLI both land on."""
    store = _store()
    # The base _store() maps no is_active; the production profile does, and
    # so must any store asked to change a status.
    store.schema.learners.columns["is_active"] = "is_active"

    seen: dict[str, object] = {}

    def patch(*args: object, **kwargs: object) -> _Reply:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _Reply(200, b'[{"id": 7}]', [{"id": 7}])

    monkeypatch.setattr(postgrest_module, "http_request", patch)

    store.set_active("7", False)

    assert seen["kwargs"]["json"] == {"is_active": False}
    assert seen["args"][1].endswith("learners?id=eq.7")
    assert seen["kwargs"]["headers"]["Prefer"] == "return=representation"
    assert seen["args"][0] == "PATCH"


def test_set_active_on_an_unmatched_row_is_a_state_error(monkeypatch):
    """The 200-with-empty-array trap: matched nothing, marked nothing."""
    store = _store()
    store.schema.learners.columns["is_active"] = "is_active"

    def nobody(*_args, **_kwargs):
        return _Reply(200, b"[]", [])

    monkeypatch.setattr(postgrest_module, "http_request", nobody)

    from baton.errors import StateError

    with pytest.raises(StateError):
        store.set_active("7", False)


def test_set_active_without_a_mapping_refuses_before_any_request(monkeypatch):
    store = _store()

    def must_not_happen(*_args, **_kwargs):  # pragma: no cover - the point
        raise AssertionError("no request may leave before the config error")

    monkeypatch.setattr(postgrest_module, "http_request", must_not_happen)

    with pytest.raises(ConfigError):
        store.set_active("7", False)


# -- trash / untrash -------------------------------------------------------------


def test_update_learner_patches_only_the_named_columns(monkeypatch):
    store = _store()
    store.schema.learners.columns["instrument"] = "instrument"
    store.schema.learners.columns["has_instrument"] = "has_instrument"

    seen: dict[str, object] = {}

    def patch(*args: object, **kwargs: object) -> _Reply:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _Reply(200, b'[{"id": 7}]', [{"id": 7}])

    monkeypatch.setattr(postgrest_module, "http_request", patch)

    store.update_learner("7", {"instrument": "กีตาร์", "has_instrument": True})

    assert seen["kwargs"]["json"] == {"instrument": "กีตาร์", "has_instrument": True}
    assert seen["args"][0] == "PATCH"
    assert seen["args"][1].endswith("learners?id=eq.7")
    assert seen["kwargs"]["headers"]["Prefer"] == "return=representation"


def test_update_learner_of_an_unknown_field_refuses_before_any_request(monkeypatch):
    store = _store()
    store.schema.learners.columns["tone"] = "tone"

    def fail(*_args, **_kwargs):
        raise AssertionError("must refuse before any request")

    monkeypatch.setattr(postgrest_module, "http_request", fail)

    with pytest.raises(ConfigError):
        store.update_learner("7", {"nickname": "nick"})


def test_update_learner_on_an_unmatched_row_is_a_state_error(monkeypatch):
    store = _store()
    store.schema.learners.columns["tone"] = "tone"

    def nobody(*_args, **_kwargs):
        return _Reply(200, b"[]", [])

    monkeypatch.setattr(postgrest_module, "http_request", nobody)

    with pytest.raises(StateError):
        store.update_learner("7", {"tone": "child"})


def test_trash_learner_patches_the_mapped_column_with_a_timestamp(monkeypatch):
    store = _store()
    store.schema.learners.columns["deleted_at"] = "deleted_at"

    seen: dict[str, object] = {}

    def patch(*args: object, **kwargs: object) -> _Reply:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _Reply(200, b'[{"id": 7}]', [{"id": 7}])

    monkeypatch.setattr(postgrest_module, "http_request", patch)

    store.trash_learner("7")

    assert isinstance(seen["kwargs"]["json"]["deleted_at"], str)
    assert seen["kwargs"]["json"]["deleted_at"]  # non-empty ISO stamp
    assert seen["args"][1].endswith("learners?id=eq.7")
    assert seen["kwargs"]["headers"]["Prefer"] == "return=representation"
    assert seen["args"][0] == "PATCH"


def test_untrash_learner_patches_the_mapped_column_with_null(monkeypatch):
    store = _store()
    store.schema.learners.columns["deleted_at"] = "deleted_at"

    seen: dict[str, object] = {}

    def patch(*args: object, **kwargs: object) -> _Reply:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return _Reply(200, b'[{"id": 7}]', [{"id": 7}])

    monkeypatch.setattr(postgrest_module, "http_request", patch)

    store.untrash_learner("7")

    assert seen["kwargs"]["json"] == {"deleted_at": None}


def test_trash_learner_on_an_unmatched_row_is_a_state_error(monkeypatch):
    store = _store()
    store.schema.learners.columns["deleted_at"] = "deleted_at"

    def nobody(*_args, **_kwargs):
        return _Reply(200, b"[]", [])

    monkeypatch.setattr(postgrest_module, "http_request", nobody)

    from baton.errors import StateError

    with pytest.raises(StateError):
        store.trash_learner("7")


def test_trash_learner_without_a_mapping_refuses_before_any_request(monkeypatch):
    store = _store()

    def must_not_happen(*_args, **_kwargs):  # pragma: no cover - the point
        raise AssertionError("no request may leave before the config error")

    monkeypatch.setattr(postgrest_module, "http_request", must_not_happen)

    with pytest.raises(ConfigError):
        store.trash_learner("7")
