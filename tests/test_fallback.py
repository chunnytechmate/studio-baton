"""Failover semantics.

One rule, and it is asymmetric: reads may be served by the secondary store,
writes never may. A read from a stale replica is a slightly old answer; a write
to a replica is two stores that disagree forever with nothing to reconcile them.
"""

from __future__ import annotations

import pytest

from baton.adapters.db.fallback import FallbackStore
from baton.adapters.fakes import FakeLearnerStore
from baton.domain.models import Learner, Piece, Session, Work
from baton.errors import ConfigError, UpstreamError

PRIMARY_PEOPLE = [Learner(id="1", name="Ada Whitfield")]
REPLICA_PEOPLE = [Learner(id="1", name="Ada Whitfield"), Learner(id="2", name="Old Record")]


@pytest.fixture
def pair():
    primary = FakeLearnerStore(learners=list(PRIMARY_PEOPLE))
    secondary = FakeLearnerStore(learners=list(REPLICA_PEOPLE))
    return primary, secondary, FallbackStore(primary, secondary)


def test_reads_use_the_primary_while_it_is_healthy(pair):
    _, _, store = pair

    assert [p.name for p in store.list_learners()] == ["Ada Whitfield"]
    assert store.degraded is False


def test_reads_fall_over_when_the_primary_is_unreachable(pair):
    primary, _, store = pair
    primary.fail_with = UpstreamError("supabase unreachable", service="supabase")

    names = [p.name for p in store.list_learners()]

    assert names == ["Ada Whitfield", "Old Record"]


def test_a_failover_is_recorded_so_callers_can_say_the_data_may_be_stale(pair):
    primary, _, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    store.list_learners()

    assert store.degraded is True


def test_writes_never_fall_over(pair):
    """The property this module exists for. A write during an outage must fail
    rather than land in a store the primary will never learn about."""
    primary, secondary, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.add_work(Work(id="", learner_id="1", title="Blackbird"))

    assert secondary.works == []


def test_assignment_writes_also_refuse_to_divert(pair):
    primary, secondary, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.set_current_piece("1", "3")

    assert secondary.get_learner("1").current_piece_id is None


def test_add_learner_never_falls_over(pair):
    primary, secondary, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.add_learner(Learner(id="", name="New Person"))

    assert all(learner.name != "New Person" for learner in secondary.learners)


def test_add_session_never_falls_over(pair):
    primary, secondary, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.add_session(Session(id="", learner_id="1", number=1))

    assert secondary.sessions == []


def test_piece_writes_never_fall_over(pair):
    primary, secondary, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.add_piece(Piece(id="", title="New Piece"))
    with pytest.raises(UpstreamError):
        store.update_piece("1", {"title": "Renamed"})
    with pytest.raises(UpstreamError):
        store.delete_piece("1")

    assert secondary.pieces == []


def test_a_config_error_is_not_treated_as_an_outage(pair):
    """Bad credentials or a missing column would fail identically on the
    secondary. Falling over would run the same error twice and bury the cause."""
    primary, secondary, store = pair
    primary.fail_with = ConfigError("column `full_name` does not exist")
    secondary.fail_with = None

    with pytest.raises(ConfigError):
        store.list_learners()

    assert store.degraded is False


def test_health_reports_the_primary_only(pair):
    """A green report while the primary is down would hide the outage."""
    primary, _, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    with pytest.raises(UpstreamError):
        store.health()


def test_every_read_path_falls_over(pair):
    primary, _, store = pair
    primary.fail_with = UpstreamError("down", service="supabase")

    # Each of these must reach the secondary rather than propagate.
    assert store.get_learner("2") is not None
    assert store.list_sessions("1") == []
    assert store.get_session("1", 1) is None
    assert store.list_pieces() == []
    assert store.get_piece("1") is None
    assert store.list_works("1") == []


def test_close_releases_both_stores(pair):
    primary, secondary, store = pair

    store.close()

    assert primary.closed and secondary.closed
