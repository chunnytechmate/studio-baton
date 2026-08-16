"""``baton calendar`` — book lessons, keeping documents and calendar in step.

Replaces the original's two scripts and the prose rule for choosing between
them. There is one command; passing a learner is what selects the lesson path,
including its ordered gate.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..adapters.cal import open_calendar
from ..adapters.cal.base import CalendarEvent
from ..adapters.db import open_store
from ..adapters.docs import open_docs
from ..domain.resolve import resolve_learner
from ..domain.status import StatusVocabulary
from ..domain.whenever import combine, parse_date, parse_schedule, parse_time, today_in
from ..errors import BatonError, UsageError
from ..exits import Exit
from ..pipelines.learner import LearnerHistory
from ..pipelines.schedule import Scheduler

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``calendar`` command group."""
    parser = subparsers.add_parser(
        "calendar",
        help="Book lessons and cancel them, keeping documents and calendar in step.",
        description=(
            "A lesson is marked in progress on its document first; the event is "
            "only created if that succeeded, so the two can never disagree."
        ),
    )
    group = parser.add_subparsers(dest="calendar_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    book = group.add_parser("book", help="Book one lesson.")
    book.add_argument("name", metavar="NAME")
    book.add_argument("date", metavar="DATE", help="YYYY-MM-DD, today, tomorrow, +2, …")
    book.add_argument("start", metavar="START", help="17:00 or 17.00")
    book.add_argument("end", metavar="END", nargs="?", help="Defaults to one hour later.")
    book.add_argument("--session", type=int, default=None, help="Defaults to the next free one.")
    book.add_argument("--dry-run", action="store_true")
    book.set_defaults(handler=handle_book)

    schedule = group.add_parser(
        "schedule",
        help="Book a whole day from a list of times and names.",
        description=(
            "One line per slot: `17:00 Ada Whitfield`. A slot ends when the next "
            "begins; `-` marks a free period, which is skipped but still bounds "
            "the slot before it."
        ),
    )
    schedule.add_argument("date", metavar="DATE")
    schedule.add_argument("--text", help="The schedule, newline separated.")
    schedule.add_argument("--file", metavar="PATH", help="Read the schedule from a file.")
    schedule.add_argument("--dry-run", action="store_true")
    schedule.set_defaults(handler=handle_schedule)

    cancel = group.add_parser(
        "cancel",
        help="Remove a booking and roll its session back.",
        description="Deletes the event, then marks the session not started — in that order.",
    )
    cancel.add_argument("name", metavar="NAME")
    cancel.add_argument("date", metavar="DATE")
    cancel.add_argument("--session", type=int, default=None)
    cancel.add_argument("--dry-run", action="store_true")
    cancel.set_defaults(handler=handle_cancel)

    listing = group.add_parser("list", help="Show a day's events.")
    listing.add_argument("date", metavar="DATE", nargs="?", default="today")
    listing.set_defaults(handler=handle_list)

    when = group.add_parser(
        "date",
        help="Resolve a date expression to YYYY-MM-DD.",
        description=(
            "Date arithmetic belongs in code, not in a model's head: an "
            "off-by-one books a lesson on the wrong day."
        ),
    )
    when.add_argument("expression", metavar="EXPR")
    when.set_defaults(handler=handle_date)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton calendar` needs a subcommand.",
        remedy="Try `baton calendar list`, or `baton calendar book <name> today 17:00`.",
    )


# -- plumbing ----------------------------------------------------------------


def _resolve(ctx: Context, store, name: str):
    return resolve_learner(
        name,
        store.list_learners(),
        aliases=ctx.config.get("db.aliases", {}) or {},
        label=ctx.config.label("learner"),
    )


def _date(ctx: Context, value: str):
    return parse_date(
        value,
        timezone=ctx.config.timezone,
        shorthand=ctx.config.section("calendar.date_shorthand"),
    )


def _scheduler(ctx: Context) -> Scheduler:
    return Scheduler(
        open_calendar(ctx.config),
        open_docs(ctx.config),
        StatusVocabulary.from_config(ctx.config.section("docs.statuses")),
        timezone=ctx.config.timezone,
        session_label=ctx.config.label("session"),
        require_doc_update=bool(ctx.config.get("calendar.require_doc_update", True)),
        event_emoji=ctx.config.section("calendar.event_emoji"),
        default_emoji=str(ctx.config.get("calendar.default_event_emoji", "")),
        default_minutes=int(ctx.config.get("calendar.default_minutes", 60)),
    )


def _pick_session(ctx: Context, store, learner, wanted: int | None):
    """The session this booking belongs to.

    An explicit number is taken from the database without reading documents;
    otherwise the next free one is chosen, which does require reading them.
    """
    if wanted is not None:
        session = store.get_session(learner.id, wanted)
        if session is None:
            raise UsageError(
                f"{learner.name} has no {ctx.config.label('session')} numbered {wanted}.",
                remedy=f'Run `baton learner sessions "{learner.name}"` to see what exists.',
            )
        return session

    history = LearnerHistory(
        store,
        open_docs(ctx.config),
        StatusVocabulary.from_config(ctx.config.section("docs.statuses")),
        max_parallel_reads=int(ctx.config.get("docs.max_parallel_reads", 4)),
    )
    views = history.sessions(learner)
    active = history.in_progress(views)
    chosen = active[0] if active else history.next_empty(views)
    if chosen is None:
        raise UsageError(
            f"{learner.name} has no free {ctx.config.label('session')} to book.",
            remedy="Pass --session explicitly, or create the next one first.",
        )
    return chosen.session


# -- handlers ----------------------------------------------------------------


def handle_book(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        session = _pick_session(ctx, store, learner, ctx.args.session)
        day = _date(ctx, ctx.args.date)
        result = _scheduler(ctx).book(
            learner, session, day, ctx.args.start, ctx.args.end, dry_run=ctx.args.dry_run
        )
    finally:
        store.close()

    verb = "Would book" if ctx.args.dry_run else "Booked"
    label = ctx.config.label("session")
    ctx.report.result(
        {**result.to_dict(), "date": day.isoformat(), "dry_run": ctx.args.dry_run},
        human=f"{verb} {result.learner_name} — {label} {result.session_number} "
        f"on {day.isoformat()} at {ctx.args.start}\n"
        f"  {result.title}"
        + ("" if ctx.args.dry_run else f"\n  document marked in progress: {result.doc_updated}"),
    )
    return Exit.OK


def handle_schedule(ctx: Context) -> Exit:
    if bool(ctx.args.text) == bool(ctx.args.file):
        raise UsageError(
            "Pass exactly one of --text or --file.",
            remedy="The schedule is one line per slot: `17:00 Ada Whitfield`.",
        )

    text = ctx.args.text
    if ctx.args.file:
        path = Path(ctx.args.file).expanduser()
        if not path.is_file():
            raise UsageError(f"No such file: {path}")
        text = path.read_text(encoding="utf-8")

    day = _date(ctx, ctx.args.date)
    slots = parse_schedule(
        text, default_minutes=int(ctx.config.get("calendar.default_minutes", 60))
    )
    if not slots:
        ctx.report.result(
            {"date": day.isoformat(), "booked": [], "blocked": []},
            human="Nothing to book — every slot is free.",
        )
        return Exit.OK

    store = open_store(ctx.config)
    scheduler = _scheduler(ctx)
    booked: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    try:
        for start, end, name in slots:
            # One slot failing must not abandon the rest of the day, but the
            # report has to name every one that did not go.
            try:
                learner = _resolve(ctx, store, name)
                session = _pick_session(ctx, store, learner, None)
                result = scheduler.book(
                    learner,
                    session,
                    day,
                    start.strftime("%H:%M"),
                    end.strftime("%H:%M"),
                    dry_run=ctx.args.dry_run,
                )
                booked.append({**result.to_dict(), "start": start.strftime("%H:%M")})
            except BatonError as err:
                blocked.append(
                    {"slot": start.strftime("%H:%M"), "name": name, "error": err.to_dict()}
                )
    finally:
        store.close()

    payload = {
        "date": day.isoformat(),
        "requested": len(slots),
        "booked": booked,
        "blocked": blocked,
        "dry_run": ctx.args.dry_run,
    }
    lines = [f"{day.isoformat()}: {len(booked)} of {len(slots)} booked"]
    for item in booked:
        lines.append(f"  ✓ {item['start']}  {item['title']}")
    for item in blocked:
        lines.append(f"  ✗ {item['slot']}  {item['name']}: {item['error']['message']}")
    ctx.report.result(payload, human="\n".join(lines))

    return Exit.OK if not blocked else Exit.NEEDS_HUMAN


def handle_cancel(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        session = _pick_session(ctx, store, learner, ctx.args.session)
        day = _date(ctx, ctx.args.date)
        result = _scheduler(ctx).cancel(
            learner,
            session,
            day,
            rollback_window_days=int(ctx.config.get("calendar.rollback_window_days", 1)),
            today=today_in(ctx.config.timezone),
            dry_run=ctx.args.dry_run,
        )
    finally:
        store.close()

    removed = result.get("deleted", result.get("would_delete", []))
    verb = "Would cancel" if ctx.args.dry_run else "Cancelled"
    ctx.report.result(
        {**result, "date": day.isoformat()},
        human=f"{verb} {learner.name} — {ctx.config.label('session')} "
        f"{result['session_number']} on {day.isoformat()}\n"
        f"  events removed: {len(removed)}",
    )
    return Exit.OK


def handle_list(ctx: Context) -> Exit:
    day = _date(ctx, ctx.args.date)
    calendar = open_calendar(ctx.config)
    start = combine(day, parse_time("00:00"), ctx.config.timezone).isoformat()
    end = combine(day + timedelta(days=1), parse_time("00:00"), ctx.config.timezone).isoformat()
    events: list[CalendarEvent] = calendar.list_between(start, end)

    payload = {"date": day.isoformat(), "events": [event.to_dict() for event in events]}
    if not events:
        ctx.report.result(payload, human=f"Nothing on {day.isoformat()}.")
        return Exit.OK

    lines = [day.isoformat()]
    for event in events:
        when = event.start[11:16] if len(event.start) >= 16 else event.start
        lines.append(f"  {when}  {event.title}")
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_date(ctx: Context) -> Exit:
    day = _date(ctx, ctx.args.expression)
    ctx.report.result(
        {
            "expression": ctx.args.expression,
            "date": day.isoformat(),
            "timezone": ctx.config.timezone,
        },
        human=day.isoformat(),
    )
    return Exit.OK
