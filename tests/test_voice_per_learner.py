"""Two learners, two prompts — and two differently-named goal sections.

The learners table has always carried what separates one learner's summary
from another's: a tone, an instrument, whether there is an instrument at
home, and (in the studio this was built for) a per-learner prompt level in a
column of its own. This file holds the ones that change what the family
reads, rather than only what the model is told.

The no-instrument case is the sharp one. A learner with nothing at home was
still getting a section headed "Practice goals" and a message line labelled
"Practice", because the only thing their record changed was one sentence of
prompt. The heading contradicted the lesson underneath it, and the phrase
list refused the one honest thing left to write — what the next lesson will
work towards.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.docs.base import DocStatus
from baton.adapters.fakes import FakeDocStore
from baton.cli.app import run
from baton.exits import Exit

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

# Bruno (id 2) has no instrument at home; Clara (id 3) has one.
WITHOUT_INSTRUMENT = "Bruno Castell"
WITH_INSTRUMENT = "Clara Nguyen"

SUMMARY = {
    "overview": ["Kept time through the whole verse."],
    "covered": [{"topic": "Backbeat", "detail": "Hi-hat with the count"}],
    "focus": [{"issue": "Rushing the fill", "fix": "Count it out loud"}],
    "goals": ["Count the backbeat out loud for five minutes a day"],
    "short_summary": {
        "covered": "Backbeat with the hi-hat",
        "progress": "Kept time through the verse",
        "homework": "Count the backbeat out loud",
    },
}


@pytest.fixture
def studio(profile, monkeypatch):
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    # The studio-specific column `learner add --prompt-level` writes to, as a
    # deployed profile carries it: Baton's own migration knows nothing about it.
    connection.execute("ALTER TABLE learners ADD COLUMN prompt_level INTEGER")
    connection.execute("UPDATE learners SET prompt_level = 5 WHERE id = 2")
    connection.execute("UPDATE learners SET prompt_level = 9 WHERE id = 3")
    connection.commit()
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            labels:
              learner: student
              session: lesson
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
              fields:
                learner:
                  prompt_level: prompt_level
            docs:
              driver: notion
              statuses:
                done: Complete
                in_progress: In progress
            summary:
              prompt_levels:
                "5": Level five reads the notes themselves; name the exercise.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    fake = FakeDocStore(
        statuses={
            "doc-bruno-01": DocStatus(doc_id="doc-bruno-01", status="In progress"),
            "doc-bruno-02": DocStatus(doc_id="doc-bruno-02", status="Not started"),
            "doc-clara-01": DocStatus(doc_id="doc-clara-01", status="In progress"),
        }
    )
    for module in ("cmd_learner", "cmd_lesson"):
        monkeypatch.setattr(f"baton.cli.{module}.open_docs", lambda _config: fake)
    return profile


def call(studio, *args):
    return run(["--profile", str(studio), "--json", "lesson", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def contract(studio, capsys, name):
    assert call(studio, "stage", name, "--session", "1", "--context", "notes") == Exit.OK
    capsys.readouterr()
    assert call(studio, "contract", name) == Exit.OK
    return out(capsys)


def ingest(studio, capsys, name, summary):
    code = call(studio, "ingest", name, "--json-text", json.dumps(summary))
    return code, out(capsys)


def goals(summary, *entries):
    return {**summary, "goals": list(entries)}


# -- what the model is told -------------------------------------------------


def test_a_learner_with_nothing_at_home_is_told_what_the_section_is_called(studio, capsys):
    payload = contract(studio, capsys, WITHOUT_INSTRUMENT)
    instructions = payload["instructions"]

    assert any("no instrument at home" in line for line in instructions)
    assert any("Goals for next lesson" in line for line in instructions)


def test_a_learner_who_practises_at_home_hears_none_of_that(studio, capsys):
    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]

    assert not any("no instrument at home" in line for line in instructions)
    assert not any("Goals for next lesson" in line for line in instructions)


def test_the_prompt_level_column_reaches_the_model(studio, capsys):
    """It has been written by `learner add --prompt-level` and read by nothing
    since the port: the studio's own scale of how much the summary should
    explain, stored per learner and never once used."""
    instructions = contract(studio, capsys, WITHOUT_INSTRUMENT)["instructions"]

    assert any("Level five reads the notes themselves" in line for line in instructions)


def test_a_level_the_profile_does_not_describe_says_nothing(studio, capsys):
    """Clara is level 9, which this profile never defines. The same stance as
    an unrecognised tone: a studio that invented a number has not yet said
    what it means, and guessing would be inventing a teaching voice."""
    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]

    assert not any("Level five" in line for line in instructions)


# -- what the validator accepts ---------------------------------------------


def test_next_lesson_goals_are_accepted_when_there_is_nothing_at_home(studio, capsys):
    contract(studio, capsys, WITHOUT_INSTRUMENT)
    code, payload = ingest(
        studio,
        capsys,
        WITHOUT_INSTRUMENT,
        goals(SUMMARY, "Listen to the recording twice", "Next lesson: start the fill slowly"),
    )

    assert code == Exit.OK
    assert payload["has_summary"] is True


def test_next_lesson_goals_are_still_refused_for_a_learner_who_can_practise(studio, capsys):
    contract(studio, capsys, WITH_INSTRUMENT)
    code, payload = ingest(
        studio, capsys, WITH_INSTRUMENT, goals(SUMMARY, "Next lesson: start the fill slowly")
    )

    assert code == Exit.CONTRACT
    assert any(item["path"] == "/goals/0" for item in payload["details"]["violations"])


def test_an_attitude_is_refused_however_the_learner_practises(studio, capsys):
    """ "Try harder" cannot be ticked off by anyone. The split is about where a
    goal happens, not about lowering the bar for one group of learners."""
    for name in (WITHOUT_INSTRUMENT, WITH_INSTRUMENT):
        contract(studio, capsys, name)
        code, payload = ingest(studio, capsys, name, goals(SUMMARY, "Try harder with the count"))

        assert code == Exit.CONTRACT, name
        assert payload["details"]["violations"], name


# -- what the family reads --------------------------------------------------


def test_the_section_and_the_message_line_are_renamed_together(studio, capsys):
    contract(studio, capsys, WITHOUT_INSTRUMENT)
    assert ingest(studio, capsys, WITHOUT_INSTRUMENT, SUMMARY)[0] == Exit.OK

    assert call(studio, "render", WITHOUT_INSTRUMENT, "--format", "blocks") == Exit.OK
    headings = [
        block["heading_2"]["rich_text"][0]["text"]["content"]
        for block in out(capsys)["blocks"]
        if block.get("type") == "heading_2"
    ]
    assert "Goals for next lesson" in headings
    assert "Practice goals" not in headings

    assert call(studio, "render", WITHOUT_INSTRUMENT, "--format", "message") == Exit.OK
    message = out(capsys)["message"]
    assert "Next lesson: Count the backbeat out loud" in message
    assert "Practice:" not in message


def test_a_learner_with_an_instrument_keeps_the_usual_wording(studio, capsys):
    contract(studio, capsys, WITH_INSTRUMENT)
    assert ingest(studio, capsys, WITH_INSTRUMENT, SUMMARY)[0] == Exit.OK

    assert call(studio, "render", WITH_INSTRUMENT, "--format", "blocks") == Exit.OK
    headings = [
        block["heading_2"]["rich_text"][0]["text"]["content"]
        for block in out(capsys)["blocks"]
        if block.get("type") == "heading_2"
    ]
    assert "Practice goals" in headings

    assert call(studio, "render", WITH_INSTRUMENT, "--format", "message") == Exit.OK
    assert "Practice: Count the backbeat out loud" in out(capsys)["message"]


def test_the_published_page_and_the_stored_message_carry_the_new_wording(studio, capsys):
    """The message is composed at publish time and stored, so the substitution
    has to happen there too — `send` reads the record, not the learner."""
    contract(studio, capsys, WITHOUT_INSTRUMENT)
    assert ingest(studio, capsys, WITHOUT_INSTRUMENT, SUMMARY)[0] == Exit.OK
    assert call(studio, "publish", WITHOUT_INSTRUMENT) == Exit.OK
    capsys.readouterr()

    record = json.loads((studio / "state" / "published" / "2-1.json").read_text(encoding="utf-8"))
    assert "Next lesson: Count the backbeat out loud" in record["short_message"]
    assert "Goals for next lesson" in {entry["text"] for entry in record["blocks"]}


# -- a tone that does not set homework --------------------------------------
#
# `casual` in the studio this was built for means "learning for fun": no
# homework, and `goals` written as what the next lesson will reach for. That is
# the substitution the no-instrument case already made, wanted for a different
# reason — so it is configuration a tone can ask for, not a second branch in
# the code naming tones it cannot know.


def with_tone_overrides(studio: Path, block: str) -> None:
    """Append `summary.tone_overrides` to the profile the fixture wrote."""
    path = studio / "baton.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + textwrap.indent(textwrap.dedent(block).strip() + "\n", "  "),
        encoding="utf-8",
    )


EXAM_OVERRIDE = """
tone_overrides:
  exam:
    section: Exam targets
    message_label: Next up
"""


def test_a_tone_can_rename_the_goals_section_for_a_learner_who_has_an_instrument(studio, capsys):
    """Nothing about Clara's record says "no instrument"; the rename is her
    tone asking for it, which nothing but `has_instrument` could do before."""
    with_tone_overrides(studio, EXAM_OVERRIDE)

    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]

    assert any("Exam targets" in line for line in instructions)
    assert not any("no instrument at home" in line for line in instructions)


def test_the_renamed_section_also_flips_the_rule_that_calls_goals_homework(studio, capsys):
    """The standing rule says `goals` is what to practise at home. Under a
    heading that says otherwise it contradicts both the heading and the tone,
    and the model has to pick one."""
    with_tone_overrides(studio, EXAM_OVERRIDE)

    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]

    assert any("aim for by the next lesson" in line for line in instructions)
    assert not any("practise at home" in line for line in instructions)


def test_without_an_override_goals_is_still_homework(studio, capsys):
    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]

    assert any("practise at home" in line for line in instructions)
    assert not any("aim for by the next lesson" in line for line in instructions)


def test_a_tone_nobody_wrote_an_override_for_changes_nothing(studio, capsys):
    """The same refusal to guess that `summary.tones` makes: a tone with no
    override is a tone whose studio has not asked for one."""
    with_tone_overrides(
        studio,
        """
        tone_overrides:
          casual:
            section: Casual targets
        """,
    )

    instructions = contract(studio, capsys, WITH_INSTRUMENT)["instructions"]  # exam

    assert not any("Casual targets" in line for line in instructions)
    assert any("practise at home" in line for line in instructions)


def test_no_instrument_at_home_outranks_the_tone_override(studio, capsys):
    """Bruno is `casual` *and* has nothing at home. A tone is a choice about
    how to teach; an empty room is a fact about what he can do. The fact wins,
    so the two never race to name the same heading."""
    with_tone_overrides(
        studio,
        """
        tone_overrides:
          casual:
            section: Casual targets
            message_label: Casually
        """,
    )

    instructions = contract(studio, capsys, WITHOUT_INSTRUMENT)["instructions"]

    assert any("Goals for next lesson" in line for line in instructions)
    assert not any("Casual targets" in line for line in instructions)


def test_the_family_reads_the_tones_wording_too(studio, capsys):
    """The rename has to survive to the page and the message, not stop at the
    prompt — the same journey the no-instrument wording already makes."""
    with_tone_overrides(studio, EXAM_OVERRIDE)
    contract(studio, capsys, WITH_INSTRUMENT)
    assert ingest(studio, capsys, WITH_INSTRUMENT, SUMMARY)[0] == Exit.OK

    assert call(studio, "render", WITH_INSTRUMENT, "--format", "blocks") == Exit.OK
    headings = [
        block["heading_2"]["rich_text"][0]["text"]["content"]
        for block in out(capsys)["blocks"]
        if block.get("type") == "heading_2"
    ]
    assert "Exam targets" in headings
    assert "Practice goals" not in headings

    assert call(studio, "render", WITH_INSTRUMENT, "--format", "message") == Exit.OK
    message = out(capsys)["message"]
    assert "Next up: Count the backbeat out loud" in message
    assert "Practice:" not in message
