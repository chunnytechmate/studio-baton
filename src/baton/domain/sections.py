"""The two names every summary section has, and the link between them.

A session page is written by one half of Baton and read back by the other, and
until this module existed the two halves kept separate vocabularies with
nothing holding them together:

- the renderer writes a heading per section, configured under
  ``summary.sections``
- the section reader finds a section by matching heading *keywords*,
  configured under ``docs.sections``

They did not even share names — the renderer's ``covered`` is the reader's
``content``, its ``goals`` is the reader's ``practice_goals`` — and the
packaged defaults disagreed outright. The renderer wrote "What we covered";
the reader looked for ``เนื้อหา``, ``สิ่งที่เรียน``, ``core lesson``; so the
largest section of every summary Baton published read back empty. Since
``prep.required`` lists ``content`` and prep is fail-closed, the teacher got no
briefing at all for those learners, and nothing anywhere said why.

Both vocabularies and the mapping between them live here now, so a section
renamed on one side is still found on the other.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Heading the renderer writes for each of its sections. Studios rename or
#: translate these under ``summary.sections``.
WRITTEN_HEADINGS: dict[str, str] = {
    "overview": "Overview",
    "progress": "Progress",
    "covered": "What we covered",
    "focus": "Focus areas",
    "goals": "Practice goals",
}

#: Heading words that open each section when a page is read back, tried in
#: order. Order is behaviour: a heading matching two sections belongs to the
#: first one listed, which is how "เป้าหมายการซ้อม" stays practice goals while
#: "เป้าหมายครั้งถัดไป" becomes the next goal.
#:
#: These are the *studio template's* headings — pages the previous pipeline
#: wrote, and pages a teacher types by hand. Baton's own headings are added on
#: top of them by :func:`with_written_headings`, so both kinds of page read
#: back the same way.
READ_KEYWORDS: dict[str, tuple[str, ...]] = {
    "overview": ("ภาพรวมการเรียน", "overview"),
    "progress": ("พัฒนาการ", "ความก้าวหน้า", "progress"),
    "content": ("เนื้อหา", "สิ่งที่เรียน", "core lesson"),
    "focus": ("โฟกัส", "focus"),
    "practice_goals": ("เป้าหมายการซ้อม", "practice goals"),
    "next_goal": ("ครั้งถัดไป",),
    "homework": ("การบ้าน", "homework"),
}

#: Which read-back section each written section lands in. Stated rather than
#: inferred: the two vocabularies grew apart, and guessing at the pairing from
#: the names alone would silently mis-file ``covered`` and ``goals``.
#:
#: ``progress`` is the one section whose two names match, because it was added
#: after this module existed to hold both.
#:
#: Sections read back but never written — ``next_goal``, ``homework`` — are
#: absent on purpose. Nothing Baton writes carries those headings; homework
#: arrives through ``docs.homework_types`` instead, which claims checklist
#: blocks wherever they sit.
WRITES_INTO: dict[str, str] = {
    "overview": "overview",
    "progress": "progress",
    "covered": "content",
    "focus": "focus",
    "goals": "practice_goals",
}


def with_written_headings(
    keywords: Mapping[str, tuple[str, ...]],
    written: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    """``keywords``, plus each written heading added to the section it feeds.

    Appended rather than prepended so a studio's own keywords keep their
    priority, and skipped when the heading is already there so a studio that
    configured both does not end up matching twice.

    Args:
        keywords: Read-back keywords per section, from ``docs.sections``.
        written: Heading per written section, from ``summary.sections``.

    Returns:
        A new mapping. Sections with no written counterpart are unchanged.
    """
    merged = {name: tuple(words) for name, words in keywords.items()}
    for written_name, read_name in WRITES_INTO.items():
        heading = str(written.get(written_name, "")).strip()
        if not heading or read_name not in merged:
            continue
        if any(heading.casefold() == word.casefold() for word in merged[read_name]):
            continue
        merged[read_name] = (*merged[read_name], heading)
    return merged
