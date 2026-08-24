"""Deterministic document resources from a staged Song DB snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters.docs.base import Block
from ..pipelines.staging import PieceSnapshot

ResourceIdentity = tuple[str, str, str]


def _rich_text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": text}}]


def to_blocks(snapshot: PieceSnapshot) -> list[dict[str, Any]]:
    """Render the frozen title and each available learning resource."""
    if snapshot.status != "captured" or snapshot.piece is None:
        return []
    piece = snapshot.piece
    title = piece.title.strip()
    source = piece.source_link.strip()
    practice = piece.practice_track.strip()
    sheet = piece.sheet_link.strip()
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich_text(f"🎵 {title}")},
        }
    ]
    if source:
        blocks.append({"object": "block", "type": "bookmark", "bookmark": {"url": source}})
    if practice:
        blocks.append(
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": _rich_text(f"Practice track: {practice}"),
                    "icon": {"type": "emoji", "emoji": "🎧"},
                },
            }
        )
    if sheet:
        blocks.append({"object": "block", "type": "embed", "embed": {"url": sheet}})
    return blocks


def to_markdown(snapshot: PieceSnapshot) -> str:
    if snapshot.status != "captured" or snapshot.piece is None:
        return ""
    piece = snapshot.piece
    lines = [f"## 🎵 {piece.title.strip()}"]
    for label, value in (
        ("Source", piece.source_link),
        ("Practice track", piece.practice_track),
        ("Sheet", piece.sheet_link),
    ):
        if value.strip():
            lines.append(f"{label}: {value.strip()}")
    return "\n\n".join(lines)


def _payload_text(body: Mapping[str, Any]) -> str:
    parts = body.get("rich_text", [])
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(part.get("text", {}).get("content", ""))
        for part in parts
        if isinstance(part, Mapping) and isinstance(part.get("text"), Mapping)
    ).strip()


def payload_identity(block: Mapping[str, Any]) -> ResourceIdentity | None:
    """Return identity only for one of Baton's generated resource shapes."""
    kind = block.get("type")
    body = block.get(kind) if isinstance(kind, str) else None
    if not isinstance(body, Mapping):
        return None
    if kind in ("bookmark", "embed"):
        url = body.get("url")
        return (kind, url.strip(), "") if isinstance(url, str) and url.strip() else None
    if kind != "callout":
        return None
    text = _payload_text(body)
    icon_data = body.get("icon")
    icon = icon_data.get("emoji") if isinstance(icon_data, Mapping) else ""
    if icon != "🎧" or not text.startswith("Practice track: "):
        return None
    return (kind, text, icon)


def stored_identity(block: Block) -> ResourceIdentity | None:
    if block.raw:
        identity = payload_identity(block.raw)
        if identity is not None:
            return identity
    if block.type in ("bookmark", "embed") and block.url.strip():
        return (block.type, block.url.strip(), "")
    text = block.text.strip()
    if block.type == "callout" and block.icon == "🎧" and text.startswith("Practice track: "):
        return (block.type, text, block.icon)
    return None
