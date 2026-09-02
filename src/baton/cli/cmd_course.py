"""``baton course``: archive a finished course, then empty it for the next one.

A course ends and the studio starts again on the same pages: the sessions keep
their numbers, the summaries are replaced, the dates move on. What must not
happen is the year of teaching that was on those pages disappearing, so a copy
is filed first and the pages are only emptied once that copy is proven.

Baton does not make the copy. Duplicating a page with its embedded table, every
row, and the table's own layout is something the documents API cannot do:
rebuilding it call by call produces a table that holds the right words in the
wrong shape, which is worse than no copy at all because it looks like one. The
harness driving Baton has a duplicate tool; it makes the copy, and Baton does
everything around it:

    plan    what the copy must be called, and where it belongs
    verify  that the copy is complete, before anything is destroyed
    clear   the live pages, once a complete copy is filed

`clear` enforces the rule itself. It does not trust that `verify` ran: it
re-reads the filed copy, now, and refuses to empty anything unless one copy is
complete: its name, where it sits, and every row. A copy verified yesterday
and trashed today protects nothing, so the gate is read at clear time, every
time. A copy made by hand satisfies it as well as a duplicated one; what the
gate demands is that the copy exists and holds the course, not how it was
made. Two paths stand deliberately outside the rule: `--session`, which
empties a single page mid-course where there is no finished course to file,
and `--dry-run`, which destroys nothing.
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import TYPE_CHECKING, Any

from ..adapters.db import open_store
from ..adapters.docs import open_docs
from ..adapters.docs.base import DocPage, DocStore, TableRow
from ..domain.archive import SpanFormat, archive_title, strip_span
from ..domain.resolve import resolve_learner
from ..errors import GateError, UpstreamError, UsageError
from ..exits import Exit

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``course`` command group."""
    parser = subparsers.add_parser(
        "course",
        help="Archive a finished course and empty it for the next one.",
        description=(
            "The copy itself is made by the harness's duplicate tool: the "
            "documents API cannot reproduce a table's layout, and a rebuilt "
            "one looks like an archive without being one."
        ),
    )
    group = parser.add_subparsers(dest="course_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    plan = group.add_parser(
        "plan",
        help="What the archived copy must be called, and where it belongs.",
        description="Reads only. Nothing is created, moved, or emptied.",
    )
    plan.add_argument("name", metavar="NAME")
    plan.add_argument(
        "--label",
        help="A note to carry in the name, e.g. the piece the course was about.",
    )
    plan.add_argument(
        "--allow-existing",
        action="store_true",
        help="Plan even when a copy of this course is already filed.",
    )
    plan.set_defaults(handler=handle_plan)

    verify = group.add_parser(
        "verify",
        help="Check a copy is complete and correctly filed.",
        description=(
            "Compares the copy against the live course: its name, where it "
            "sits, and every row's number, date, and status."
        ),
    )
    verify.add_argument("name", metavar="NAME")
    verify.add_argument("--page", metavar="PAGE_ID", required=True, help="The copy.")
    verify.add_argument("--label", help="The label the plan was made with, if any.")
    verify.set_defaults(handler=handle_verify)

    clear = group.add_parser(
        "clear",
        help="Empty the live session pages, keeping their numbers.",
        description=(
            "Deletes each page's contents and empties its properties. The rows "
            "stay, so the next course reuses them. Refuses unless a complete "
            "archived copy is filed: the copy is re-read at clear time, so "
            "`course verify` passing earlier is not enough on its own. "
            "`--session N` empties a single page and skips the archive rule; "
            "`--dry-run` only lists."
        ),
    )
    clear.add_argument("name", metavar="NAME")
    clear.add_argument(
        "--session",
        type=int,
        metavar="N",
        help="Only this session. Partial clears are never archived.",
    )
    clear.add_argument("--label", help="The label the plan was made with, if any.")
    clear.add_argument("--dry-run", action="store_true", help="List what would be emptied.")
    clear.set_defaults(handler=handle_clear)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton course` needs a subcommand.",
        remedy='Try `baton course plan "<name>" --json`.',
    )


# -- shared reading ----------------------------------------------------------


def _span_format(ctx: Context) -> SpanFormat:
    section = ctx.config.section("courses.archive.span")
    return SpanFormat(
        date_format=str(section.get("format", "%d/%m")),
        year_format=str(section.get("year", "%y")),
        era=str(section.get("era", "gregorian")),
        separator=str(section.get("separator", " - ")),
        joiner=str(section.get("joiner", "/")),
    )


def _resolve(ctx: Context, store: Any) -> Any:
    return resolve_learner(
        ctx.args.name,
        store.list_learners(),
        aliases=ctx.config.get("db.aliases", {}) or {},
        label=ctx.config.label("learner"),
    )


def _sessions(ctx: Context, store: Any, learner: Any) -> list[Any]:
    sessions = [s for s in store.list_sessions(learner.id) if s.doc_id]
    if not sessions:
        raise GateError(
            f"{learner.name} has no session documents recorded.",
            missing=[{"field": "sessions", "learner": learner.name}],
        )
    return sessions


def _course_of(docs: DocStore, doc_id: str) -> tuple[DocPage, str]:
    """The course page a session document belongs to, and its table's id.

    Walked rather than stored: a studio's own database records which page a
    session is, not which course page holds the table those sessions are rows
    of. The walk is two reads and cannot go stale.
    """
    session = docs.get_page(doc_id)
    if session.parent_kind != "database_id":
        raise GateError(
            "That session document is not a row of a course table.",
            missing=[{"field": "course_table", "doc_id": doc_id, "parent": session.parent_kind}],
            remedy="Courses are archived as a whole; a loose page has nothing to archive.",
        )
    table_id = session.parent_id
    table = docs.get_table(table_id)
    if not table.parent_id:
        raise GateError(
            "The course table is not on a page, so its course cannot be found.",
            missing=[{"field": "course_page", "table_id": table_id}],
        )
    return docs.get_page(table.parent_id), table_id


def _folder_titles(ctx: Context) -> tuple[str, ...]:
    raw = ctx.config.get("courses.archive.folder_titles", ["Archives", "Archive"])
    if isinstance(raw, str):
        raw = [raw]
    return tuple(str(item).strip().casefold() for item in raw if str(item).strip())


def _destination(ctx: Context, docs: DocStore, course: DocPage) -> tuple[str, str]:
    """Where this studio files this learner's finished courses, and how.

    Two arrangements are in use and both are correct. Some learners have a
    folder page beside their course: the copy belongs inside it. The rest keep
    finished courses in the same place as the live one, which is where a
    duplicate already lands, so nothing needs moving. Detecting it per learner
    is what lets one command serve both without anybody tidying Notion first.
    """
    wanted = _folder_titles(ctx)
    for child in docs.list_children(course.parent_id):
        if child.kind != "page":
            continue
        title = child.title.casefold()
        if any(name in title for name in wanted):
            return child.child_id, "folder"
    return course.parent_id, "inplace"


def _span_of(rows: list[TableRow]) -> tuple[date, date]:
    days = sorted(date.fromisoformat(row.date[:10]) for row in rows if row.date)
    if not days:
        raise GateError(
            "No session in this course carries a date, so its span is unknown.",
            remedy="Fill in the dates on the course table, then plan again.",
        )
    return days[0], days[-1]


def _fingerprints(rows: list[TableRow]) -> list[tuple[str, str, str]]:
    return sorted((row.title, row.date, row.status) for row in rows)


def _read_copy(
    docs: DocStore, plan: dict[str, Any], page_id: str
) -> tuple[list[str], DocPage, list[TableRow]]:
    """Every problem with this page as the course's filed copy, with the page.

    The one standard both `verify` and `clear`'s gate hold a copy to: not the
    live page, not in the trash, rightly named, rightly filed, and holding
    every row the live course holds.
    """
    if page_id.replace("-", "") == plan["course"]["page_id"].replace("-", ""):
        # Not a nicety: for a studio that names its live page after the span it
        # is teaching, the copy's name and the live page's name are identical,
        # and every other check here would pass on the original.
        raise GateError(
            "That is the live course page, not a copy of it.",
            missing=[{"field": "copy", "given": page_id, "course_page": plan["course"]["page_id"]}],
            remedy="Use the id the duplicate tool returned.",
        )

    problems: list[str] = []
    copy = docs.get_page(page_id)
    if copy.trashed:
        problems.append("the copy is in the trash")
    if copy.title != plan["archive"]["title"]:
        problems.append(f"named `{copy.title}`, expected `{plan['archive']['title']}`")
    if copy.parent_id.replace("-", "") != plan["archive"]["destination_id"].replace("-", ""):
        problems.append(
            "filed under {} rather than {}".format(
                copy.parent_id or "nothing", plan["archive"]["destination_id"]
            )
        )

    tables = [child for child in docs.list_children(page_id) if child.kind == "table"]
    rows: list[TableRow] = []
    if not tables:
        problems.append("the copy has no course table yet: the duplicate may still be running")
    else:
        rows = docs.table_rows(tables[0].child_id)
        live = docs.table_rows(plan["course"]["table_id"])
        if len(rows) != len(live):
            problems.append(f"holds {len(rows)} rows, the course has {len(live)}")
        else:
            for copied, original in zip(_fingerprints(rows), _fingerprints(live), strict=True):
                if copied != original:
                    problems.append(f"row {original} was copied as {copied}")
                    break
    return problems, copy, rows


def _plan(ctx: Context, *, allow_existing: bool) -> dict[str, Any]:
    """Everything the harness needs, and everything verify checks against."""
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store)
        sessions = _sessions(ctx, store, learner)
    finally:
        store.close()

    docs = open_docs(ctx.config)
    course, table_id = _course_of(docs, sessions[0].doc_id)
    rows = docs.table_rows(table_id)
    if not rows:
        raise GateError(
            "The course table has no rows to archive.",
            missing=[{"field": "rows", "table_id": table_id}],
        )

    first, last = _span_of(rows)
    span = _span_format(ctx)
    label = getattr(ctx.args, "label", None)
    title = archive_title(
        course.title,
        first,
        last,
        span_format=span,
        template=str(ctx.config.get("courses.archive.title", "{course} ({span})")),
        label=label,
    )

    destination_id, arrangement = _destination(ctx, docs, course)

    # A copy already filed under this name means the course was archived
    # before. The live course page is skipped when looking: a studio that
    # renames its live page for the span it is teaching gives that page the
    # very name the copy will take, and mistaking it for the archive would
    # stop every such learner from ever being archived.
    already = [
        child.child_id
        for child in docs.list_children(destination_id)
        if child.kind == "page"
        and child.child_id.replace("-", "") != course.doc_id.replace("-", "")
        and child.title == title
    ]
    if already and not allow_existing:
        raise GateError(
            f"`{title}` is already filed.",
            missing=[{"field": "unfiled_course", "title": title, "existing": already}],
            remedy=(
                "If that copy is complete, this course is archived: clear it. "
                "To file a second copy anyway, pass --allow-existing."
            ),
        )

    return {
        "learner": learner.name,
        "course": {
            "page_id": course.doc_id,
            "title": course.title,
            "base_title": strip_span(course.title, span),
            "table_id": table_id,
            "url": course.url,
        },
        "archive": {
            "title": title,
            "label": label,
            "destination_id": destination_id,
            "arrangement": arrangement,
            "needs_move": arrangement == "folder",
            "already_filed": already,
        },
        "span": {"first": first.isoformat(), "last": last.isoformat()},
        "rows": len(rows),
        "renames_live_page": course.title == title,
    }


# -- handlers ----------------------------------------------------------------


def handle_plan(ctx: Context) -> Exit:
    payload = _plan(ctx, allow_existing=bool(ctx.args.allow_existing))
    archive = payload["archive"]

    lines = [
        f"{payload['learner']}: {payload['course']['title']}",
        f"  copy this page : {payload['course']['page_id']}",
        f"  name it        : {archive['title']}",
    ]
    if archive["needs_move"]:
        lines.append(f"  move it into   : {archive['destination_id']}")
    else:
        lines.append("  move it        : not needed, a copy already lands where these are filed")
    lines.append(f"  rows to expect : {payload['rows']}")
    if payload["renames_live_page"]:
        lines.append(
            "  note           : the live page already carries this name: rename it "
            "for the new course once the copy is filed"
        )
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_verify(ctx: Context) -> Exit:
    plan = _plan(ctx, allow_existing=True)
    docs = open_docs(ctx.config)
    page_id = str(ctx.args.page)
    problems, copy, rows = _read_copy(docs, plan, page_id)

    payload = {
        "ok": not problems,
        "page_id": page_id,
        "title": copy.title,
        "expected_title": plan["archive"]["title"],
        "rows": len(rows),
        "expected_rows": plan["rows"],
        "problems": problems,
        "url": copy.url,
    }
    if problems:
        ctx.report.failure(
            payload,
            human="The copy is not usable yet:\n" + "\n".join(f"  - {p}" for p in problems),
        )
        return Exit.GATE

    ctx.report.result(
        payload,
        human=f"`{copy.title}` is complete: {len(rows)} rows, filed where it belongs.",
    )
    return Exit.OK


def _require_archive(ctx: Context, docs: DocStore) -> dict[str, Any]:
    """The copy that stands between this clear and the course it empties.

    `clear` does not remember that `verify` ran: it looks for the filed copy
    now, holds it to the same standard `verify` holds, and refuses unless one
    passes. That is the rule: no complete copy, no clear.
    """
    plan = _plan(ctx, allow_existing=True)
    candidates = plan["archive"]["already_filed"]
    if not candidates:
        raise GateError(
            "No archived copy of this course is filed.",
            missing=[
                {
                    "field": "archive",
                    "course": plan["course"]["title"],
                    "expected_title": plan["archive"]["title"],
                }
            ],
            remedy=(
                "Run `baton course plan`, duplicate the page, then "
                "`baton course verify`: clear refuses to empty an unarchived course."
            ),
        )

    failures: list[dict[str, Any]] = []
    for page_id in candidates:
        problems, copy, _rows = _read_copy(docs, plan, page_id)
        if not problems:
            return {"title": copy.title, "page_id": copy.doc_id, "url": copy.url}
        failures.append({"page_id": page_id, "problems": problems})

    raise GateError(
        "The filed copy of this course is not complete.",
        missing=[{"field": "archive", "copies": failures}],
        remedy=(
            "Run `baton course verify` to see what is wrong, fix the copy or "
            "file a new one: clear refuses to empty an unarchived course."
        ),
    )


def handle_clear(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store)
        sessions = _sessions(ctx, store, learner)
    finally:
        store.close()

    wanted = ctx.args.session
    if wanted is not None:
        sessions = [s for s in sessions if s.number == wanted]
        if not sessions:
            raise UsageError(
                f"{learner.name} has no {ctx.config.label('session')} {wanted}.",
                remedy="Run `baton learner sessions` to see which exist.",
            )
    sessions.sort(key=lambda s: s.number)

    if ctx.args.dry_run:
        payload = {
            "dry_run": True,
            "learner": learner.name,
            "sessions": [s.number for s in sessions],
            "count": len(sessions),
        }
        ctx.report.result(
            payload,
            human="Would empty {} {}: {}".format(
                len(sessions),
                ctx.config.label("sessions"),
                ", ".join(str(s.number) for s in sessions),
            ),
        )
        return Exit.OK

    docs = open_docs(ctx.config)
    archive: dict[str, Any] | None = None
    if wanted is None:
        # The full clear is the destructive one, so it carries the gate. A
        # partial clear is a mid-course tool: there is no finished course to
        # file, so demanding an archive for it would make it unusable.
        archive = _require_archive(ctx, docs)

    cleared: list[int] = []
    skipped: list[dict[str, Any]] = []
    blocks_removed = 0

    for session in sessions:
        try:
            if not docs.restore(session.doc_id):
                skipped.append({"session": session.number, "reason": "in the trash"})
                continue
            block_ids = [block.id for block in docs.list_blocks(session.doc_id)]
            blocks_removed += docs.delete_blocks(block_ids)
            docs.reset_properties(session.doc_id)
        except UpstreamError as failure:
            # One unreachable page must not strand the rest half-emptied with
            # no record of which. The run continues and reports both lists.
            skipped.append({"session": session.number, "reason": str(failure)})
            continue
        cleared.append(session.number)

    payload = {
        "learner": learner.name,
        "archive": archive,
        "cleared": cleared,
        "skipped": skipped,
        "blocks_removed": blocks_removed,
    }
    human = "Emptied {} {} ({} blocks removed). The rows stayed.".format(
        len(cleared), ctx.config.label("sessions"), blocks_removed
    )
    if archive:
        human += f"\nArchived first as `{archive['title']}`."
    if skipped:
        human += "\nLeft alone: " + ", ".join(
            f"{item['session']} ({item['reason']})" for item in skipped
        )
        ctx.report.failure(payload, human=human)
        return Exit.UPSTREAM
    ctx.report.result(payload, human=human)
    return Exit.OK
