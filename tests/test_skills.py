"""The skills must stay true to the CLI, and stay free of raw API calls.

Documentation drifts from code silently; a skill that names a flag which no
longer exists sends an agent into a retry loop against a usage error. These
tests fail instead.

The second half is the load-bearing one. The whole point of the CLI is that a
model never assembles an API call, so a skill that reintroduced `curl` or
`python3 -c` would quietly undo it, and would look perfectly reasonable in
review.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from baton.cli.app import build_parser

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))

#: Ways a model could be led back into hand-assembling a call.
FORBIDDEN = [
    (re.compile(r"\bcurl\b"), "a raw HTTP call"),
    (re.compile(r"python3?\s+-c\b"), "an inline Python snippet"),
    (re.compile(r"\bapi\.notion\.com\b"), "a Notion API URL"),
    (re.compile(r"\bapi\.line\.me\b"), "a LINE API URL"),
    (re.compile(r"\bgoogleapis\.com\b"), "a Google API URL"),
    (re.compile(r'"Notion-Version"'), "a Notion API header"),
    (re.compile(r"\brequests\.post\b"), "a direct HTTP client call"),
]

#: `baton <command> <subcommand>` as written in a skill.
_INVOCATION = re.compile(r"^\s*(?:\$\s*)?baton\s+([a-z-]+)(?:\s+([a-z-]+))?", re.MULTILINE)


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    """Every command and subcommand the CLI actually implements."""
    found: dict[str, set[str]] = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            children: set[str] = set()
            for child_action in sub._actions:
                if isinstance(child_action, argparse._SubParsersAction):
                    children |= set(child_action.choices)
            found[name] = children
    return found


COMMANDS = _subcommands(build_parser())

#: Flags every skill may use without them being command-specific.
GLOBAL_FLAGS = {"--json", "--quiet", "--profile"}


def test_there_is_a_skill_for_each_pipeline():
    assert {path.parent.name for path in SKILL_FILES} == {
        "course-archive",
        "lesson-summarizer",
        "prep-report",
        "quick-notes",
        "send-lesson",
        "send-recording",
        "student-lookup",
        "studio-calendar",
        "studio-songs",
        "video-pipeline",
    }


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_frontmatter_names_and_describes_the_skill(path):
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---\n"), "a skill needs YAML frontmatter"
    frontmatter = text.split("---", 2)[1]
    assert re.search(r"^name:\s*\S+", frontmatter, re.MULTILINE)
    description = re.search(r'^description:\s*"(.+)"', frontmatter, re.MULTILINE)
    assert description, "a skill needs a description for the harness to match on"
    # The description is how a harness decides to load the skill at all, so it
    # has to say *when* to reach for it, not only what it is. Matching the
    # property rather than one phrasing: "Use when", "Use after ... when", and
    # "Use for" all state it.
    assert re.search(r"\bUse\b", description.group(1)), (
        "the description must say when to use the skill"
    )


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_the_skill_name_matches_its_directory(path):
    frontmatter = path.read_text(encoding="utf-8").split("---", 2)[1]
    name = re.search(r"^name:\s*(\S+)", frontmatter, re.MULTILINE).group(1)

    assert name == path.parent.name


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_command_a_skill_names_exists(path):
    text = path.read_text(encoding="utf-8")

    for command, subcommand in _INVOCATION.findall(text):
        assert command in COMMANDS, f"`baton {command}` is not a command"
        if subcommand and COMMANDS[command]:
            assert subcommand in COMMANDS[command], (
                f"`baton {command} {subcommand}` is not a subcommand of `{command}`"
            )


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_no_skill_reintroduces_a_raw_api_call(path):
    """The reason the CLI exists. A skill with a curl in it undoes all of it."""
    text = path.read_text(encoding="utf-8")

    for pattern, description in FORBIDDEN:
        assert not pattern.search(text), f"skill contains {description}"


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_skill_documents_what_to_do_about_exit_codes(path):
    """An agent branches on the number. A skill that does not say what the
    numbers mean leaves it guessing."""
    text = path.read_text(encoding="utf-8")

    assert "Exit codes" in text
    assert "`0`" in text


@pytest.mark.parametrize(
    "path",
    [
        p
        for p in SKILL_FILES
        if p.parent.name in {"send-lesson", "send-recording", "studio-calendar"}
    ],
    ids=lambda p: p.parent.name,
)
def test_gated_skills_say_the_block_cannot_be_overridden(path):
    """These can refuse a send outright. Each must tell the agent not to look
    for a way around it, because looking is exactly what an agent does next."""
    text = path.read_text(encoding="utf-8")

    assert "`5`" in text
    assert "Do not retry" in text or "do not retry" in text


def test_the_send_skill_forbids_sending_by_hand():
    """The original's most important guard rail, carried into the skill."""
    text = (SKILLS_DIR / "send-lesson" / "SKILL.md").read_text(encoding="utf-8")

    assert "do not send the message by hand" in text.lower()
    assert "no override flag" in text.lower() or "there is no override" in text.lower()


def test_the_readme_explains_installation_and_the_exit_contract():
    text = (SKILLS_DIR / "README.md").read_text(encoding="utf-8")

    assert "BATON_PROFILE" in text
    for code in ("`0`", "`3`", "`4`", "`5`", "`8`"):
        assert code in text


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skills_stay_short_enough_to_be_read(path):
    """A skill is loaded into context every time it matches. The original ran
    to 400 lines of prose, which is how its rules stopped being followed."""
    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) < 120, f"{path.parent.name} is {len(lines)} lines"


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_invocation_in_a_skill_actually_parses(path):
    """Names existing is not enough: a skill that documents a flag in a
    position the parser rejects sends an agent into a loop against exit 2.
    Every command line in the skills is parsed for real."""
    import contextlib
    import io
    import shlex

    parser = build_parser()
    text = path.read_text(encoding="utf-8")

    for line in text.splitlines():
        stripped = line.strip().removeprefix("$ ")
        if not stripped.startswith("baton "):
            continue
        # Skip continuations and placeholder-only lines; the point is flag
        # placement, not whether a made-up name resolves.
        if stripped.endswith("\\"):
            continue
        try:
            # shlex drops the trailing `# comment` that makes an example
            # readable, so the line parses as it would when actually typed.
            argv = shlex.split(stripped, comments=True)[1:]
        except ValueError:
            continue

        with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(argv)
            continue
        raise AssertionError(f"{path.parent.name}: `{stripped}` does not parse")
