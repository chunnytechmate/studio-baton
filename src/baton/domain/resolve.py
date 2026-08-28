"""Turning a typed name into exactly one learner, or refusing to.

This is the hard gate carried over from the original ``scal.py``, generalised
so every command shares it. The rule is narrow on purpose:

* an exact name match resolves
* a configured alias that points at exactly one name resolves
* **everything else stops and asks**

A partial match does not resolve, even when there is only one of them. A
nickname matching a single learner today will match two the moment a second
child with that nickname joins,
and a booking silently made against the wrong person is far more expensive than
one extra question. Substring matches are returned as *candidates* so the
caller can present real options instead of inventing one.

This is deliberately defence in depth. The agent driving Baton is also told to
confirm ambiguous names — but an agent that forgets, or that is confidently
wrong, still cannot get past this.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from ..errors import NeedsHumanError
from .models import Learner


def normalise(name: str) -> str:
    """Fold a name for comparison: trim, collapse spaces, casefold, NFC.

    Thai composes above and below the base character, so the same visible name
    can arrive as different code point sequences depending on the keyboard or
    the paste source. Without NFC normalisation those compare unequal and an
    exact match silently becomes an ambiguity prompt.
    """
    folded = unicodedata.normalize("NFC", name).strip().casefold()
    return " ".join(folded.split())


def resolve_learner(
    query: str,
    learners: Iterable[Learner],
    *,
    aliases: Mapping[str, str] | None = None,
    label: str = "learner",
) -> Learner:
    """Resolve ``query`` to exactly one learner, or raise.

    Args:
        query: The name as typed by a person or passed by an agent.
        learners: The full candidate set to resolve against.
        aliases: Optional nickname map, ``{"JK": "Jao Khun"}``. An alias is
            followed once and its target must then match exactly — chained
            aliases are not resolved, because a chain is impossible to audit.
        label: Domain word used in messages, from the profile's labels.

    Returns:
        The single matching :class:`~baton.domain.models.Learner`.

    Raises:
        NeedsHumanError: The name is ambiguous, or matches nothing. Carries a
            ``candidates`` list — possibly empty — for the caller to show.
    """
    people = list(learners)
    wanted = normalise(query)

    if not wanted:
        raise NeedsHumanError(
            f"No {label} name was given.",
            candidates=_as_candidates(people),
            remedy=f"Pass the {label}'s name exactly as it is recorded.",
        )

    exact = [p for p in people if normalise(p.name) == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # Two records genuinely share a name. No amount of cleverness picks the
        # right one; the operator has to say which.
        raise NeedsHumanError(
            f"More than one {label} is recorded as “{query}”.",
            candidates=_as_candidates(exact),
            remedy=f"Ask which {label} is meant and re-run using their id.",
        )

    if aliases:
        target = _alias_target(wanted, aliases)
        if target is not None:
            aliased = [p for p in people if normalise(p.name) == target]
            if len(aliased) == 1:
                return aliased[0]
            if len(aliased) > 1:
                raise NeedsHumanError(
                    f"The alias “{query}” points at more than one {label}.",
                    candidates=_as_candidates(aliased),
                    remedy="Fix the alias in baton.yaml so it names one person.",
                )
            raise NeedsHumanError(
                f"The alias “{query}” points at a {label} who is not recorded.",
                candidates=_as_candidates(people),
                remedy=f"Fix the alias in baton.yaml, or add the missing {label} to the database.",
            )

    partial = [p for p in people if wanted in normalise(p.name)]
    if partial:
        # Never resolved automatically, however few there are. See module docs.
        raise NeedsHumanError(
            f"“{query}” is not an exact match for any {label}.",
            candidates=_as_candidates(partial),
            remedy=f"Ask which {label} is meant, then re-run with the full name.",
        )

    raise NeedsHumanError(
        f"No {label} matches “{query}”.",
        candidates=_as_candidates(people),
        remedy=f"Check the spelling with the {label} list, or add them first.",
    )


def resolve_learner_loose(
    query: str,
    learners: Iterable[Learner],
    *,
    aliases: Mapping[str, str] | None = None,
    label: str = "learner",
) -> tuple[Learner, str]:
    """Resolve like :func:`resolve_learner`, with one deliberate relaxation.

    A partial match that lands on exactly one person resolves — and returns a
    note saying so, because a booking made under a guess the operator never
    saw is worse than a refusal. Zero matches, or several, re-raise the strict
    gate unchanged: its candidates list is already the right answer, and
    guessing between two learners is not a relaxation anyone asked for. The
    module docstring above explains why the strict gate exists at all.

    Booking is the only caller. ``calendar book`` and ``calendar schedule``
    read names a person typed by hand, often shortened; every other command
    keeps the strict gate, because a message that leaves the studio for the
    wrong person is not something an exit code can undo.

    Returns:
        ``(learner, note)`` — the note is empty unless the partial-match
        relaxation fired, in which case it is a sentence for the operator.
    """
    try:
        return resolve_learner(query, learners, aliases=aliases, label=label), ""
    except NeedsHumanError:
        wanted = normalise(query)
        if not wanted:
            raise
        matches = [p for p in learners if wanted in normalise(p.name)]
        if len(matches) == 1:
            return matches[0], (f'resolved the partial {label} name "{query}" to {matches[0].name}')
        raise


def _alias_target(wanted: str, aliases: Mapping[str, str]) -> str | None:
    """The normalised name an alias points at, if any."""
    for alias, target in aliases.items():
        if normalise(str(alias)) == wanted:
            return normalise(str(target))
    return None


def _as_candidates(people: Iterable[Learner]) -> list[dict[str, str]]:
    """Shape learners for the ``candidates`` payload of a NeedsHumanError."""
    return [
        {"id": str(p.id), "name": p.name, "instrument": p.instrument}
        for p in sorted(people, key=lambda p: p.name)
    ]
