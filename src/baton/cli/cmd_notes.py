"""``baton notes`` — push a note to the documents store.

The skill this replaces had no code at all. It gave a model the Notion API
shape and a `curl` invocation and asked it to assemble the JSON, chunk it at
100 blocks, and retry on failure. All three are mechanical, and all three are
things a model gets wrong quietly — a dropped line looks like a note that was
simply shorter than you remembered.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ..adapters.docs import open_docs
from ..errors import UsageError
from ..exits import Exit
from ..render import markdown

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``notes`` command group."""
    parser = subparsers.add_parser(
        "notes",
        help="Push a note or a Markdown file to the documents store.",
        description=(
            "Markdown is converted to blocks here, not by a model: the "
            "conversion is mechanical, and a dropped line is invisible."
        ),
    )
    group = parser.add_subparsers(dest="notes_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    push = group.add_parser("push", help="Create a note page.")
    push.add_argument("--title", help="Page title. Defaults to the first heading or line.")
    push.add_argument("--text", help="The note itself, as Markdown.")
    push.add_argument("--file", metavar="PATH", help="Read the note from a file. `-` for stdin.")
    push.add_argument(
        "--parent",
        metavar="PAGE_ID",
        help="Parent page. Defaults to the id named by notes.parent_id_env.",
    )
    push.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the blocks that would be created, and stop.",
    )
    push.set_defaults(handler=handle_push)

    preview = group.add_parser(
        "preview",
        help="Convert Markdown to blocks and print them, touching nothing.",
        description="For checking how a note will land before sending it.",
    )
    preview.add_argument("--text")
    preview.add_argument("--file", metavar="PATH")
    preview.set_defaults(handler=handle_preview)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton notes` needs a subcommand.",
        remedy='Try `baton notes push --text "..."`.',
    )


def _read_note(ctx: Context) -> str:
    """The note's text, from --text, a file, or stdin."""
    text: str | None = getattr(ctx.args, "text", None)
    source: str | None = getattr(ctx.args, "file", None)

    if bool(text) == bool(source):
        raise UsageError(
            "Pass exactly one of --text or --file.",
            remedy="Use `--file -` to read the note from stdin.",
        )
    if text:
        return text
    if source == "-":
        return sys.stdin.read()

    path = Path(str(source)).expanduser()
    if not path.is_file():
        raise UsageError(f"No such file: {path}", remedy="Check the path and try again.")
    return path.read_text(encoding="utf-8")


def _title_for(note: str, given: str | None, timezone: str) -> str:
    """The page title: what was asked for, the first heading, or the date.

    A note with no title is still a note. Refusing one would turn a quick
    capture into a form to fill in, which is how quick captures stop happening.
    """
    if given:
        return given.strip()
    for line in note.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Strip the marker as well as the text: a page called "- buy strings"
        # reads as a mistake, and the marker carries no meaning in a title.
        cleaned = stripped.lstrip("#").lstrip("-*+>").strip()
        return cleaned[:200] or _today(timezone)
    return _today(timezone)


def _today(timezone: str) -> str:
    return f"Note — {datetime.now(ZoneInfo(timezone)).date().isoformat()}"


def handle_push(ctx: Context) -> Exit:
    note = _read_note(ctx)
    if not note.strip():
        raise UsageError("The note is empty.", remedy="Pass some text to push.")

    blocks = markdown.to_blocks(note)
    title = _title_for(note, ctx.args.title, ctx.config.timezone)

    parent = ctx.args.parent or ctx.config.secret("notes.parent_id_env", required=False)
    if not parent:
        env_name = ctx.config.get("notes.parent_id_env", "BATON_NOTES_PARENT")
        raise UsageError(
            "No parent page to create the note under.",
            remedy=f"Pass --parent, or set {env_name} to the page id notes live under.",
        )

    if ctx.args.dry_run:
        ctx.report.result(
            {
                "dry_run": True,
                "title": title,
                "parent": parent,
                "blocks": len(blocks),
                "requests": len(markdown.chunk(blocks)),
            },
            human=f"Would create “{title}” with {len(blocks)} block(s) "
            f"in {len(markdown.chunk(blocks))} request(s).",
        )
        return Exit.OK

    ctx.report.step(f"creating “{title}”")
    status = open_docs(ctx.config).create_page(parent, title, blocks)

    ctx.report.result(
        {**status.to_dict(), "blocks": len(blocks)},
        human=f"Created “{title}” — {len(blocks)} block(s)\n  {status.url or status.doc_id}",
    )
    return Exit.OK


def handle_preview(ctx: Context) -> Exit:
    import json

    note = _read_note(ctx)
    blocks = markdown.to_blocks(note)
    kinds: dict[str, int] = {}
    for block in blocks:
        kinds[block["type"]] = kinds.get(block["type"], 0) + 1

    payload = {"blocks": blocks, "count": len(blocks), "types": kinds}
    if ctx.args.json_mode:
        ctx.report.result(payload)
        return Exit.OK

    lines = [f"{len(blocks)} block(s):"]
    lines += [f"  {count:>3}  {kind}" for kind, count in sorted(kinds.items())]
    lines.append("")
    lines.append(json.dumps(blocks, ensure_ascii=False, indent=2))
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK
