"""``baton send`` — deliver a lesson message, or refuse to.

One learner or several, one invocation either way. The original skill's rule
holds: when a user names several learners, they all go through one command —
opening a terminal per learner is how sends got lost.
"""

from __future__ import annotations

import argparse
import contextlib
from typing import TYPE_CHECKING, Any

from ..adapters.chat import open_chat
from ..adapters.db import open_store
from ..adapters.docs import VIDEO_LINK_BLOCKS, find_video_link, open_docs
from ..domain.localdate import DateFormat
from ..domain.models import Learner
from ..domain.resolve import resolve_learner
from ..errors import BatonError, GateError, NeedsHumanError, UsageError
from ..exits import Exit
from ..pipelines.recording import list_candidates, send_recording
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

    recording = group.add_parser(
        "recording",
        help="Send a recorded work's Drive/YouTube links to one learner's contact.",
        description=(
            "Lists the learner's recorded works and sends the chosen one's "
            "links. Two invocations by design: without --pick the command ends "
            "at exit 3 carrying the numbered list; --pick <n> then delivers "
            "exactly that work. Whatever side of the recording is missing is "
            "simply not sent."
        ),
    )
    recording.add_argument("name", metavar="NAME")
    # Not required at parse time: the listing half needs no recipient at all.
    # Only a picked send goes anywhere.
    recording.add_argument(
        "--to", metavar="CONTACT", help="Configured contact name. Required once --pick is used."
    )
    recording.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="Send the Nth recording from the newest-first list (1 is the latest).",
    )
    recording.add_argument("--dry-run", action="store_true", help="Compose the message and stop.")
    recording.set_defaults(handler=handle_recording)

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


def _date_format(ctx: Context) -> DateFormat:
    """How this studio writes a lesson date for the families it teaches.

    Empty by default, which passes the document's own value through unchanged.
    The studio this came from writes "23 ส.ค. 2569", and the message said
    "2026-08-23" from the rewrite until this existed.
    """
    return DateFormat.from_config(ctx.config.section("chat.date"))


def _date_and_titles(docs, doc_id: str) -> tuple[str, str]:
    """The document's own date and titles, read fresh rather than from the
    published record — both can be corrected on the document after the
    summary went out, and the message should reflect the current page.

    Same degrade-quietly stance as `find_video_link`: these are cosmetic, not
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


def _send_one(
    ctx: Context,
    name: str,
    *,
    session: int | None,
    dry_run: bool,
    resolved: Learner | None = None,
) -> dict[str, Any]:
    """Resolve, gather, gate, and send for one learner. Raises on any refusal.

    ``resolved`` lets a batch pass in a learner it resolved up front, so the
    duplicate check and the send act on the same person — the check cannot
    compare raw strings and the send compare records.
    """
    messenger = open_chat(ctx.config)
    store = open_store(ctx.config)
    try:
        learner = resolved if resolved is not None else _resolve(ctx, store, name)
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
        video_link = find_video_link(
            docs,
            doc_id,
            blocks=tuple(
                str(item) for item in ctx.config.get("docs.video_link_blocks", VIDEO_LINK_BLOCKS)
            ),
        )
        date, titles = _date_and_titles(docs, doc_id)
        date = _date_format(ctx).of_text(date)

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
        # `not result.get("sent")` has no reachable trigger today: every
        # Messenger.send() either raises (the send failed, and _send_one lets
        # that propagate before this point is ever reached) or returns
        # SendOutcome(sent=True, ...) -- no shipped driver or test fake
        # constructs sent=False. The field stays on SendOutcome for a future
        # driver that reports a failed-but-not-exceptional send (e.g. queued,
        # rejected without a hard error) without raising; this line is what
        # would surface that the moment one exists.
        + ("" if ctx.args.dry_run or result.get("sent") else "  ✗ NOT DELIVERED"),
    )
    return Exit.OK


def handle_recording(ctx: Context) -> Exit:
    """List a learner's recorded works, or deliver the picked one's links.

    Two invocations and no prompt, because whatever runs Baton drives it with
    commands — the studio's agent relays the list to the person choosing, then
    answers with ``--pick``. Each call reads the store fresh: nothing is
    remembered between them, so a pick always lands on the row the list the
    person answered from was built from.
    """
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        works = store.list_works(learner.id)

        pick = ctx.args.pick
        if pick is None:
            if not works:
                raise GateError(
                    f"{learner.name} has no recorded work to send.",
                    remedy="Record one first with `baton learner add-work "
                    f'"{ctx.args.name}" --title … (--video-link / --drive-link)`.',
                )
            # Which recording goes out is a person's call — the date ordering
            # suggests, but cannot know, what the parent meant.
            raise NeedsHumanError(
                f"{learner.name} has {len(works)} recorded work(s) — which one goes out?",
                candidates=list_candidates(works),
                remedy="Re-run with --pick <number> from that list; 1 is the "
                "most recent. Nothing has been sent.",
            )

        if ctx.args.to is None:
            raise UsageError(
                "A picked recording needs somewhere to go.",
                remedy="Re-run with --to <contact> alongside --pick.",
                details={"candidates": list_candidates(works)},
            )

        if not 1 <= pick <= len(works):
            raise UsageError(
                f"--pick {pick} does not match any of {learner.name}'s "
                f"{len(works)} recorded work(s).",
                remedy=(
                    "--pick counts the newest-first list: 1 is the latest. "
                    "Re-run without --pick to see it."
                    if works
                    else f"No recording exists yet — record one first with "
                    f'`baton learner add-work "{ctx.args.name}" --title …`.'
                ),
                details={"candidates": list_candidates(works)} if works else None,
            )
        index = pick - 1
    finally:
        store.close()

    # The lesson page the recording belongs to, when one is published: the
    # record already exists, and after a publish it *is* "the latest lesson".
    # Read fail-open — nothing has been published yet, or the record cannot be
    # read, and the links still go out the way the old sender sent them.
    doc_url = ""
    with contextlib.suppress(BatonError):
        latest = PublishedRecord(ctx.config.state_dir / "published").latest(learner.id)
        doc_url = str((latest or {}).get("doc_url", ""))

    # The contact resolves here rather than before the gate: a wrong contact
    # name should not be the error that masks "there is nothing to send".
    messenger = open_chat(ctx.config)
    recipient_id = messenger.resolve(ctx.args.to)
    work = works[index]
    result = send_recording(
        messenger,
        recipient_id=recipient_id,
        work=work,
        learner_name=learner.name,
        instrument=learner.instrument,
        date=_date_format(ctx).of_text(work.performed_date),
        doc_url=doc_url,
        dry_run=ctx.args.dry_run,
    )

    verb = "would send" if ctx.args.dry_run else "sent"
    delivered = "" if ctx.args.dry_run or result.get("sent") else "  ✗ NOT DELIVERED"
    ctx.report.result(
        result,
        human=f"{verb.capitalize()} the recording “{work.title}” for {learner.name} "
        f"to {ctx.args.to}{delivered}",
    )
    return Exit.OK


def handle_batch(ctx: Context) -> Exit:
    """Every learner in one invocation, reported together.

    One refusal must not abandon the rest — the teacher still wants the others
    sent — but the summary at the end has to say exactly which did not go.
    """
    requested = list(ctx.args.learners)

    # Resolve the whole batch before anything is sent. Two different strings
    # can name one learner through `db.aliases` — "เจ" and "น้องเจ" — and a
    # comparison of the raw names lets that pair through, after which each
    # entry sends its own message. The duplicate that matters is the person,
    # not the spelling.
    store = open_store(ctx.config)
    try:
        resolved: list[Learner] = []
        for name in requested:
            resolved.append(_resolve(ctx, store, name))
    finally:
        store.close()

    duplicates = sorted(
        {
            learner.name
            for learner in resolved
            if [item.id for item in resolved].count(learner.id) > 1
        }
    )
    if duplicates:
        raise UsageError(
            "The same learner appears twice in this batch: " + ", ".join(duplicates) + ".",
            remedy="Remove the duplicate; sending twice is what it would cause.",
        )

    results: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    sent = 0

    for name, learner in zip(requested, resolved, strict=True):
        try:
            result = _send_one(ctx, name, session=None, dry_run=ctx.args.dry_run, resolved=learner)
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
