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
caller gets every violation at once with a JSON Pointer to each, so an agent
can fix them in one pass rather than discovering them one re-run at a time.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections.abc import Iterable
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

    Carried over from the original rule "never invent a callout: look it up".
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


def _body_strings(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Every sentence the document body will show, each with its JSON Pointer.

    The parent-facing `short_summary` is deliberately absent: it restates the
    document on purpose, and it has its own rules. What is checked here is
    whether the *document* says one thing several times.
    """
    found: list[tuple[str, str]] = []
    for index, line in enumerate(payload.get("overview", []) or []):
        found.append((f"/overview/{index}", str(line)))
    for index, item in enumerate(payload.get("progress", []) or []):
        if isinstance(item, dict):
            found.append((f"/progress/{index}/after", str(item.get("after", ""))))
    for index, entry in enumerate(payload.get("covered", []) or []):
        if not isinstance(entry, dict):
            continue
        for field in ("topic", "detail"):
            if entry.get(field):
                found.append((f"/covered/{index}/{field}", str(entry[field])))
    for index, item in enumerate(payload.get("focus", []) or []):
        if isinstance(item, dict):
            for field in ("issue", "fix"):
                if item.get(field):
                    found.append((f"/focus/{index}/{field}", str(item[field])))
    for index, goal in enumerate(payload.get("goals", []) or []):
        found.append((f"/goals/{index}", str(goal)))
    return [(pointer, text) for pointer, text in found if text.strip()]


def _fold(text: str) -> str:
    """Normalise a sentence for comparison: case, spacing, and punctuation."""
    stripped = "".join(
        char
        for char in unicodedata.normalize("NFC", text)
        if not unicodedata.category(char).startswith("P")
    )
    return " ".join(stripped.casefold().split())


def validate_no_repetition(
    payload: dict[str, Any], *, max_repeats: int = 2, similarity: float = 0.82
) -> list[dict[str, str]]:
    """Refuse a document that says the same thing in too many places.

    Each section of a summary is meant to carry a different *kind* of
    information: what happened, what changed, what is still hard, what to
    practise. When one fact is restated in three of them the page grows without
    telling a family anything more, and the sections stop meaning what their
    headings promise.

    Comparison is on characters rather than words: Thai does not space its
    words, so a word-based measure reads a Thai sentence as one long token and
    matches nothing.

    Args:
        max_repeats: How many places one fact may appear in. Two is a
            restatement; three is padding.
        similarity: How alike two sentences must be to count as the same fact.
    """
    entries = [(pointer, _fold(text)) for pointer, text in _body_strings(payload)]
    entries = [(pointer, folded) for pointer, folded in entries if len(folded) >= 12]

    clusters: list[list[tuple[str, str]]] = []
    for pointer, folded in entries:
        for cluster in clusters:
            if difflib.SequenceMatcher(None, cluster[0][1], folded).ratio() >= similarity:
                cluster.append((pointer, folded))
                break
        else:
            clusters.append([(pointer, folded)])

    violations = []
    for cluster in clusters:
        if len(cluster) <= max_repeats:
            continue
        where = ", ".join(pointer for pointer, _ in cluster[:max_repeats])
        for pointer, _ in cluster[max_repeats:]:
            violations.append(
                _violation(
                    pointer,
                    f"repeats what is already said at {where}",
                    "Each section answers a different question. Cut this, or "
                    "replace it with what that section alone can say.",
                )
            )
    return violations


def _phrase_hits(text: str, phrases: Iterable[str]) -> str:
    """The first listed phrase this text contains, or ""."""
    folded = _fold(text)
    for phrase in phrases:
        candidate = _fold(str(phrase))
        if candidate and candidate in folded:
            return str(phrase)
    return ""


def validate_specific_language(
    payload: dict[str, Any], vague_phrases: Iterable[str]
) -> list[dict[str, str]]:
    """Refuse a verdict where an observation belongs.

    "Did very well" is the safest sentence a model can write and the emptiest:
    it survives any lesson, so it describes none. A family reading it learns
    nothing they could not have assumed, and the teacher who wrote the notes
    gets no credit for what they actually saw.

    A list rather than a cleverer measure, because the phrases that do this are
    few, studio-specific, and known, and a rule a studio can read is one it
    can argue with.
    """
    phrases = [str(phrase) for phrase in vague_phrases if str(phrase).strip()]
    if not phrases:
        return []
    violations = []
    for pointer, text in _body_strings(payload):
        hit = _phrase_hits(text, phrases)
        if hit:
            violations.append(
                _violation(
                    pointer,
                    f"rates the lesson (`{hit}`) instead of describing it",
                    "Say what was observed: what they managed, how much help "
                    "it took, what changed since last time.",
                )
            )
    return violations


def validate_about_the_playing(
    payload: dict[str, Any], trait_language: Iterable[str]
) -> list[dict[str, str]]:
    """Refuse a description of the child where one of the playing belongs.

    "A weak point" names something a learner *is*; "still coming" names
    something a lesson *changes*. The families reading these pages keep them,
    and a word that sounds diagnostic is the one they remember, so the rule is
    not politeness, it is accuracy: a lesson observes playing, and nobody in
    the room assessed the child.

    Separate from :func:`validate_specific_language` because the correction is
    a different one. That rule says be specific; this one says say it about
    the playing.
    """
    phrases = [str(phrase) for phrase in trait_language if str(phrase).strip()]
    if not phrases:
        return []
    violations = []
    for pointer, text in _body_strings(payload):
        hit = _phrase_hits(text, phrases)
        if hit:
            violations.append(
                _violation(
                    pointer,
                    f"describes the learner (`{hit}`) rather than the playing",
                    "Name the skill and where it has got to: what they can do "
                    "unaided, what still needs help, not what they are like.",
                )
            )
    return violations


def validate_practice_goals(
    payload: dict[str, Any], not_practicable: Iterable[str]
) -> list[dict[str, str]]:
    """Refuse a practice goal nobody can practise.

    `goals` renders as the checklist a learner and their family work through
    between lessons. Anything on it that can only happen in the next lesson (
    or that asks for an attitude rather than an action) cannot be ticked off,
    and an uncompletable checklist teaches a family to ignore the list.
    """
    phrases = [str(phrase) for phrase in not_practicable if str(phrase).strip()]
    if not phrases:
        return []
    violations = []
    for index, goal in enumerate(payload.get("goals", []) or []):
        hit = _phrase_hits(str(goal), phrases)
        if hit:
            violations.append(
                _violation(
                    f"/goals/{index}",
                    f"`{hit}` is not something to practise at home",
                    "Write what to do between now and the next lesson, with "
                    "how long or how many times. Anything that happens in the "
                    "lesson itself belongs in `focus`.",
                )
            )
    return violations


def validate_progress(payload: dict[str, Any], *, expected: bool) -> list[dict[str, str]]:
    """Require the progress section once there is something to compare against.

    Not expressible in the schema, which cannot see whether this learner has a
    previous session. The first lesson of a course has nothing to compare and
    is not asked to invent one; every lesson after it is.
    """
    if not expected or (payload.get("progress") or []):
        return []
    return [
        _violation(
            "/progress",
            "is missing, and this session has a previous one to compare with",
            "Name what changed since last time, as `before` and `after`: what "
            "they needed help with then and manage now. If genuinely nothing "
            "changed, say that as the one entry rather than leaving it out.",
        )
    ]


def validate_lesson_summary(
    payload: Any,
    *,
    max_lines: int = 5,
    allow_emoji: bool = False,
    allow_links: bool = False,
    known_callouts: set[str] | None = None,
    expect_progress: bool = False,
    max_repeats: int = 2,
    vague_phrases: Iterable[str] = (),
    trait_language: Iterable[str] = (),
    goals_not_practicable: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate a complete lesson summary, or raise with every violation.

    Args:
        payload: The parsed JSON the model produced.
        max_lines: Line budget for the parent-facing message.
        allow_emoji: Permit emoji in that message.
        allow_links: Permit links in that message.
        known_callouts: Theory ids that exist. ``None`` skips the check, for
            a studio that keeps no theory notes.
        expect_progress: Whether this session has a previous one to compare
            against, which is what makes `progress` required.
        max_repeats: How many places one fact may appear in.
        vague_phrases: Phrases that rate a lesson instead of describing it.
        trait_language: Phrases that describe the learner rather than the
            playing.
        goals_not_practicable: Phrases that make a practice goal impossible to
            do at home.

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
        violations.extend(validate_progress(payload, expected=expect_progress))
        violations.extend(validate_no_repetition(payload, max_repeats=max_repeats))
        violations.extend(validate_specific_language(payload, vague_phrases))
        violations.extend(validate_about_the_playing(payload, trait_language))
        violations.extend(validate_practice_goals(payload, goals_not_practicable))
        violations.sort(key=lambda item: item["path"])

    if violations:
        raise ContractError(
            f"The lesson summary does not match the required structure "
            f"({len(violations)} problem{'s' if len(violations) != 1 else ''}).",
            violations=violations,
        )
    return payload


#: Similarity band of a *misspelling*: close enough to be the same word,
#: different enough not to be it. Below it the text is simply about something
#: else; at 1.0 the spelling is already exact.
NEAR_MISS_RATIO = 0.75

#: Ceiling on the warnings handed back, so one bad summary cannot bury the
#: agent's context under a list of every window it almost matched.
NEAR_MISS_LIMIT = 10


def _strings_with_paths(value: Any, pointer: str = "") -> list[tuple[str, str]]:
    """Every string in the payload, with the JSON Pointer that reached it.

    Unlike :func:`_body_strings` this walks ``short_summary`` too, and goals
    and callouts alike: a spelling rule broken in the message that goes to
    LINE is broken in the message, wherever on the page it sits.
    """
    if isinstance(value, str):
        return [(pointer or "/", value)]
    if isinstance(value, dict):
        found: list[tuple[str, str]] = []
        for key, item in value.items():
            found.extend(_strings_with_paths(item, f"{pointer}/{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(_strings_with_paths(item, f"{pointer}/{index}"))
        return found
    return []


def vocabulary_near_misses(payload: dict[str, Any], vocabulary: Iterable[str]) -> list[str]:
    """Name the places a summary nearly used a pool spelling and did not.

    The third layer of the vocabulary design, and the only one that looks at
    what the model actually wrote. The contract asked for the pool's spellings
    before the model started; if it invented a near-miss anyway, the teacher
    should see it before the page is published, but a warning is where this
    stops. A summary rejected over a spelling is how a pipeline stops being
    used, and the raw teacher's notes are always what wins an argument about
    which spelling was right in the first place.
    """
    terms = [str(term) for term in vocabulary if str(term).strip()]
    if not terms:
        return []
    strings = _strings_with_paths(payload)
    folded_strings = [(pointer, _fold(text)) for pointer, text in strings]

    findings: list[str] = []
    for term in terms:
        folded_term = _fold(term)
        if not folded_term:
            continue
        if any(folded_term in folded for _pointer, folded in folded_strings):
            continue

        # The exact spelling is nowhere, so look for what took its place:
        # a sliding window over each string, at the lengths one inserted,
        # dropped, or doubled character would produce.
        examples: list[str] = []
        for pointer, folded in folded_strings:
            for size in range(len(folded_term) - 1, len(folded_term) + 2):
                if size < 2:
                    continue
                for start in range(0, max(0, len(folded) - size + 1)):
                    window = folded[start : start + size]
                    matcher = difflib.SequenceMatcher(None, window, folded_term)
                    if matcher.real_quick_ratio() < NEAR_MISS_RATIO:
                        continue
                    if NEAR_MISS_RATIO <= matcher.ratio() < 1.0:
                        examples.append(f"`{window}` at {pointer}")
                        break
                else:
                    continue
                break
            if len(examples) >= 2:
                break
        if examples:
            findings.append(
                f"the summary spells it {', '.join(examples)} where the "
                f"vocabulary pool spells it `{term}`"
            )
        if len(findings) >= NEAR_MISS_LIMIT:
            break
    return findings[:NEAR_MISS_LIMIT]


__all__ = [
    "LESSON_SUMMARY",
    "has_emoji",
    "load_schema",
    "validate_about_the_playing",
    "validate_callouts",
    "validate_lesson_summary",
    "validate_no_repetition",
    "validate_practice_goals",
    "validate_progress",
    "validate_schema",
    "validate_short_summary",
    "validate_specific_language",
    "vocabulary_near_misses",
]
