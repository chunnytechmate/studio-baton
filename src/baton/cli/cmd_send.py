"""``baton send`` — deliver a lesson message, or refuse to.

One learner or several, one invocation either way. The original skill's rule
holds: when a user names several learners, they all go through one command —
opening a terminal per learner is how sends got lost.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from ..adapters.chat import open_chat
from ..adapters.db import open_store
from ..adapters.docs import open_docs
from ..domain.resolve import resolve_learner
from ..errors import BatonError, UsageError
from ..exits import Exit
from ..pipelines.send import send_lesson
from ..pipelines.staging import PublishedRecord

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``send`` command group."""
    parser = subparsers.add_parser(
        "send",
        help="Send a lesson message, refusing when required data is missing.",
        description=(
            "Sends the message that was published — never a re-derived one. A "
            "missing required field blocks the send with exit 5; there is no override."
        ),
    )
    group = parser.add_subparsers(dest="send_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    lesson = group.add_parser("lesson", help="Send one learner's published lesson message.")
    lesson.add_argument("name", metavar="NAME")
    lesson.add_argument("--to", metavar="CONTACT", required=True, help="Configured contact name.")
    lesson.add_argument(
        "--session", type=int, default=None, help="Defaults to the latest published session."
    )
    lesson.add_argument("--dry-run", action="store_true", help="Run the gate and stop.")
    lesson.set_defaults(handler=handle_lesson)

    batch = group.add_parser("batch", help="Send several learners' messages in one invocation.")
    batch.add_argument("--to", metavar="CONTACT", required=True)
    batch.add_argument(
        "--learner",
        action="append",
        dest="learners",
        metavar="NAME",
        required=True,
        help="One learner per flag. Pass the same command once for a whole day.",
    )
    batch.add_argument("--dry-run", action="store_true")
    batch.set_defaults(handler=handle_batch)

    contacts = group.add_parser("contacts", help="List configured contacts.")
    contacts.set_defaults(handler=handle_contacts)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton send` needs a subcommand.",
        remedy="Try `baton send lesson <name> --to <contact>`.",
    )


def _resolve(ctx: Context, store, name: str):
    return resolve_learner(
        name,
        store.list_learners(),
        aliases=ctx.config.get("db.aliases", {}) or {},
        label=ctx.config.label("learner"),
    )


def _required_optional(ctx: Context) -> tuple[list[str], list[str]]:
    gates = ctx.config.section("gates")
    required = [str(item) for item in gates.get("send_lesson_required", [])]
    optional = [str(item) for item in gates.get("send_lesson_optional", [])]
    return required, optional


def _video_link(docs, doc_id: str) -> str:
    """The newest video block on the session document, if any.

    Read from the document rather than remembered from publish time, because
    recordings are usually attached after the summary goes out.

    A document-store failure here degrades to "no recording link": the field
    is optional, so an outage must not stop the summary reaching the family.
    If a studio has moved video_link into the required set, the gate then
    blocks on it with the usual remedy — which is the correct outcome for a
    studio that requires it.
    """
    if not doc_id:
        return ""
    try:
        for block in reversed(docs.list_blocks(doc_id)):
            if block.type == "video" and block.url:
                return block.url
    except BatonError:
        return ""
    return ""


def _date_and_titles(docs, doc_id: str) -> tuple[str, str]:
    """The document's own date and titles, read fresh rather than from the
    published record — both can be corrected on the document after the
    summary went out, and the message should reflect the current page.

    Same degrade-quietly stance as `_video_link`: these are cosmetic, not
    gated, so a document-store hiccup here must not block a send that would
    otherwise go.
    """
    if not doc_id:
        return "", ""
    try:
        status = docs.get_status(doc_id)
    except BatonError:
        return "", ""
    return status.date, status.titles


def _send_one(ctx: Context, name: str, *, session: int | None, dry_run: bool) -> dict[str, Any]:
    """Resolve, gather, gate, and send for one learner. Raises on any refusal."""
    messenger = open_chat(ctx.config)
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, name)
        records = PublishedRecord(ctx.config.state_dir / "published")

        if session is not None:
            published = records.get(learner.id, session)
            if published is None:
                raise UsageError(
                    f"No published lesson for {learner.name} at "
                    f"{ctx.config.label('session')} {session}.",
                    remedy=f'Run `baton lesson publish "{learner.name}"` first.',
                )
        else:
            published = records.latest(learner.id)
            if published is None:
                raise UsageError(
                    f"Nothing has been published for {learner.name} yet.",
                    remedy=f'Run `baton lesson publish "{learner.name}"` first.',
                )

        recipient_id = messenger.resolve(ctx.args.to)
        docs = open_docs(ctx.config)
        doc_id = str(published.get("doc_id", ""))
        video_link = _video_link(docs, doc_id)
        date, titles = _date_and_titles(docs, doc_id)

        required, optional = _required_optional(ctx)
        return send_lesson(
            messenger,
            store,
            recipient_id=recipient_id,
            learner_id=learner.id,
            published=published,
            video_link=video_link,
            date=date,
            titles=titles,
            required=required,
            optional=optional,
            dry_run=dry_run,
        )
    finally:
        store.close()


def handle_lesson(ctx: Context) -> Exit:
    result = _send_one(ctx, ctx.args.name, session=ctx.args.session, dry_run=ctx.args.dry_run)

    verb = "would send" if ctx.args.dry_run else "sent"
    ctx.report.result(
        result,
        human=f"{verb.capitalize()} message for {result['learner']} "
        f"({ctx.config.label('session')} {result['session_number']}) "
        f"to {ctx.args.to}"
        + ("" if ctx.args.dry_run or result.get("sent") else "  ✗ NOT DELIVERED"),
    )
    return Exit.OK


def handle_batch(ctx: Context) -> Exit:
    """Every learner in one invocation, reported together.

    One refusal must not abandon the rest — the teacher still wants the others
    sent — but the summary at the end has to say exactly which did not go.
    """
    requested = list(ctx.args.learners)
    if len(set(requested)) != len(requested):
        raise UsageError(
            "The same learner appears twice in this batch.",
            remedy="Remove the duplicate; sending twice is what it would cause.",
        )

    results: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    sent = 0

    for name in requested:
        try:
            result = _send_one(ctx, name, session=None, dry_run=ctx.args.dry_run)
            results.append(result)
            if result.get("sent"):
                sent += 1
        except BatonError as err:
            blocked.append({"learner": name, "error": err.to_dict()})

    payload = {
        "requested": len(requested),
        "sent": sent,
        "ready": len(results) - sent,
        "blocked": blocked,
        "results": results,
    }

    lines = [f"{len(requested)} requested, {sent} sent"]
    for item in blocked:
        lines.append(f"  ✗ {item['learner']}: {item['error']['message']}")
    if blocked and not ctx.args.dry_run:
        lines.append("Some messages did not go. Fix those and re-run for them alone.")
    ctx.report.result(payload, human="\n".join(lines))

    # A partial batch is not a failure of the ones that went out, but the exit
    # code must still say that not everything was sent.
    return Exit.OK if not blocked else Exit.GATE


def handle_contacts(ctx: Context) -> Exit:
    contacts = ctx.config.section("chat.contacts")
    payload = {
        "contacts": [
            {"name": str(key), "aliases": list(entry.get("aliases", []))}
            for key, entry in contacts.items()
            if isinstance(entry, dict)
        ]
    }
    if not payload["contacts"]:
        ctx.report.result(payload, human="No contacts configured.")
        return Exit.OK

    lines = []
    for entry in payload["contacts"]:
        aliases = ", ".join(entry["aliases"]) if entry["aliases"] else "-"
        lines.append(f"  {entry['name']:<16} {aliases}")
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK
