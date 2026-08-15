"""The boundary where model output is accepted or refused.

Every test here is a way a model gets it wrong. The schema catches shape; the
code catches the rules a schema cannot express — and those are the ones a small
local model breaks, because they are the ones usually written as prose.
"""

from __future__ import annotations

import copy

import pytest

from baton.contracts import (
    has_emoji,
    validate_lesson_summary,
    validate_short_summary,
)
from baton.errors import ContractError
from baton.exits import Exit

VALID = {
    "overview": ["Steady progress on the B section; the tempo held this week."],
    "covered": [
        {"topic": "Blackbird, bars 9-16", "detail": "Thumb-and-finger pattern"},
        {"topic": "Chord changes", "notation": "Em7: 022033"},
    ],
    "focus": [
        {"issue": "The change to C is late", "fix": "Practise the change alone, four beats each"}
    ],
    "goals": ["Play bars 9-16 with the backing track at 80bpm"],
    "short_summary": {
        "covered": "Blackbird, bars 9 to 16",
        "progress": "Held the tempo without stopping",
        "homework": "Bars 9-16 with the backing track",
    },
}


def valid(**overrides):
    payload = copy.deepcopy(VALID)
    payload.update(overrides)
    return payload


def test_a_well_formed_summary_is_accepted():
    assert validate_lesson_summary(valid()) == valid()


# -- shape ------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["overview", "covered", "goals", "short_summary"])
def test_a_missing_required_section_is_rejected(missing):
    payload = valid()
    del payload[missing]

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload)

    assert any(missing in v["reason"] for v in excinfo.value.violations)


def test_an_unknown_top_level_key_is_rejected():
    """A model that invents a section would otherwise have it silently dropped,
    and the teacher would never learn the content went nowhere."""
    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(valid(notes="something I made up"))

    assert excinfo.value.violations


def test_a_difficulty_without_a_fix_is_rejected():
    """An issue with no fix is an observation, not teaching."""
    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(valid(focus=[{"issue": "Rushing the chorus"}]))

    assert any("fix" in v["reason"] for v in excinfo.value.violations)


def test_an_empty_goals_list_is_rejected():
    with pytest.raises(ContractError):
        validate_lesson_summary(valid(goals=[]))


def test_a_non_object_payload_is_rejected_clearly():
    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(["not", "an", "object"])

    assert "object" in str(excinfo.value)


def test_violations_carry_a_pointer_to_the_offending_field():
    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(valid(goals=[""]))

    assert any(v["path"].startswith("/goals") for v in excinfo.value.violations)


def test_every_violation_is_reported_at_once():
    """One re-run per problem is how a model gets stuck in a loop."""
    payload = valid(goals=[], overview=[])
    del payload["covered"]

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload)

    assert len(excinfo.value.violations) >= 3


def test_a_rejection_maps_to_the_contract_exit_code():
    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary({})

    assert excinfo.value.exit_code == Exit.CONTRACT
    assert excinfo.value.to_dict()["error"] == "contract"


# -- the rules a schema cannot express ---------------------------------------


def test_emoji_in_the_parent_message_is_rejected():
    """The rule that used to be a sentence in a prompt."""
    payload = valid()
    payload["short_summary"]["progress"] = "Great work today 🎉"

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload)

    assert any("emoji" in v["reason"] for v in excinfo.value.violations)


def test_a_link_in_the_parent_message_is_rejected():
    payload = valid()
    payload["short_summary"]["covered"] = "See https://example.invalid/notes"

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload)

    assert any("link" in v["reason"] for v in excinfo.value.violations)


def test_a_markdown_link_is_caught_too():
    payload = valid()
    payload["short_summary"]["covered"] = "See [the notes](https://example.invalid)"

    with pytest.raises(ContractError):
        validate_lesson_summary(payload)


def test_a_line_break_inside_a_message_field_is_rejected():
    payload = valid()
    payload["short_summary"]["progress"] = "Held tempo\nand kept time"

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload)

    assert any("line break" in v["reason"] for v in excinfo.value.violations)


def test_emoji_can_be_allowed_by_configuration():
    payload = valid()
    payload["short_summary"]["progress"] = "Great work 🎉"

    assert validate_lesson_summary(payload, allow_emoji=True)


def test_links_can_be_allowed_by_configuration():
    payload = valid()
    payload["short_summary"]["covered"] = "See https://example.invalid/notes"

    assert validate_lesson_summary(payload, allow_links=True)


def test_the_line_budget_is_enforced():
    violations = validate_short_summary(
        {"covered": "a", "progress": "b", "homework": "c"}, max_lines=2
    )

    assert any("more than the configured maximum" in v["reason"] for v in violations)


@pytest.mark.parametrize(
    "text",
    ["🎉", "great 🥁 work", "✅ done", "⚠️ careful"],
)
def test_emoji_detection_covers_the_common_ranges(text):
    assert has_emoji(text) is True


@pytest.mark.parametrize("text", ["plain words", "ตีกลองได้ดีขึ้น", "80bpm — steady", "C→G"])
def test_emoji_detection_does_not_fire_on_ordinary_text(text):
    """Thai text and typographic arrows are not emoji; treating them as such
    would make the check unusable for the studio it was written for."""
    assert has_emoji(text) is False


# -- theory callouts ---------------------------------------------------------


def test_a_callout_id_that_does_not_exist_is_rejected():
    """The original rule was "never invent a callout, look it up". Here the
    lookup is enforced: an unknown id cannot be rendered as if it were real."""
    payload = valid(callouts=["vibrato", "made-up-technique"])

    with pytest.raises(ContractError) as excinfo:
        validate_lesson_summary(payload, known_callouts={"vibrato", "flam"})

    reasons = [v["reason"] for v in excinfo.value.violations]
    assert any("made-up-technique" in reason for reason in reasons)


def test_known_callout_ids_pass():
    payload = valid(callouts=["vibrato"])

    assert validate_lesson_summary(payload, known_callouts={"vibrato", "flam"})


def test_callouts_are_not_checked_when_a_studio_keeps_no_theory_notes():
    payload = valid(callouts=["anything"])

    assert validate_lesson_summary(payload, known_callouts=None)


# -- the escape hatch --------------------------------------------------------


def test_extra_sections_stay_structured():
    payload = valid(
        extra_sections=[{"heading": "Ensemble notes", "items": ["Counted the band in"]}]
    )

    assert validate_lesson_summary(payload)


def test_an_extra_section_without_items_is_rejected():
    with pytest.raises(ContractError):
        validate_lesson_summary(valid(extra_sections=[{"heading": "Empty"}]))
