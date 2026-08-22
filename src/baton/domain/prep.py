"""Reading a session page as named sections, for lesson preparation.

A summarised page answers one question well — "what happened" — while the
person walking into the next lesson needs three: what was covered, what was
set as homework, and where the teaching was meant to go next. The page knows
all of it; the knowledge just lives under headings, not in properties. So the
reading is by headings: a block belongs to the section whose heading it sits
under.

The headings are the studio's own template, so they are configuration, not
code — a studio that renames "การบ้าน" to something else changes one line of
``baton.yaml``, not this module. Two rules survive any template: a checklist
block is homework wherever it sits (a to-do under "ภาพรวม" is still a thing to
practise), and the summariser's credit line is never part of any section.

Nothing here talks to a network: blocks in, section names out, which is what
makes the template testable rather than merely documented.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..adapters.docs.base import Block

if TYPE_CHECKING:
    from ..core.config import Config

#: The sections a session template defines, in the order headings are tried.
#: Order is behaviour: a heading matching two keywords belongs to the first
#: section listed, which is how "เป้าหมายการซ้อม" stays practice goals while
#: "เป้าหมายครั้งถัดไป" becomes the next goal.
DEFAULT_SECTIONS: dict[str, tuple[str, ...]] = {
    "overview": ("ภาพรวมการเรียน", "overview"),
    "content": ("เนื้อหา", "สิ่งที่เรียน", "core lesson"),
    "focus": ("โฟกัส", "focus"),
    "practice_goals": ("เป้าหมายการซ้อม", "practice goals"),
    "next_goal": ("ครั้งถัดไป",),
    "homework": ("การบ้าน", "homework"),
}

#: The credit line the summariser appends, never part of what was taught.
DEFAULT_FOOTER = r"หางยาว \(\d+ .*\) สรุปนี้มาจากผู้ช่วย AI"


def missing_fields(entry: Mapping[str, Any], wanted: list[str]) -> list[str]:
    """The fields of ``entry`` that are absent or blank, in ``wanted`` order."""
    return [field for field in wanted if not str(entry.get(field) or "").strip()]


@dataclass(frozen=True)
class SectionRules:
    """How one studio's session pages are divided into sections.

    Attributes:
        keywords: Section name to the heading words that open it, tried in
            order. Matching is a casefolded substring test, so an emoji or a
            trailing "(Practice Goals)" on a heading changes nothing.
        homework_types: Block types that are homework regardless of the
            heading above them.
        footer: A pattern stripped from every section — the summariser's
            credit line, which is about the tool, not the lesson.
        max_chars: Per-section ceiling, so one runaway page cannot flood a
            report meant to be read before a lesson.
    """

    keywords: dict[str, tuple[str, ...]]
    homework_types: frozenset[str]
    footer: re.Pattern[str] | None
    max_chars: int

    @classmethod
    def from_config(cls, config: Config) -> SectionRules:
        """Read ``docs.sections`` and friends, with the studio template default.

        Raises:
            ConfigError: ``docs.summary_footer`` is not a valid pattern.
        """
        from ..errors import ConfigError  # local: avoids an import cycle

        raw_sections = config.get("docs.sections", None)
        if isinstance(raw_sections, str):
            raw_sections = [raw_sections]
        keywords: dict[str, tuple[str, ...]] = {}
        if isinstance(raw_sections, Mapping):
            for name, words in raw_sections.items():
                if isinstance(words, str):
                    words = [words]
                keywords[str(name)] = tuple(str(word) for word in words or ())
        if not keywords:
            keywords = dict(DEFAULT_SECTIONS)

        types = config.get("docs.homework_types", ["to_do"])
        if isinstance(types, str):
            types = [types]
        footer_raw = str(config.get("docs.summary_footer", DEFAULT_FOOTER))
        try:
            footer = re.compile(footer_raw, re.DOTALL)
        except re.error as exc:
            raise ConfigError(
                f"`docs.summary_footer` is not a valid pattern: {exc}",
                remedy="Quote the pattern in baton.yaml, or restore the default "
                "by removing the key.",
            ) from exc
        return cls(
            keywords=keywords,
            homework_types=frozenset(str(kind) for kind in types),
            footer=footer,
            max_chars=int(config.get("prep.max_section_chars", 400)),
        )

    def read(self, blocks: list[Block]) -> dict[str, str]:
        """Every section these blocks hold, capped and footer-stripped.

        Blocks before the first recognised heading belong to nothing — the
        page's title area, not a section. Every key of ``keywords`` appears in
        the result, empty when the page lacks it, so callers can index without
        asking first.
        """
        collected: dict[str, list[str]] = {name: [] for name in self.keywords}
        current: str | None = None
        for block in blocks:
            if block.type.startswith("heading"):
                current = None
                folded = block.text.casefold()
                for name, words in self.keywords.items():
                    if any(word.casefold() in folded for word in words):
                        current = name
                        break
                continue
            if block.type in self.homework_types:
                body = block.raw.get(block.type) or {}
                mark = "[x]" if isinstance(body, dict) and body.get("checked") else "[ ]"
                if block.text.strip():
                    collected.setdefault("homework", []).append(f"{mark} {block.text.strip()}")
                continue
            if block.text.strip() and current:
                collected[current].append(block.text.strip())

        sections: dict[str, str] = {}
        for name, lines in collected.items():
            joined = "\n".join(lines).strip()
            if self.footer is not None:
                joined = self.footer.sub("", joined).strip()
            sections[name] = joined[: self.max_chars]
        return sections
