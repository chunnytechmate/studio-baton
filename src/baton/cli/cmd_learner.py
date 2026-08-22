"""``baton learner`` — look up people, their sessions, pieces, and work.

Every subcommand that takes a name runs it through the resolution gate first,
so an ambiguous name exits ``3`` with candidates rather than acting on a guess.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from ..adapters.db import open_store
from ..adapters.docs import open_docs
from ..domain.models import Work
from ..domain.prep import SectionRules
from ..domain.resolve import resolve_learner
from ..domain.status import StatusVocabulary
from ..domain.whenever import today_in
from ..errors import BatonError, UsageError
from ..exits import Exit
from ..pipelines.learner import LearnerHistory, SessionView
from .cmd_calendar import _scheduler

if TYPE_CHECKING:
    from .app import Context

_STATE_MARK = {"done": "✓", "in_progress": "▶", "not_started": "·", "": "?"}


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``learner`` command group."""
    parser = subparsers.add_parser(
        "learner",
        help="Look up learners, sessions, pieces, and recorded work.",
        description="Read-mostly queries across the database and the session documents.",
    )
    group = parser.add_subparsers(dest="learner_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    listing = group.add_parser("list", help="List every learner.")
    listing.set_defaults(handler=handle_list)

    show = group.add_parser(
        "show",
        help="Everything about one learner: current piece, latest session, next free one.",
    )
    show.add_argument("name", metavar="NAME")
    show.set_defaults(handler=handle_show)

    sessions = group.add_parser("sessions", help="Every session with its document status.")
    sessions.add_argument("name", metavar="NAME")
    sessions.set_defaults(handler=handle_sessions)

    latest = group.add_parser(
        "latest",
        help="The most recent session that actually happened.",
        description=(
            "The newest session whose document is marked done — not the highest "
            "number. Sessions get skipped, so a high number proves nothing."
        ),
    )
    latest.add_argument("name", metavar="NAME")
    latest.set_defaults(handler=handle_latest)

    next_free = group.add_parser(
        "next",
        help="The lowest session a new lesson may land on.",
        description=(
            "A not-started page with no content is free. A page in progress is "
            "the target while it is fresh — the studio's flow books a lesson, the "
            "page turns In progress, and the summary is written onto that page. "
            "Only a page still in progress more than learner.next_stale_days "
            "past its date is passed over as abandoned. An unstarted page that "
            "already has blocks on it is work in progress, and is never free."
        ),
    )
    next_free.add_argument("name", metavar="NAME")
    next_free.set_defaults(handler=handle_next)

    active = group.add_parser(
        "in-progress",
        help="Who still owes a summary, from the calendar window.",
        description=(
            "Reads the last learner.in_progress_days of calendar events (today "
            "included) and checks only those learners' pages — one calendar call "
            "plus one document read per lesson, not every page of every learner. "
            "A lesson counts only while its page still says In progress: "
            "cancelled and summarized lessons drop out on their own. Sessions "
            "booked for a coming day are outside the window; `calendar list` "
            "answers who is coming."
        ),
    )
    active.set_defaults(handler=handle_in_progress)

    works = group.add_parser("works", help="Recorded performances and recordings.")
    works.add_argument("name", metavar="NAME")
    works.set_defaults(handler=handle_works)

    add_work = group.add_parser("add-work", help="Record a finished performance.")
    add_work.add_argument("name", metavar="NAME")
    add_work.add_argument("--title", required=True)
    add_work.add_argument("--type", default="performance", help="performance, cover, exam, …")
    add_work.add_argument("--video-link", default="")
    add_work.add_argument("--date", default="", metavar="YYYY-MM-DD")
    add_work.add_argument(
        "--dry-run", action="store_true", help="Show what would be recorded, and stop."
    )
    add_work.set_defaults(handler=handle_add_work)

    pieces = group.add_parser("pieces", help="List every piece.")
    pieces.set_defaults(handler=handle_pieces)

    assign = group.add_parser("assign", help="Set or clear the piece a learner is working on.")
    assign.add_argument("name", metavar="NAME")
    assign.add_argument(
        "--piece", default="", metavar="PIECE_ID", help="Piece id. Omit to clear the assignment."
    )
    assign.add_argument("--dry-run", action="store_true")
    assign.set_defaults(handler=handle_assign)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton learner` needs a subcommand.",
        remedy="Try `baton learner list`, or `baton learner show <name>`.",
    )


# -- shared plumbing ---------------------------------------------------------


def _store(ctx: Context):
    return open_store(ctx.config)


def _next_stale_days(ctx: Context) -> int | None:
    """`learner.next_stale_days`, where `null` means never abandon a page."""
    value = ctx.config.get("learner.next_stale_days", 1)
    return None if value is None else int(value)


def _history(ctx: Context, store) -> LearnerHistory:
    return LearnerHistory(
        store,
        open_docs(ctx.config),
        StatusVocabulary.from_config(ctx.config.section("docs.statuses")),
        max_parallel_reads=int(ctx.config.get("docs.max_parallel_reads", 4)),
        next_stale_days=_next_stale_days(ctx),
    )


def _resolve(ctx: Context, store, name: str):
    """Resolve a typed name, or raise NeedsHumanError with candidates."""
    return resolve_learner(
        name,
        store.list_learners(),
        aliases=ctx.config.get("db.aliases", {}) or {},
        label=ctx.config.label("learner"),
    )


def _session_line(view: SessionView, vocabulary: StatusVocabulary, label: str) -> str:
    mark = _STATE_MARK.get(view.state, "?")
    status = view.doc.status or vocabulary.label(view.state) or "unknown"
    bits = [f"  {mark} {label} {view.number:<3} {status}"]
    if view.doc.date:
        bits.append(f"  {view.doc.date}")
    if view.doc.titles:
        bits.append(f"  {view.doc.titles}")
    if view.doc.block_count:
        bits.append(f"  ({view.doc.block_count} blocks)")
    return "".join(bits)


# -- handlers ----------------------------------------------------------------


def handle_list(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learners = store.list_learners()
    finally:
        store.close()

    payload = {"learners": [item.to_dict() for item in learners], "count": len(learners)}
    if not learners:
        ctx.report.result(payload, human=f"No {ctx.config.label('learners')} recorded.")
        return Exit.OK

    width = max(len(item.name) for item in learners)
    lines = [
        f"  {item.name:<{width}}  {item.instrument or '-':<10} {item.tone or '-'}"
        for item in learners
    ]
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_show(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        history = _history(ctx, store)
        views = history.sessions(learner)
        payload = history.summarise(learner, views, today=today_in(ctx.config.timezone))
    finally:
        store.close()

    label = ctx.config.label("session")
    lines = [f"{learner.name}  ({learner.instrument or 'no instrument recorded'})"]
    if payload["current_piece"]:
        lines.append(f"  working on : {payload['current_piece']['title']}")
    lines.append(f"  tone       : {learner.tone or '-'}")
    lines.append(f"  practises at home: {'yes' if learner.has_instrument else 'no'}")
    lines.append("")

    latest = payload["sessions"]["latest_done"]
    upcoming = payload["sessions"]["next_empty"]
    lines.append(
        f"  latest done: {label} {latest['number']} ({latest['date'] or 'no date'})"
        if latest
        else f"  latest done: none — no {label} is marked done yet"
    )
    lines.append(
        f"  next free  : {label} {upcoming['number']}"
        if upcoming
        else f"  next free  : none — every {label} is started or has content"
    )
    for view in payload["sessions"]["in_progress"]:
        lines.append(f"  in progress: {label} {view['number']}")
    totals = payload["sessions"]
    lines.append(f"  total      : {totals['total']} ({totals['done']} done)")

    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_sessions(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        history = _history(ctx, store)
        views = history.sessions(learner)
    finally:
        store.close()

    label = ctx.config.label("session")
    payload = {
        "learner": learner.to_dict(),
        "sessions": [view.to_dict(history.vocabulary) for view in views],
    }
    if not views:
        ctx.report.result(payload, human=f"{learner.name} has no {ctx.config.label('sessions')}.")
        return Exit.OK

    lines = [f"{learner.name}"] + [_session_line(view, history.vocabulary, label) for view in views]
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_latest(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        history = _history(ctx, store)
        view = history.latest_done(history.sessions(learner))
    finally:
        store.close()

    label = ctx.config.label("session")
    payload: dict[str, Any] = {
        "learner": learner.to_dict(),
        "latest_done": view.to_dict(history.vocabulary) if view else None,
    }
    if view is None:
        ctx.report.result(
            payload,
            human=f"{learner.name} has no {label} marked done yet.",
        )
        return Exit.OK

    # The page's headings carry what properties cannot: what was covered, what
    # was set to practise, where the teaching goes next. One extra read turns
    # "which session was last" into "prepare the next lesson" in one command.
    sections: dict[str, str] = {}
    unreadable = ""
    if view.session.doc_id:
        try:
            blocks = history.docs.list_blocks(view.session.doc_id)
            sections = SectionRules.from_config(ctx.config).read(blocks)
        except BatonError as exc:
            unreadable = exc.message
    payload["sections"] = sections
    if unreadable:
        payload["sections_unreadable"] = unreadable

    lines = [
        f"{learner.name} — {label} {view.number}"
        f"{f'  {view.doc.date}' if view.doc.date else ''}"
        f"{f'  {view.doc.titles}' if view.doc.titles else ''}",
        f"  doc: {view.session.doc_id}",
    ]
    if unreadable:
        lines.append(f"  sections unreadable: {unreadable}")
    lines += [f"  {name.replace('_', ' ')}: {text}" for name, text in sections.items() if text]
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_next(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        history = _history(ctx, store)
        views = history.sessions(learner)
        view = history.next_empty(views, today=today_in(ctx.config.timezone))
    finally:
        store.close()

    label = ctx.config.label("session")
    payload = {
        "learner": learner.to_dict(),
        "next_empty": view.to_dict(history.vocabulary) if view else None,
        "highest_number": max((v.number for v in views), default=0),
    }
    if view is None:
        ctx.report.result(
            payload,
            human=f"{learner.name} has no free {label}. "
            f"Highest recorded: {payload['highest_number']}.",
        )
        return Exit.OK

    ctx.report.result(
        payload,
        human=f"{learner.name} — next free {label}: {view.number}\n  doc: {view.session.doc_id}",
    )
    return Exit.OK


def handle_in_progress(ctx: Context) -> Exit:
    scheduler = _scheduler(ctx)
    store = _store(ctx)
    try:
        report = scheduler.in_progress(
            store,
            window_days=int(ctx.config.get("learner.in_progress_days", 14)),
        )
    finally:
        store.close()

    label = ctx.config.label("session")
    payload = {
        "in_progress": [
            {"learner": learner.to_dict(), **view.to_dict(scheduler.vocabulary)}
            for learner, view in report.found
        ],
        "unreadable": report.unreadable,
        "unmatched_events": report.unmatched,
        "window": report.window,
        "count": len(report.found),
    }

    lines: list[str] = []
    if report.found:
        width = max(len(learner.name) for learner, _ in report.found)
        lines += [
            f"  {learner.name:<{width}}  {label} {view.number}"
            f"{f'  {view.doc.titles}' if view.doc.titles else ''}"
            for learner, view in report.found
        ]
    lines += [
        f"  ⚠ {entry['learner']}'s {label} {entry['number']} could not be read: {entry['why']}"
        for entry in report.unreadable
    ]
    lines += [
        f"  ? “{entry['title']}” names no {label} this studio booked" for entry in report.unmatched
    ]
    if not lines:
        lines = [f"No {label} is in progress in the last {report.window['days']} days."]
    lines.append(f"  window: {report.window['start']} … {report.window['through']}")
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_works(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        works = store.list_works(learner.id)
    finally:
        store.close()

    payload = {"learner": learner.to_dict(), "works": [item.to_dict() for item in works]}
    if not works:
        ctx.report.result(payload, human=f"{learner.name} has no recorded work.")
        return Exit.OK

    lines = [f"{learner.name}"] + [
        f"  {item.performed_date or '          '}  {item.title}  ({item.type})"
        + (f"  {item.video_link}" if item.video_link else "")
        for item in works
    ]
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_add_work(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        proposed = Work(
            id="",
            learner_id=learner.id,
            title=ctx.args.title,
            type=ctx.args.type,
            video_link=ctx.args.video_link,
            performed_date=ctx.args.date,
        )
        if ctx.args.dry_run:
            ctx.report.result(
                {"learner": learner.to_dict(), "would_add": proposed.to_dict(), "dry_run": True},
                human=f"Would record for {learner.name}: {proposed.title} ({proposed.type})",
            )
            return Exit.OK

        created = store.add_work(proposed)
    finally:
        store.close()

    ctx.report.result(
        {"learner": learner.to_dict(), "work": created.to_dict()},
        human=f"Recorded for {learner.name}: {created.title} ({created.type})",
    )
    return Exit.OK


def handle_pieces(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        pieces = store.list_pieces()
    finally:
        store.close()

    payload = {"pieces": [item.to_dict() for item in pieces], "count": len(pieces)}
    if not pieces:
        ctx.report.result(payload, human=f"No {ctx.config.label('pieces')} recorded.")
        return Exit.OK

    width = max(len(item.title) for item in pieces)
    lines = []
    for item in pieces:
        extras = []
        if item.practice_track:
            extras.append("track")
        if item.sheet_link:
            extras.append("sheet")
        lines.append(f"  {item.id:>4}  {item.title:<{width}}  {' '.join(extras)}")
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_assign(ctx: Context) -> Exit:
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        piece_id = ctx.args.piece or None

        piece = None
        if piece_id:
            piece = store.get_piece(piece_id)
            if piece is None:
                raise UsageError(
                    f"No {ctx.config.label('piece')} has id `{piece_id}`.",
                    remedy="Run `baton learner pieces` to see the ids that exist.",
                )

        target = piece.title if piece else "nothing"
        if ctx.args.dry_run:
            ctx.report.result(
                {"learner": learner.to_dict(), "would_assign": piece_id, "dry_run": True},
                human=f"Would set {learner.name} to {target}.",
            )
            return Exit.OK

        store.set_current_piece(learner.id, piece_id)
    finally:
        store.close()

    ctx.report.result(
        {"learner": learner.to_dict(), "assigned": piece_id},
        human=f"{learner.name} is now working on {target}.",
    )
    return Exit.OK
