"""Markdown to document blocks.

The original skill had no code at all: it handed a model the Notion API shape
and a `curl` invocation and asked it to build the JSON. That is the single most
model-dependent thing the whole system did, and it is entirely mechanical — so
it is a parser.

Deliberately a small subset, and total: every line produces exactly one block,
and anything unrecognised becomes a paragraph rather than being dropped. A note
that silently loses a line is worse than one that renders a line plainly.
"""

from __future__ import annotations

import re
from typing import Any

#: Notion's ceiling on children per request. Callers chunk to this.
MAX_BLOCKS_PER_REQUEST = 100

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_TODO = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_DIVIDER = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_FENCE = re.compile(r"^\s*```(\S*)\s*$")

#: Notion rejects a rich_text run longer than this.
_MAX_RUN = 2000


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Split text into runs Notion will accept.

    A long paragraph is not an error to report back to the user — it is a
    paragraph. Splitting is the only correct handling.
    """
    if not text:
        return []
    return [
        {"type": "text", "text": {"content": text[index : index + _MAX_RUN]}}
        for index in range(0, len(text), _MAX_RUN)
    ]


def _block(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"rich_text": _rich_text(text)}
    body.update(extra)
    return {"object": "block", "type": kind, kind: body}


def to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert Markdown into document blocks.

    Supports headings (1-3), bullets, numbered lists, task items, quotes,
    fenced code, and horizontal rules. Everything else becomes a paragraph.

    Args:
        markdown: The note's text.

    Returns:
        Blocks in document order. Blank lines outside a code fence are dropped
        — a document store lays out its own spacing, and empty paragraphs just
        make the page taller.
    """
    blocks: list[dict[str, Any]] = []
    code_lines: list[str] | None = None
    code_language = "plain text"

    for line in markdown.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if code_lines is None:
                code_lines = []
                code_language = fence.group(1) or "plain text"
            else:
                blocks.append(_block("code", "\n".join(code_lines), language=code_language))
                code_lines = None
            continue

        if code_lines is not None:
            # Inside a fence every line is content, including blank ones:
            # indentation and spacing are what make code readable.
            code_lines.append(line)
            continue

        if not line.strip():
            continue

        if _DIVIDER.match(line):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        todo = _TODO.match(line)
        if todo:
            blocks.append(_block("to_do", todo.group(2), checked=todo.group(1).lower() == "x"))
            continue

        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(_block(f"heading_{level}", heading.group(2).strip()))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            blocks.append(_block("bulleted_list_item", bullet.group(1).strip()))
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            blocks.append(_block("numbered_list_item", numbered.group(1).strip()))
            continue

        quote = _QUOTE.match(line)
        if quote:
            blocks.append(_block("quote", quote.group(1).strip()))
            continue

        blocks.append(_block("paragraph", line.strip()))

    if code_lines is not None:
        # An unclosed fence is a typo, not a reason to lose the content.
        blocks.append(_block("code", "\n".join(code_lines), language=code_language))

    return blocks


def chunk(blocks: list[dict[str, Any]], size: int = MAX_BLOCKS_PER_REQUEST) -> list[list[dict]]:
    """Split blocks into request-sized batches.

    Not an optimisation: the store rejects a larger request outright, and the
    original worked around that by asking the model to split the payload.
    """
    return [blocks[index : index + size] for index in range(0, len(blocks), size)]
