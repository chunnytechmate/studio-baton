"""``baton learner`` — look up people, their sessions, pieces, and work.

Every subcommand that takes a name runs it through the resolution gate first,
so an ambiguous name exits ``3`` with candidates rather than acting on a guess.
"""

from __future__ import annotations

import argparse
import re
from typing import TYPE_CHECKING, Any

from ..adapters.db import open_store
from ..adapters.docs import VIDEO_LINK_BLOCKS, find_video_link, open_docs
from ..adapters.docs.base import PreservePolicy
from ..domain.models import Learner, Session, Work
from ..domain.notion_urls import detect_week, parse_page_id
from ..domain.prep import SectionRules
from ..domain.resolve import normalise, resolve_learner
from ..domain.status import StatusVocabulary
from ..domain.whenever import today_in
from ..errors import BatonError, ConfigError, GateError, NeedsHumanError, UpstreamError, UsageError
from ..exits import Exit
from ..pipelines.learner import LearnerHistory, PublishedPieceUpdater, SessionView
from ..pipelines.recording import attach_work, list_candidates, recording_blocks
from ..pipelines.staging import PublishedRecord
from .cmd_calendar import _scheduler

if TYPE_CHECKING:
    from .app import Context

_STATE_MARK = {"done": "✓", "in_progress": "▶", "not_started": "·", "": "?"}


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``learner`` command group."""
    parser = subparsers.add_parser(
        "learner",
        help="Enrol a learner, then look up sessions, pieces, and recorded work.",
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
    active.add_argument(
        "--videos",
        action="store_true",
        help="Also read the candidates' pages for their recording, so the "
        "teacher sees which unfinished lessons already have one.",
    )
    active.set_defaults(handler=handle_in_progress)

    works = group.add_parser("works", help="Recorded performances and recordings.")
    works.add_argument("name", metavar="NAME")
    works.set_defaults(handler=handle_works)

    add = group.add_parser(
        "add",
        help="Enrol a new learner.",
        description="Refuses on an exact-name duplicate, and writes nothing "
        "unless every page URL given resolves.",
    )
    add.add_argument("name", metavar="NAME")
    add.add_argument("--instrument", required=True)
    add.add_argument("--tone", default="")
    add.add_argument("--has-instrument", action="store_true")
    add.add_argument(
        "--prompt-level", type=int, default=None, metavar="N", help="A studio-specific column."
    )
    add.add_argument("--master-link", default="", metavar="URL")
    add.add_argument(
        "--db-link",
        default="",
        metavar="URL",
        help="The Notion database the session pages live in.",
    )
    add.add_argument(
        "--page-urls",
        nargs="*",
        default=(),
        metavar="URL",
        help="Session pages; the week comes from each URL's slug, or counts "
        "up from 1 when a URL has none.",
    )
    add.add_argument(
        "--pages",
        nargs="*",
        default=(),
        metavar="W<n>-URL",
        help="Session pages named by week, e.g. W1-https://... Takes "
        "precedence over --page-urls when both are given.",
    )
    add.add_argument(
        "--dry-run", action="store_true", help="Show what would be enrolled, and stop."
    )
    add.set_defaults(handler=handle_add)

    add_work = group.add_parser("add-work", help="Record a finished performance.")
    add_work.add_argument("name", metavar="NAME")
    add_work.add_argument("--title", required=True)
    add_work.add_argument("--type", default="performance", help="performance, cover, exam, …")
    add_work.add_argument("--video-link", default="")
    add_work.add_argument(
        "--drive-link", default="", help="A second home of the recording (Drive file)."
    )
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
    assign.add_argument(
        "--update-published",
        action="store_true",
        help="Replace this assignment's rendered piece section on already-published sessions.",
    )
    assign.add_argument("--dry-run", action="store_true")
    assign.set_defaults(handler=handle_assign)

    attach = group.add_parser(
        "attach-work",
        help="Put a recorded work onto its session page.",
        description=(
            "A recording that did not go through the video pipeline — a "
            "teacher's own edit, a clip shared directly — has links in the "
            "database and nothing on the page. This writes the same section "
            "the old push wrote, under the same heading, onto the session "
            "In progress by default or --session N. Links already on the page "
            "are not written twice, and nothing else on the page is removed."
        ),
    )
    attach.add_argument("name", metavar="NAME")
    attach.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="The Nth work from the newest-first list (1 is the latest). "
        "Without it the command lists and asks.",
    )
    attach.add_argument(
        "--session", type=int, default=None, help="Defaults to the session in progress."
    )
    attach.add_argument("--dry-run", action="store_true", help="Report what would be written.")
    attach.set_defaults(handler=handle_attach_work)


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
        # One extra read, only when there is a list to annotate: the piece
        # catalogue is small, and knowing who is on what without a second
        # command is the whole point of a roster.
        titles = {p.id: p.title for p in store.list_pieces()} if learners else {}
    finally:
        store.close()

    payload = {"learners": [item.to_dict() for item in learners], "count": len(learners)}
    if not learners:
        ctx.report.result(payload, human=f"No {ctx.config.label('learners')} recorded.")
        return Exit.OK

    width = max(len(item.name) for item in learners)
    lines = []
    for item in learners:
        line = f"  {item.name:<{width}}  {item.instrument or '-':<10} {item.tone or '-'}"
        if item.current_piece_id:
            line += f"  — {titles.get(item.current_piece_id, '?')}"
        lines.append(line)
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

    # The candidates' recordings, when asked for: one extra block read per
    # in-progress session, still bounded by the calendar window. The old
    # report answered this by scanning every page of every learner; the same
    # column without the whole-world scan. A page that cannot be read here
    # reads as no recording, matching how the send path degrades.
    recordings: dict[str, str] = {}
    if ctx.args.videos:
        docs = open_docs(ctx.config)
        shapes = tuple(
            str(item) for item in ctx.config.get("docs.video_link_blocks", VIDEO_LINK_BLOCKS)
        )
        for _learner, view in report.found:
            recordings[view.session.doc_id] = find_video_link(
                docs, view.session.doc_id, blocks=shapes
            )

    payload = {
        "in_progress": [
            {
                "learner": learner.to_dict(),
                **view.to_dict(scheduler.vocabulary),
                **({"video_link": recordings[view.session.doc_id]} if ctx.args.videos else {}),
            }
            for learner, view in report.found
        ],
        "unreadable": report.unreadable,
        "unmatched_events": report.unmatched,
        "window": report.window,
        "count": len(report.found),
    }

    def _mark(view) -> str:
        if not ctx.args.videos:
            return ""
        return "  🎬" if recordings.get(view.session.doc_id) else "  —"

    lines: list[str] = []
    if report.found:
        width = max(len(learner.name) for learner, _ in report.found)
        lines += [
            f"  {learner.name:<{width}}  {label} {view.number}"
            f"{f'  {view.doc.titles}' if view.doc.titles else ''}{_mark(view)}"
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


def _check_mapped(fields: dict[str, Any], extra: dict[str, Any], *, setting: str) -> None:
    """Refuse before any write when an extra field has no column configured.

    The same check :meth:`~baton.adapters.db.base.FieldMap.extra_columns`
    makes at write time, run here first so a learner and their first session
    are not created before the *second* session's extra field turns out to
    be unmapped.
    """
    for key in extra:
        if key not in fields:
            raise ConfigError(
                f"`{setting}.{key}` is not mapped, so `{key}` cannot be written.",
                remedy=f"Add it under {setting} in baton.yaml, or drop the field.",
                details={"setting": setting, "field": key},
            )


def _validate_choice(ctx: Context, value: str, *, setting: str, field: str) -> None:
    """Refuse a value the profile's own list does not name.

    An empty list means the profile has not restricted this field: every
    value is accepted, because a public tool has no business guessing a
    studio's vocabulary for it.
    """
    allowed = [str(item) for item in (ctx.config.get(setting, []) or [])]
    if allowed and value not in allowed:
        raise UsageError(
            f"`{value}` is not a configured {field}.",
            remedy=f"Use one of: {', '.join(allowed)}.",
            details={"setting": setting, "understood": allowed},
        )


def _resolve_pages(args: Any) -> list[tuple[int, str]]:
    """(week, page id) pairs from --pages or --page-urls, in the order given.

    ``--pages`` wins over ``--page-urls`` when both are given, matching the
    original script's order. A URL Baton cannot read a page id from is
    refused rather than skipped — silently dropping a page a person meant to
    add is worse than stopping to ask about it.

    Raises:
        UsageError: A ``--pages`` token is not ``W<week>-<url>``, or a URL
            carries no Notion page id.
    """
    entries: list[tuple[int, str]] = []
    if args.pages:
        for token in args.pages:
            match = re.match(r"^[Ww](\d+)-(.+)$", token)
            if not match:
                raise UsageError(
                    f"`{token}` is not `W<week>-<url>`.",
                    remedy="Each --pages token is a week number, a dash, then the page URL.",
                )
            url = match.group(2)
            page_id = parse_page_id(url)
            if page_id is None:
                raise UsageError(
                    f"No Notion page id could be read from `{url}`.",
                    remedy="Paste the page's own URL, not a shortened or edited one.",
                )
            entries.append((int(match.group(1)), page_id))
        return entries

    next_week = 1
    for url in args.page_urls:
        page_id = parse_page_id(url)
        if page_id is None:
            raise UsageError(
                f"No Notion page id could be read from `{url}`.",
                remedy="Paste the page's own URL, not a shortened or edited one.",
            )
        week = detect_week(url) or next_week
        entries.append((week, page_id))
        next_week = week + 1
    return entries


def handle_add(ctx: Context) -> Exit:
    """Enrol a learner, then their session pages, in that order.

    Nothing is written until every input has resolved: the name is not a
    duplicate, the instrument and tone (when the profile restricts them) are
    known values, every page URL carries a page id, and every extra field —
    a prompt level, a master link, a session's Notion database id — has a
    place to be written. A studio-specific column named on the command line
    with nowhere configured to put it is a configuration error, not a
    silently dropped write.
    """
    args = ctx.args
    _validate_choice(ctx, args.instrument, setting="learner.instruments", field="instrument")
    if args.tone:
        _validate_choice(ctx, args.tone, setting="learner.tones", field="tone")

    pages = _resolve_pages(args)

    database_id = ""
    if args.db_link:
        parsed = parse_page_id(args.db_link)
        if parsed is None:
            raise UsageError(
                f"No Notion database id could be read from `{args.db_link}`.",
                remedy="Paste the database's own URL.",
            )
        database_id = parsed

    learner_fields = ctx.config.section("db.fields.learner")
    session_fields = ctx.config.section("db.fields.session")
    learner_extra: dict[str, Any] = {}
    if args.prompt_level is not None:
        learner_extra["prompt_level"] = args.prompt_level
    if args.master_link:
        learner_extra["master_link"] = args.master_link
    session_extra_keys = []
    if database_id:
        session_extra_keys.append("database_id")
        session_extra_keys.append("notion_db_full_link")
    elif pages and "database_id" in session_fields:
        # The overlay's own schema has a NOT NULL database_id column: a page
        # with no database id would fail the whole insert, so this is caught
        # before the learner is even created rather than mid-way through.
        raise UsageError(
            f"{ctx.config.label('session')} pages need --db-link: this "
            "profile's session table requires a database id.",
            remedy="Pass --db-link with the pages' Notion database URL.",
        )
    _check_mapped(learner_fields, learner_extra, setting="db.fields.learner")
    _check_mapped(
        session_fields, dict.fromkeys(session_extra_keys, ""), setting="db.fields.session"
    )

    store = _store(ctx)
    try:
        existing = store.list_learners()
        wanted = normalise(args.name)
        duplicate = next((p for p in existing if normalise(p.name) == wanted), None)
        if duplicate is not None:
            raise GateError(
                f"{args.name} is already recorded (id={duplicate.id}).",
                remedy=f'Use `baton learner show "{args.name}"` to see the '
                "existing record. Nothing was written.",
            )
        # Either direction: a name that extends an existing one ("Elin
        # Frostberg" against "Elin Frost") or is extended by one, either way
        # worth a second look without blocking the enrolment over it.
        similar = [
            p.name for p in existing if wanted in normalise(p.name) or normalise(p.name) in wanted
        ]

        proposed = Learner(
            id="",
            name=args.name,
            instrument=args.instrument,
            tone=args.tone,
            has_instrument=args.has_instrument,
        )

        if args.dry_run:
            ctx.report.result(
                {
                    "would_add": proposed.to_dict(),
                    "would_add_extra": learner_extra,
                    "would_add_sessions": [
                        {"number": week, "doc_id": page_id} for week, page_id in pages
                    ],
                    "similar": similar,
                    "dry_run": True,
                },
                human=f"Would enrol {args.name} ({args.instrument})"
                + (f" with {len(pages)} session page(s)." if pages else "."),
            )
            return Exit.OK

        created = store.add_learner(proposed, extra=learner_extra or None)
        created_sessions: list[Session] = []
        try:
            for week, page_id in pages:
                session_extra: dict[str, Any] = {}
                if database_id:
                    session_extra["database_id"] = database_id
                    session_extra["notion_db_full_link"] = args.db_link
                created_sessions.append(
                    store.add_session(
                        Session(id="", learner_id=created.id, number=week, doc_id=page_id),
                        extra=session_extra or None,
                    )
                )
        except BatonError as exc:
            raise UpstreamError(
                f"{created.name} was enrolled, but a session page failed to write: {exc.message}",
                service="db",
                remedy="The learner and any listed sessions already exist — "
                "check what landed before retrying the rest by hand.",
                details={
                    "learner": created.to_dict(),
                    "sessions_written": [s.to_dict() for s in created_sessions],
                },
            ) from exc
    finally:
        store.close()

    ctx.report.result(
        {
            "learner": created.to_dict(),
            "sessions": [s.to_dict() for s in created_sessions],
            "similar": similar,
        },
        human=f"Enrolled {created.name} ({created.instrument})"
        + (f" with {len(created_sessions)} session page(s)." if created_sessions else ".")
        + (f" Similarly named: {', '.join(similar)}." if similar else ""),
    )
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
            drive_link=ctx.args.drive_link,
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


def handle_attach_work(ctx: Context) -> Exit:
    """One recorded work, written onto the session page it belongs to.

    Which recording goes on is a person's call, so the command is two steps
    like `send recording`: without --pick it ends at exit 3 carrying the
    numbered list; --pick N then writes exactly that one.
    """
    label = ctx.config.label("session")
    store = _store(ctx)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
        works = store.list_works(learner.id)

        pick = ctx.args.pick
        if pick is None:
            if not works:
                raise GateError(
                    f"{learner.name} has no recorded work to attach.",
                    remedy="Record one first with `baton learner add-work "
                    f'"{ctx.args.name}" --title … (--video-link / --drive-link)`.',
                )
            raise NeedsHumanError(
                f"{learner.name} has {len(works)} recorded work(s) — which one goes on the page?",
                candidates=list_candidates(works),
                remedy="Re-run with --pick <number> from that list; 1 is the "
                "most recent. Nothing was written.",
            )
        if not 1 <= pick <= len(works):
            raise UsageError(
                f"--pick {pick} does not match any of {learner.name}'s "
                f"{len(works)} recorded work(s).",
                remedy="Re-run without --pick to see the newest-first list.",
                details={"candidates": list_candidates(works)} if works else None,
            )

        # Which page: the session in progress by default — the recording
        # belongs to the lesson it came from — or the one named.
        history = _history(ctx, store)
        views = history.sessions(learner)
        if ctx.args.session is not None:
            view = next((v for v in views if v.session.number == ctx.args.session), None)
            if view is None or not view.session.doc_id:
                raise UsageError(
                    f"{learner.name} has no {label} {ctx.args.session} to attach to.",
                    remedy=f'Run `baton learner sessions "{ctx.args.name}"` to see what exists.',
                )
        else:
            active = [v for v in views if v.state == "in_progress" and v.session.doc_id]
            if len(active) != 1:
                numbers = ", ".join(str(v.session.number) for v in active)
                which = (
                    "none is in progress"
                    if not active
                    else f"{len(active)} are in progress ({numbers})"
                )
                raise UsageError(
                    f"Cannot tell which {label} {learner.name}'s recording belongs to: {which}.",
                    remedy="Re-run with --session N. Nothing was written.",
                )
            view = active[0]
        doc_id = view.session.doc_id
        work = works[pick - 1]
    finally:
        store.close()

    if ctx.args.dry_run:
        would = recording_blocks(work)
        ctx.report.result(
            {
                "learner": learner.to_dict(),
                "work": work.to_dict(),
                "doc_id": doc_id,
                "would_append": len(would),
                "dry_run": True,
            },
            human=f"Would write {len(would)} blocks for “{work.title}” onto "
            f"{learner.name}'s {label} {view.session.number}.",
        )
        return Exit.OK

    result = attach_work(open_docs(ctx.config), doc_id, work)
    ctx.report.result(
        {"learner": learner.to_dict(), "work": work.to_dict(), **result},
        human=(
            f"Attached “{work.title}” to {learner.name}'s {label} "
            f"{view.session.number}: {result['appended']} blocks written"
            + (
                f", {len(result['already_on_page'])} already there"
                if result["already_on_page"]
                else ""
            )
        ),
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
        published_plan: dict[str, Any] | None = None
        updater: PublishedPieceUpdater | None = None
        if ctx.args.update_published:
            updater = PublishedPieceUpdater(
                open_docs(ctx.config),
                PublishedRecord(ctx.config.state_dir / "published"),
                PreservePolicy.from_config(ctx.config.get("docs.preserve", [])),
            )
            published_plan = updater.plan(
                store.list_sessions(learner.id),
                from_piece_id=learner.current_piece_id,
                to_piece=piece,
            )

        if ctx.args.dry_run:
            visible_plan = (
                {key: value for key, value in published_plan.items() if key != "_plans"}
                if published_plan is not None
                else None
            )
            page_count = int((visible_plan or {}).get("would_update", 0))
            ctx.report.result(
                {
                    "learner": learner.to_dict(),
                    "would_assign": piece_id,
                    "published_updates": visible_plan,
                    "dry_run": True,
                },
                human=f"Would set {learner.name} to {target}"
                + (f" and update {page_count} published page(s)." if visible_plan else "."),
            )
            return Exit.OK

        published_updates = updater.apply(published_plan) if updater and published_plan else None
        store.set_current_piece(learner.id, piece_id)
    finally:
        store.close()

    ctx.report.result(
        {
            "learner": learner.to_dict(),
            "assigned": piece_id,
            "published_updates": published_updates,
        },
        human=f"{learner.name} is now working on {target}."
        + (
            f" Updated {published_updates['updated']} published page(s)."
            if published_updates is not None
            else ""
        ),
    )
    return Exit.OK
