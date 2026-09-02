"""Message catalogues.

Strings a person reads are meant to come from a catalogue keyed by a stable id.
The English catalogue is the reference for *keys*; the Thai catalogue is a
first-class translation, not a stub: the studio these pipelines came from runs
in Thai, and a half-translated tool is worse than an untranslated one.

Missing keys fall back to English and then to the key itself, so an incomplete
translation degrades to readable output instead of a crash.

**Only `doctor` and `config show` read from here today.** Both catalogues carry
keys for resolution, gates, contracts, and jobs: `resolve.*`, `gate.blocked`,
`contract.invalid`, `job.*`, `error.unknown_command`, and nothing calls them
yet, so those messages reach the user in English whatever the configured
locale. The catalogues are ahead of the wiring, not behind it: the work left is
routing the errors in :mod:`baton.errors` and the command modules through a
translator, not writing Thai.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

FALLBACK_LOCALE = "en"
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"


def available_locales() -> list[str]:
    """Locale codes with a catalogue in the package."""
    return sorted(p.stem for p in LOCALE_DIR.glob("*.yaml"))


@lru_cache(maxsize=8)
def _catalogue(locale: str) -> dict[str, str]:
    path = LOCALE_DIR / f"{locale}.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


class Translator:
    """Resolves message ids for one locale."""

    def __init__(self, locale: str = FALLBACK_LOCALE) -> None:
        self.locale = locale
        self._primary = _catalogue(locale)
        self._fallback = _catalogue(FALLBACK_LOCALE) if locale != FALLBACK_LOCALE else {}

    def __call__(self, key: str, **kwargs: Any) -> str:
        """Return the message for ``key``, formatted with ``kwargs``.

        A formatting placeholder the caller did not supply yields the raw
        template rather than raising: a broken translation must never take
        down a pipeline that was otherwise about to succeed.
        """
        template = self._primary.get(key) or self._fallback.get(key) or key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template


def translator(locale: str = FALLBACK_LOCALE) -> Translator:
    """Build a :class:`Translator` for ``locale``."""
    return Translator(locale)
