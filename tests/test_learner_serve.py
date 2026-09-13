"""The localhost checklist behind `learner deactivate --serve`.

The write path is exercised through ``FakeLearnerStore`` (a real learner must
never be touched by a test), and the render/parse functions are driven
directly: the socket is one thin layer over plain functions, so the tests do
not need one either.
"""

from __future__ import annotations

import urllib.request
from threading import Thread

import pytest

from baton.adapters.fakes import FakeLearnerStore
from baton.cli.serve import build_checklist_server, parse_form, render_checklist
from baton.domain.models import Learner
from baton.errors import ConfigError

STUDIO = [
    Learner(id="1", name="น้องจี", instrument="guitar"),
    Learner(id="2", name="น้องเจี้ยนซี", instrument="guitar", is_active=False),
    Learner(id="3", name="Jee Wongsakorn", instrument="drums"),
]


def test_the_page_offers_the_active_and_names_the_gone():
    page = render_checklist(STUDIO, label="learner")

    # The active learners are checkboxes keyed by id; the inactive one is
    # named in the static section instead, with the way back.
    assert 'value="1"' in page and "น้องจี" in page
    assert 'value="3"' in page
    assert 'value="2"' not in page
    assert "Already inactive" in page
    assert "น้องเจี้ยนซี" in page
    # A Thai name must survive as text, never as markup.
    assert "<น้อง" not in page


def test_a_page_with_nobody_gone_says_so():
    page = render_checklist(STUDIO[:1], label="learner")

    assert "Already inactive" not in page


def test_parse_form_keeps_the_ticked_ids_in_order_and_deduplicated_by_the_caller():
    body = b"learner=1&learner=3&learner=1"

    assert parse_form(body) == ["1", "3", "1"]


def test_an_oversized_body_is_refused():
    with pytest.raises(ConfigError):
        parse_form(b"learner=1&" * 20000)


def _apply_through_fake(store: FakeLearnerStore):
    """The same apply closure `run_checklist` builds, over a fake store."""

    def apply(ids: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        roster = {item.id: item for item in store.list_learners()}
        applied: list[str] = []
        failed: list[tuple[str, str]] = []
        for learner_id in dict.fromkeys(ids):
            learner = roster.get(learner_id)
            if learner is None:
                continue
            try:
                store.set_active(learner.id, False)
            except Exception as exc:
                failed.append((learner.name, str(exc)))
                continue
            applied.append(learner.name)
        return applied, failed

    return apply


def test_ticking_marks_and_an_unknown_id_is_dropped():
    store = FakeLearnerStore(learners=list(STUDIO))

    applied, failed = _apply_through_fake(store)(["1", "999", "2"])

    # The unknown id vanishes; ticking someone already inactive succeeds by
    # being a no-op, so a re-submitted form never reports phantom failures.
    assert applied == ["น้องจี", "น้องเจี้ยนซี"]
    assert failed == []
    assert store.get_learner("1").is_active is False
    assert store.get_learner("3").is_active is True
    assert store.get_learner("2").is_active is False


def test_a_refusing_store_lands_in_failed_without_aborting_the_rest():
    store = FakeLearnerStore(learners=list(STUDIO))
    store.fail_with = None

    original = store.set_active

    def refusing(learner_id: str, active: bool) -> None:
        if learner_id == "3":
            raise ConfigError("the column is unmapped")
        original(learner_id, active)

    store.set_active = refusing  # type: ignore[method-assign]

    applied, failed = _apply_through_fake(store)(["1", "3"])

    assert applied == ["น้องจี"]
    assert failed == [("Jee Wongsakorn", "the column is unmapped")]


def test_the_served_page_round_trips_over_a_socket():
    store = FakeLearnerStore(learners=list(STUDIO))
    page = lambda: render_checklist(store.list_learners(), label="learner")  # noqa: E731
    apply = _apply_through_fake(store)

    # port 0 asks the OS for a free one, so parallel test runs never collide.
    server = build_checklist_server(port=0, page=page, apply=apply, log=lambda _msg: None)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{base}/") as response:  # noqa: S310 - loopback, fixed scheme
            body = response.read().decode("utf-8")
            assert "น้องจี" in body
            assert response.status == 200

        request = urllib.request.Request(  # noqa: S310 - loopback, fixed scheme
            base + "/", data=b"learner=1", method="POST"
        )
        with urllib.request.urlopen(request) as response:  # noqa: S310 - loopback, fixed scheme
            assert "Marked" in response.read().decode("utf-8")

        assert store.get_learner("1").is_active is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
