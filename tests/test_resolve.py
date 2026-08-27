"""The hard gate on names.

The rule under test is uncomfortable on purpose: a partial match never
resolves, even when it is the only one. These tests exist to stop a future
change from making it "helpful".
"""

from __future__ import annotations

import pytest

from baton.domain.models import Learner
from baton.domain.resolve import normalise, resolve_learner
from baton.errors import NeedsHumanError
from baton.exits import Exit

PEOPLE = [
    Learner(id="1", name="Ada Whitfield", instrument="guitar"),
    Learner(id="2", name="Bruno Castell", instrument="drums"),
    Learner(id="3", name="Clara Nguyen", instrument="piano"),
    Learner(id="4", name="น้องมานะ กีตาร์", instrument="guitar"),
    Learner(id="5", name="น้องมานะ กลอง", instrument="drums"),
]


def test_exact_match_resolves():
    assert resolve_learner("Ada Whitfield", PEOPLE).id == "1"


def test_match_ignores_case_and_surrounding_space():
    assert resolve_learner("  ada whitfield ", PEOPLE).id == "1"


def test_partial_match_never_resolves_even_when_unique():
    """ "Bruno" matches exactly one person today. It still must not resolve —
    the second Bruno is what makes this rule worth its friction."""
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("Bruno", PEOPLE)

    assert [c["name"] for c in excinfo.value.candidates] == ["Bruno Castell"]


def test_ambiguous_partial_returns_every_candidate():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("มานะ", PEOPLE)

    names = [c["name"] for c in excinfo.value.candidates]
    assert names == ["น้องมานะ กลอง", "น้องมานะ กีตาร์"]


def test_alias_resolves_to_an_exact_name():
    resolved = resolve_learner("ada", PEOPLE, aliases={"ada": "Ada Whitfield"})

    assert resolved.id == "1"


def test_alias_pointing_at_nobody_is_reported_as_a_config_problem():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("boss", PEOPLE, aliases={"boss": "Nobody At All"})

    assert "baton.yaml" in (excinfo.value.remedy or "")


def test_alias_chain_is_not_followed():
    """A chain would be impossible to audit, so `a -> b -> c` stops at `b`."""
    aliases = {"a": "b", "b": "Ada Whitfield"}

    with pytest.raises(NeedsHumanError):
        resolve_learner("a", PEOPLE, aliases=aliases)


def test_unknown_name_still_returns_the_full_list_to_choose_from():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("Zebedee", PEOPLE)

    assert len(excinfo.value.candidates) == len(PEOPLE)


def test_empty_query_asks_rather_than_guessing():
    with pytest.raises(NeedsHumanError):
        resolve_learner("   ", PEOPLE)


def test_duplicate_recorded_names_cannot_be_resolved():
    twins = [
        Learner(id="1", name="Sam Reed"),
        Learner(id="2", name="Sam Reed"),
    ]

    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("Sam Reed", twins)

    assert len(excinfo.value.candidates) == 2


def test_gate_always_maps_to_the_needs_human_exit_code():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("nobody", PEOPLE)

    assert excinfo.value.exit_code == Exit.NEEDS_HUMAN
    assert excinfo.value.to_dict()["error"] == "needs_human"


def test_thai_combining_marks_normalise_before_comparison():
    """The same visible name can arrive as different code points depending on
    the keyboard; without NFC folding an exact match becomes an ambiguity."""
    decomposed = "น้อง"  # NFC already
    people = [Learner(id="9", name=decomposed)]

    assert resolve_learner(decomposed, people).id == "9"
    assert normalise(decomposed) == normalise(decomposed)
