"""The hard gate on names.

The rule under test is uncomfortable on purpose: a partial match never
resolves, even when it is the only one. These tests exist to stop a future
change from making it "helpful".
"""

from __future__ import annotations

import pytest

from baton.domain.models import Learner
from baton.domain.resolve import normalise, resolve_learner, resolve_learner_loose
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
    """ "Bruno" matches exactly one person today. It still must not resolve:
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
    the keyboard; without NFC folding an exact match becomes an ambiguity.

    Thai stacks a vowel below and a tone mark above the same base letter, and
    the two can be typed in either order. They look identical on screen and
    compare unequal as strings, which is a name resolving on one keyboard and
    raising an ambiguity prompt on another.
    """
    stored = "ปุ่ม"  # vowel below, then tone mark: canonical order
    typed = "ปุ่ม"  # tone mark first, as another keyboard sends it

    assert typed != stored, "the two spellings must really differ, or this proves nothing"
    assert normalise(typed) == normalise(stored)
    assert resolve_learner(typed, [Learner(id="9", name=stored)]).id == "9"


# -- the booking relaxation --------------------------------------------------


def test_booking_resolves_a_unique_partial_and_says_so():
    """The relaxation booking gets and nothing else does: one substring match
    resolves, and the note travels with the booking so the match is announced
    rather than discovered later."""
    learner, note = resolve_learner_loose("Bruno", PEOPLE)

    assert learner.id == "2"
    assert "Bruno Castell" in note


def test_booking_still_refuses_an_ambiguous_partial():
    """Two มานะ. Widening what counts as a match must never widen what counts
    as one answer."""
    with pytest.raises(NeedsHumanError):
        resolve_learner_loose("มานะ", PEOPLE)


def test_booking_still_refuses_nobody():
    with pytest.raises(NeedsHumanError):
        resolve_learner_loose("Zebedee", PEOPLE)


def test_an_exact_or_alias_match_relaxes_nothing():
    learner, note = resolve_learner_loose("Ada Whitfield", PEOPLE)

    assert (learner.id, note) == ("1", "")

    learner, note = resolve_learner_loose("ada", PEOPLE, aliases={"ada": "Ada Whitfield"})

    assert (learner.id, note) == ("1", "")


def test_a_duplicated_recorded_name_still_needs_a_human():
    twins = [Learner(id="1", name="Sam Reed"), Learner(id="2", name="Sam Reed")]

    with pytest.raises(NeedsHumanError):
        resolve_learner_loose("Sam", twins)


# -- learners who stopped studying ---------------------------------------------


# Named for the booking that prompted the feature: "Jee" kept being offered
# next to a learner who left years ago.
GONE = [
    Learner(id="1", name="น้องจี", instrument="guitar"),
    Learner(id="2", name="น้องเจี้ยนซี", instrument="guitar", is_active=False),
    Learner(id="3", name="Jee Wongsakorn", instrument="drums"),
]


def test_a_learner_is_active_by_default():
    """The unmapped-profile compatibility case: a Learner built without a
    status reads active, so a studio that never adopted the column keeps
    exactly the matching behaviour it had before."""
    assert Learner(id="1", name="Ada Whitfield").is_active is True


def test_exact_match_on_an_inactive_learner_still_resolves():
    """History stays reachable: the full name of someone who left still
    resolves, so their records can be read and a returning student can be
    booked without re-activating anything first."""
    assert resolve_learner("น้องเจี้ยนซี", GONE).id == "2"


def test_an_alias_to_an_inactive_learner_still_resolves():
    resolved = resolve_learner("jas", GONE, aliases={"jas": "น้องเจี้ยนซี"})

    assert resolved.id == "2"


def test_partial_candidates_exclude_inactive_learners():
    """The case the whole feature exists for: "จี" must stop offering the
    learner who left as an equal choice next to the ones still studying."""
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("จี", GONE)

    assert [c["name"] for c in excinfo.value.candidates] == ["น้องจี"]


def test_partial_matches_that_are_all_inactive_are_named_in_the_remedy():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("เจี้ยน", GONE)

    assert excinfo.value.candidates == []
    assert "น้องเจี้ยนซี" in (excinfo.value.remedy or "")
    assert "no longer study here" in (excinfo.value.remedy or "")


def test_an_unknown_name_offers_only_active_learners():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("Zebedee", GONE)

    assert [c["name"] for c in excinfo.value.candidates] == ["Jee Wongsakorn", "น้องจี"]


def test_duplicate_recorded_names_still_list_both_when_one_is_inactive():
    twins = [
        Learner(id="1", name="Sam Reed"),
        Learner(id="2", name="Sam Reed", is_active=False),
    ]

    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner("Sam Reed", twins)

    assert len(excinfo.value.candidates) == 2


def test_inactive_note_names_the_learner():
    from baton.domain.resolve import inactive_note

    assert inactive_note(GONE[0]) == ""
    assert inactive_note(GONE[1]) == "น้องเจี้ยนซี is recorded as no longer studying"


def test_booking_partial_lands_on_one_inactive_learner_and_says_so():
    """A former learner booking a one-off lesson back is exactly the case the
    studio wants to serve; the note keeps the operator in on it."""
    learner, note = resolve_learner_loose("เจี้ยน", GONE)

    assert learner.id == "2"
    assert "เจี้ยน" in note
    assert "no longer studying" in note


def test_booking_exact_on_an_inactive_learner_carries_the_inactive_note():
    learner, note = resolve_learner_loose("น้องเจี้ยนซี", GONE)

    assert (learner.id, "no longer studying" in note) == ("2", True)


def test_booking_partial_on_one_active_one_inactive_still_refuses():
    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_learner_loose("จี", GONE)

    assert [c["name"] for c in excinfo.value.candidates] == ["น้องจี"]
