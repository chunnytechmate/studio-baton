"""Validating content a model wrote, before any of it reaches a document.

This is the boundary the whole design turns on. Baton scripts everything it
can, and the one thing it cannot script is the sentence a teacher would have
written about a lesson. So the model returns *data* against a JSON Schema, and
Baton renders the document itself.

Schema validation is necessary but not sufficient: "no emoji", "no links", and
"at most five lines" are not expressible in JSON Schema, and they are precisely
the rules a small local model ignores when they are stated in prose. Those are
checked here in code.

A rejection is never partial. Nothing is stored, nothing is published, and the
caller gets every violation at once with a JSON Pointer to each — so an agent
can fix them in one pass rather than discovering them one re-run at a time.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from ..errors import ContractError

SCHEMA_DIR = Path(__file__).resolve().parent

LESSON_SUMMARY = "lesson_summary"

#: Matches a bare URL or a markdown link. A link in a chat message is either
#: dead (the recipient cannot click a Notion URL they have no access to) or
#: duplicates one Baton attaches itself.
_LINK = re.compile(r"(https?://|www\.)\S+|\[[^\]]+\]\([^)]+\)", re.IGNORECASE)


@lru_cache(maxsize=8)
def load_schema(name: str) -> dict[str, Any]:
    """Read a packaged JSON Schema by name."""
    path = SCHEMA_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise ContractError(f"No schema named `{name}` is packaged with this build.")
    return json.loads(path.read_text(encoding="utf-8"))


def has_emoji(text: str) -> bool:
    """Whether the text contains a pictographic character.

    Uses Unicode categories rather than a fixed list: a hand-maintained emoji
    list is out of date the month it is written, and the check is meant to hold
    for text in any language.
    """
    for char in text:
        if unicodedata.category(char) == "So":  # Symbol, other
            return True
        if 0x1F000 <= ord(char) <= 0x1FAFF:
            return True
        if 0x2600 <= ord(char) <= 0x27BF:
            return True
    return False


def _violation(path: str, reason: str, hint: str = "") -> dict[str, str]:
    entry = {"path": path, "reason": reason}
    if hint:
        entry["hint"] = hint
    return entry


def _pointer(error: jsonschema.ValidationError) -> str:
    """A JSON Pointer to the offending value, so a caller can locate it exactly."""
    return "/" + "/".join(str(part) for part in error.absolute_path) if error.absolute_path else "/"


def validate_schema(payload: Any, schema_name: str) -> list[dict[str, str]]:
    """Check ``payload`` against a packaged schema.

    Returns:
        Every violation found, in document order. Empty means valid.
    """
    schema = load_schema(schema_name)
    validator = jsonschema.Draft202012Validator(schema)
    return [
        _violation(_pointer(error), error.message)
        for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    ]


def validate_short_summary(
    short: dict[str, Any],
    *,
    max_lines: int = 5,
    allow_emoji: bool = False,
    allow_links: bool = False,
) -> list[dict[str, str]]:
    """Apply the rules JSON Schema cannot express to the parent-facing message.

    Args:
        short: The ``short_summary`` object.
        max_lines: Total lines the rendered message may occupy.
        allow_emoji: Permit pictographic characters.
        allow_links: Permit URLs and markdown links.

    Returns:
        Violations, empty when the message is acceptable.
    """
    violations: list[dict[str, str]] = []

    for field in ("covered", "progress", "homework"):
        value = short.get(field)
        if not isinstance(value, str) or not value:
            continue
        pointer = f"/short_summary/{field}"

        if not allow_emoji and has_emoji(value):
            violations.append(
                _violation(
                    pointer,
                    "contains emoji, which this profile does not allow in messages",
                    "Write the same thing in words.",
                )
            )
        if not allow_links and _LINK.search(value):
            violations.append(
                _violation(
                    pointer,
                    "contains a link",
                    "Baton attaches the document and recording links itself; "
                    "a link written into the text is either duplicated or dead.",
                )
            )
        if "\n" in value:
            violations.append(
                _violation(
                    pointer,
                    "contains a line break",
                    "Each field is one line. Use the separate fields for separate points.",
                )
            )

    used = sum(1 for field in ("covered", "progress", "homework") if short.get(field))
    if used > max_lines:
        violations.append(
            _violation(
                "/short_summary",
                f"renders to {used} lines, more than the configured maximum of {max_lines}",
                "Shorten it, or raise summary.short_summary.max_lines.",
            )
        )

    return violations


def validate_callouts(payload: dict[str, Any], known_ids: set[str]) -> list[dict[str, str]]:
    """Check every referenced theory callout exists.

    Carried over from the original rule "never invent a callout — look it up".
    Here the lookup is enforced: an id the studio's notes do not contain is a
    rejection, not a silently rendered invention.
    """
    violations = []
    for index, callout_id in enumerate(payload.get("callouts", []) or []):
        if str(callout_id) not in known_ids:
            violations.append(
                _violation(
                    f"/callouts/{index}",
                    f"`{callout_id}` is not in this studio's theory notes",
                    "`lesson contract` lists every id that exists under "
                    "`constraints.available_callout_ids`. Use one of those, or "
                    "drop the callout.",
                )
            )
    return violations


def validate_lesson_summary(
    payload: Any,
    *,
    max_lines: int = 5,
    allow_emoji: bool = False,
    allow_links: bool = False,
    known_callouts: set[str] | None = None,
) -> dict[str, Any]:
    """Validate a complete lesson summary, or raise with every violation.

    Args:
        payload: The parsed JSON the model produced.
        max_lines: Line budget for the parent-facing message.
        allow_emoji: Permit emoji in that message.
        allow_links: Permit links in that message.
        known_callouts: Theory ids that exist. ``None`` skips the check, for
            a studio that keeps no theory notes.

    Returns:
        The payload, unchanged, once it is acceptable.

    Raises:
        ContractError: With every violation found. Nothing is stored.
    """
    if not isinstance(payload, dict):
        raise ContractError(
            f"A lesson summary must be a JSON object, got {type(payload).__name__}.",
            violations=[_violation("/", "not an object")],
        )

    violations = validate_schema(payload, LESSON_SUMMARY)
    if not violations:
        # Only worth running the finer checks once the shape is right;
        # otherwise they report noise about fields that are missing anyway.
        short = payload.get("short_summary") or {}
        violations.extend(
            validate_short_summary(
                short,
                max_lines=max_lines,
                allow_emoji=allow_emoji,
                allow_links=allow_links,
            )
        )
        if known_callouts is not None:
            violations.extend(validate_callouts(payload, known_callouts))

    if violations:
        raise ContractError(
            f"The lesson summary does not match the required structure "
            f"({len(violations)} problem{'s' if len(violations) != 1 else ''}).",
            violations=violations,
        )
    return payload


__all__ = [
    "LESSON_SUMMARY",
    "has_emoji",
    "load_schema",
    "validate_callouts",
    "validate_lesson_summary",
    "validate_schema",
    "validate_short_summary",
]
