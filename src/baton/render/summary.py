"""Turning a validated summary into a document.

Deterministic and total: the same structure always produces the same blocks,
and every field the schema allows has a rendering. No model runs here, which is
what makes the output reviewable — a change in the document means a change in
the data or in this file, never a change of mood in a language model.

Section headings come from configuration so a studio can rename them and a
non-English profile can translate them.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SECTIONS = {
    "overview": "Overview",
    "covered": "What we covered",
    "focus": "Focus areas",
    "goals": "Practice goals",
}


def _rich_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def _heading(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": _rich_text(text)}}


def _paragraph(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}


def _bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _todo(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": _rich_text(text), "checked": False},
    }


def _code(text: str, language: str = "plain text") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "code",
        "code": {"rich_text": _rich_text(text), "language": language},
    }


def _callout(text: str, icon: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"rich_text": _rich_text(text)}
    if icon:
        body["icon"] = {"type": "emoji", "emoji": icon}
    return {"object": "block", "type": "callout", "callout": body}


def _sections(config_sections: dict[str, Any] | None) -> dict[str, str]:
    merged = dict(DEFAULT_SECTIONS)
    for key, value in (config_sections or {}).items():
        merged[str(key)] = str(value)
    return merged


def to_blocks(
    summary: dict[str, Any],
    *,
    sections: dict[str, Any] | None = None,
    callout_texts: dict[str, str] | None = None,
    callout_icon: str = "",
) -> list[dict[str, Any]]:
    """Render a validated summary as document blocks.

    Args:
        summary: A payload that has passed
            :func:`baton.contracts.validate_lesson_summary`.
        sections: Heading overrides from ``summary.sections``.
        callout_texts: Theory id to stored text. Ids are resolved here rather
            than by the model, so callout content is always the studio's own.
        callout_icon: Emoji for theory callouts. Matching it to a
            ``docs.preserve`` rule is what keeps them across a later rewrite.

    Returns:
        Blocks in document order.
    """
    names = _sections(sections)
    texts = callout_texts or {}
    blocks: list[dict[str, Any]] = []

    blocks.append(_heading(names["overview"]))
    blocks.extend(_paragraph(line) for line in summary.get("overview", []))

    covered = summary.get("covered", [])
    if covered:
        blocks.append(_heading(names["covered"]))
        for entry in covered:
            topic = entry.get("topic", "")
            detail = entry.get("detail", "")
            blocks.append(_bullet(f"{topic} — {detail}" if detail else topic))
            if entry.get("notation"):
                blocks.append(_code(entry["notation"]))

    for callout_id in summary.get("callouts", []) or []:
        text = texts.get(str(callout_id))
        if text:
            blocks.append(_callout(text, callout_icon))

    focus = summary.get("focus", []) or []
    if focus:
        blocks.append(_heading(names["focus"]))
        blocks.extend(_bullet(f"{item['issue']} → {item['fix']}") for item in focus)

    for section in summary.get("extra_sections", []) or []:
        blocks.append(_heading(section["heading"]))
        blocks.extend(_bullet(item) for item in section["items"])

    goals = summary.get("goals", [])
    if goals:
        blocks.append(_heading(names["goals"]))
        blocks.extend(_todo(goal) for goal in goals)

    return blocks


def to_markdown(
    summary: dict[str, Any],
    *,
    sections: dict[str, Any] | None = None,
    callout_texts: dict[str, str] | None = None,
) -> str:
    """Render the same summary as Markdown, for review before publishing."""
    names = _sections(sections)
    texts = callout_texts or {}
    lines: list[str] = []

    lines.append(f"## {names['overview']}")
    lines.append("")
    lines.extend(summary.get("overview", []))
    lines.append("")

    covered = summary.get("covered", [])
    if covered:
        lines.append(f"## {names['covered']}")
        lines.append("")
        for entry in covered:
            topic = entry.get("topic", "")
            detail = entry.get("detail", "")
            lines.append(f"- {topic} — {detail}" if detail else f"- {topic}")
            if entry.get("notation"):
                lines.append("")
                lines.append("```")
                lines.append(entry["notation"])
                lines.append("```")
        lines.append("")

    for callout_id in summary.get("callouts", []) or []:
        text = texts.get(str(callout_id))
        if text:
            lines.append(f"> {text}")
            lines.append("")

    focus = summary.get("focus", []) or []
    if focus:
        lines.append(f"## {names['focus']}")
        lines.append("")
        lines.extend(f"- {item['issue']} → {item['fix']}" for item in focus)
        lines.append("")

    for section in summary.get("extra_sections", []) or []:
        lines.append(f"## {section['heading']}")
        lines.append("")
        lines.extend(f"- {item}" for item in section["items"])
        lines.append("")

    goals = summary.get("goals", [])
    if goals:
        lines.append(f"## {names['goals']}")
        lines.append("")
        lines.extend(f"- [ ] {goal}" for goal in goals)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def short_message(
    summary: dict[str, Any],
    *,
    bullet: str = "•",
    labels: dict[str, Any] | None = None,
) -> str:
    """Render the parent-facing message.

    Assembled from validated fields rather than taken as a block of text, so
    the format is guaranteed regardless of what the model would have written
    around it.
    """
    names = {"covered": "Covered", "progress": "Progress", "homework": "Practice"}
    for key, value in (labels or {}).items():
        names[str(key)] = str(value)

    short = summary.get("short_summary", {})
    lines = []
    for field in ("covered", "progress", "homework"):
        value = short.get(field)
        if value:
            lines.append(f"{bullet} {names[field]}: {value}")
    return "\n".join(lines)
