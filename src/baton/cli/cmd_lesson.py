"""``baton lesson`` — stage a lesson, validate a written summary, publish it.

The loop an agent follows, and the reason it is three commands rather than one:

    baton lesson stage    "Ada"              # collect context
    baton lesson contract "Ada"              # → JSON Schema + that context
    …the model writes JSON…
    baton lesson ingest   "Ada" --file s.json  # validated, or exit 4 with reasons
    baton lesson render   "Ada"              # deterministic preview
    baton lesson publish  "Ada"              # onto the document

`contract` hands the model everything it needs and tells it exactly what shape
to return. `ingest` is where a wrong shape stops — nothing is stored, and every
violation comes back at once with a pointer, so the next attempt is informed
rather than another guess.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import contracts
from ..adapters.db import open_store
from ..adapters.db.base import LearnerStore
from ..adapters.docs import find_video_link, open_docs
from ..adapters.docs.base import PreservePolicy
from ..adapters.media import open_publisher
from ..adapters.media.google import extract_video_id
from ..domain.models import Learner
from ..domain.resolve import resolve_learner
from ..domain.status import StatusVocabulary
from ..domain.whenever import today_in
from ..errors import BatonError, ConfigError, UpstreamError, UsageError
from ..exits import Exit
from ..pipelines.learner import LearnerHistory
from ..pipelines.publish import SummaryPublisher
from ..pipelines.staging import (
    PUBLISHED,
    SUMMARISED,
    LessonDraft,
    PieceSnapshot,
    PublishedRecord,
    StagingStore,
)
from ..render import summary as render
from ..render import youtube as render_youtube

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``lesson`` command group."""
    parser = subparsers.add_parser(
        "lesson",
        help="Stage, summarise, and publish a lesson.",
        description=(
            "Baton renders the document; a model only supplies validated data. "
            "Run `lesson contract` to get the schema and context, then `ingest`."
        ),
    )
    group = parser.add_subparsers(dest="lesson_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    stage = group.add_parser("stage", help="Start a draft, collecting context automatically.")
    stage.add_argument("name", metavar="NAME")
    stage.add_argument(
        "--session",
        type=int,
        default=None,
        help="Session number. Defaults to the next unstarted, empty one.",
    )
    stage.add_argument("--titles", default="", help="Repertoire covered, for the document.")
    stage.add_argument("--context", default="", help="The teacher's notes for this lesson.")
    stage.add_argument("--context-file", default="", metavar="PATH")
    stage.add_argument(
        "--no-previous",
        action="store_true",
        help="Skip fetching the previous session's summary.",
    )
    stage.set_defaults(handler=handle_stage)

    contract = group.add_parser(
        "contract",
        help="Print the schema and context a model needs to write the summary.",
        description=(
            "Everything required to produce a valid summary, in one document: "
            "the JSON Schema, the lesson context, and the learner's teaching profile."
        ),
    )
    contract.add_argument("name", metavar="NAME")
    contract.set_defaults(handler=handle_contract)

    ingest = group.add_parser(
        "ingest",
        help="Validate and store a model-written summary.",
        description="Exits 4 with every violation if the JSON does not conform. Nothing is stored.",
    )
    ingest.add_argument("name", metavar="NAME")
    ingest.add_argument("--file", metavar="PATH", help="JSON file. Use - for stdin.")
    ingest.add_argument("--json-text", metavar="JSON", help="JSON as a literal argument.")
    ingest.set_defaults(handler=handle_ingest)

    render_cmd = group.add_parser("render", help="Preview the rendered summary.")
    render_cmd.add_argument("name", metavar="NAME")
    render_cmd.add_argument(
        "--format", choices=("markdown", "blocks", "message"), default="markdown"
    )
    render_cmd.set_defaults(handler=handle_render)

    listing = group.add_parser("list", help="Every staged lesson.")
    listing.set_defaults(handler=handle_list)

    show = group.add_parser("show", help="One draft in full.")
    show.add_argument("name", metavar="NAME")
    show.set_defaults(handler=handle_show)

    publish = group.add_parser(
        "publish",
        help="Write the summary onto the session document.",
        description=(
            "Appends the summary and removes the previous replaceable blocks. "
            "Blocks matching docs.preserve — recordings, embeds — are kept."
        ),
    )
    publish.add_argument("name", metavar="NAME")
    publish.add_argument("--dry-run", action="store_true", help="Report what would change.")
    publish.add_argument(
        "--force",
        action="store_true",
        help="Publish again even if this document was already published to.",
    )
    publish.set_defaults(handler=handle_publish)

    remove = group.add_parser("remove", help="Discard a draft.")
    remove.add_argument("name", metavar="NAME")
    remove.set_defaults(handler=handle_remove)

    clear = group.add_parser("clear", help="Discard every draft.")
    clear.add_argument("--yes", action="store_true", help="Required: this deletes drafts.")
    clear.set_defaults(handler=handle_clear)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton lesson` needs a subcommand.",
        remedy="Try `baton lesson list`, or `baton lesson stage <name>`.",
    )


# -- shared plumbing ---------------------------------------------------------


def _staging(ctx: Context) -> StagingStore:
    return StagingStore(ctx.config.state_dir / "lessons")


def _published(ctx: Context) -> PublishedRecord:
    return PublishedRecord(ctx.config.state_dir / "published")


def _resolve(ctx: Context, store, name: str):
    return resolve_learner(
        name,
        store.list_learners(),
        aliases=ctx.config.get("db.aliases", {}) or {},
        label=ctx.config.label("learner"),
    )


def _capture_piece_snapshot(store: LearnerStore, learner: Learner) -> PieceSnapshot:
    """Read the assigned Song DB row once, at the lesson boundary."""
    if learner.current_piece_id is None:
        return PieceSnapshot.capture(None)
    piece = store.get_piece(learner.current_piece_id)
    if piece is None:
        raise UsageError(
            f'{learner.name} is assigned missing piece id "{learner.current_piece_id}".',
            remedy="Fix or clear the learner's current piece, then re-stage the lesson.",
        )
    return PieceSnapshot.capture(piece)


def _short_summary_rules(ctx: Context) -> dict[str, Any]:
    rules = ctx.config.section("summary.short_summary")
    return {
        "max_lines": int(rules.get("max_lines", 5)),
        "allow_emoji": bool(rules.get("allow_emoji", False)),
        "allow_links": bool(rules.get("allow_links", False)),
    }


def _theory(ctx: Context) -> dict[str, str]:
    """Theory callouts from the profile, as ``{id: text}``.

    Lives in the profile rather than the package: a studio's teaching notes are
    its own, and in the private deployment they are the material that must not
    be published.
    """
    path = ctx.config.profile_dir / "theory.json"
    if not path.is_file():
        return {}
    from ..core import jsonio

    data = jsonio.read_json(path, {})
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _titles_from(summary: dict[str, Any] | None) -> str:
    """What the session covered, as one line for the document's own column.

    Falls back to the topics the summary already lists, because the column is
    read by `prep` and by anyone scanning the course at a glance — and a
    studio that never passes `--titles` would otherwise leave it empty
    forever.
    """
    if not summary:
        return ""
    topics = [
        str(entry.get("topic", "")).strip()
        for entry in summary.get("covered", [])
        if isinstance(entry, dict) and str(entry.get("topic", "")).strip()
    ]
    return ", ".join(topics)


def _update_youtube_description(
    ctx: Context, docs, doc_id: str, learner, draft: LessonDraft
) -> dict[str, Any] | None:
    """Best-effort: put the just-published summary on the lesson's YouTube
    video description too, the way the studio's previous pipeline did.

    Returns ``None`` when there was nothing to do (no YouTube configured, no
    video on the document yet, or the link on the document is not a YouTube
    URL at all) — that is the ordinary case for most sessions, not a failure.
    A configured-but-failing update (wrong owner, API error) is reported but
    never raised: the document is already published, and this is a nice-to-
    have on top of it, not a reason to make the command exit non-zero.
    """
    video_link = find_video_link(docs, doc_id)
    video_id = extract_video_id(video_link) if video_link else None
    if not video_id:
        return None

    date = ""
    with contextlib.suppress(BatonError):  # cosmetic; the description renders without it
        date = docs.get_status(doc_id).date

    description = render_youtube.format_description(
        draft.summary or {},
        instrument=learner.instrument,
        week=draft.session_number,
        student_name=learner.name,
        date=date,
    )
    try:
        # Credentials are resolved lazily (on first API call, not on
        # construction), so a studio that never configured YouTube only
        # surfaces `ConfigError` here — not from `open_publisher` itself.
        publisher = open_publisher(ctx.config)
        publisher.update_description(video_id, description)
    except ConfigError:
        # This studio has not configured YouTube at all — nothing to update.
        return None
    except BatonError as exc:
        return {"status": "error", "video_id": video_id, "error": str(exc)}
    return {"status": "ok", "video_id": video_id}


def _finish_session(
    ctx: Context,
    publisher: SummaryPublisher,
    staging: StagingStore,
    draft: LessonDraft,
    learner_name: str,
    *,
    resumed: bool = False,
) -> Exit:
    """Mark a published session done on its document.

    Separate from writing the blocks because the two can fail independently:
    the summary is on the page for good once it is appended, while this write
    can be retried without consequence. Recording which one happened is what
    lets `publish` be re-run to finish a session without appending a second
    copy of the same summary.
    """
    label = ctx.config.label("session")
    try:
        written = publisher.complete(
            draft.doc_id,
            date=today_in(ctx.config.timezone).isoformat(),
            titles=draft.titles or _titles_from(draft.summary),
        )
    except Exception as exc:
        draft.note_target("docs", completed=False, complete_error=str(exc))
        staging.save(draft)
        raise UpstreamError(
            f"{learner_name}'s summary is on the page, but {label} "
            f"{draft.session_number} could not be marked done: {exc}",
            service="docs",
            remedy=f'Re-run `baton lesson publish "{learner_name}"`. The summary is '
            f"already published, so a re-run only finishes the {label} — it will "
            "not append a second copy.",
        ) from exc

    draft.note_target("docs", completed=written)
    staging.save(draft)

    if resumed:
        ctx.report.result(
            {**draft.summary_view(), "completed": written},
            human=f"{learner_name} — {label} {draft.session_number} was already "
            f"published; marked it done now.",
        )
    return Exit.OK


def _read_payload(ctx: Context) -> Any:
    """Read the model's JSON from --file, stdin, or --json-text."""
    source = ctx.args.file
    text: str

    if ctx.args.json_text and source:
        raise UsageError(
            "Pass either --file or --json-text, not both.",
            remedy="Choose one source for the summary.",
        )
    if ctx.args.json_text:
        text = ctx.args.json_text
    elif source == "-":
        import sys

        text = sys.stdin.read()
    elif source:
        path = Path(source).expanduser()
        if not path.is_file():
            raise UsageError(
                f"No such file: {path}",
                remedy="Check the path, or pipe the JSON in with `--file -`.",
            )
        text = path.read_text(encoding="utf-8")
    else:
        raise UsageError(
            "`lesson ingest` needs the summary JSON.",
            remedy="Pass --file <path>, --file - to read stdin, or --json-text.",
        )

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Malformed JSON is a contract failure, not a usage error: the model
        # produced it and the model is what has to fix it.
        from ..errors import ContractError

        raise ContractError(
            f"The summary is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno}).",
            violations=[{"path": "/", "reason": f"invalid JSON at line {exc.lineno}"}],
            remedy="Return only the JSON object, with no prose around it.",
        ) from exc


# -- handlers ----------------------------------------------------------------


def handle_stage(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)

        context = ctx.args.context
        if ctx.args.context_file:
            path = Path(ctx.args.context_file).expanduser()
            if not path.is_file():
                raise UsageError(f"No such file: {path}")
            context = path.read_text(encoding="utf-8")

        session_number: int
        doc_id: str
        titles = ctx.args.titles
        previous_context = ""

        if ctx.args.session is not None:
            # An explicitly named session needs no document reads at all: the
            # number and the document id both come from the database. Staging
            # then works during a document-store outage, which matters because
            # the notes are usually written straight after the lesson.
            session = store.get_session(learner.id, ctx.args.session)
            if session is None:
                raise UsageError(
                    f"{learner.name} has no {ctx.config.label('session')} "
                    f"numbered {ctx.args.session}.",
                    remedy=f'Run `baton learner sessions "{learner.name}"` to see what exists.',
                )
            session_number = session.number
            doc_id = session.doc_id
        else:
            # Choosing for the caller does require reading the documents:
            # "free" is a fact about the page, not about the database row.
            history = LearnerHistory(
                store,
                open_docs(ctx.config),
                StatusVocabulary.from_config(ctx.config.section("docs.statuses")),
                max_parallel_reads=int(ctx.config.get("docs.max_parallel_reads", 4)),
            )
            views = history.sessions(learner)
            chosen = history.next_empty(views)
            if chosen is None:
                raise UsageError(
                    f"{learner.name} has no free {ctx.config.label('session')} to stage.",
                    remedy="Pass --session explicitly, or create the next one first.",
                )
            session_number = chosen.number
            doc_id = chosen.session.doc_id
            titles = titles or chosen.doc.titles

            if not ctx.args.no_previous:
                previous = history.latest_done(views)
                if previous is not None:
                    record = _published(ctx).get(learner.id, previous.number)
                    if record:
                        previous_context = str(record.get("short_message", ""))

        if ctx.args.session is not None and not ctx.args.no_previous:
            # Read the previous summary from Baton's own records rather than
            # from the document store, so this path stays offline too.
            record = _published(ctx).latest(learner.id)
            if record and int(record.get("session_number", 0)) < session_number:
                previous_context = str(record.get("short_message", ""))

        piece_snapshot = _capture_piece_snapshot(store, learner)
        draft = LessonDraft(
            learner_id=learner.id,
            learner_name=learner.name,
            session_number=session_number,
            piece_snapshot=piece_snapshot,
            doc_id=doc_id,
            titles=titles,
            context=context,
            previous_context=previous_context,
        )
    finally:
        store.close()

    _staging(ctx).save(draft)
    label = ctx.config.label("session")
    ctx.report.result(
        draft.summary_view(),
        human=f"Staged {learner.name} — {label} {draft.session_number}\n"
        f'  next: baton lesson contract "{learner.name}"',
    )
    return Exit.OK


def handle_contract(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    draft = _staging(ctx).require(learner.id, learner.name)
    learner_context = learner.to_dict()
    learner_context.pop("current_piece_id", None)
    piece = draft.piece_snapshot.piece
    rules = _short_summary_rules(ctx)
    theory = _theory(ctx)

    payload = {
        "schema": contracts.load_schema(contracts.LESSON_SUMMARY),
        "context": {
            "learner": learner_context,
            "session_number": draft.session_number,
            "titles": draft.titles,
            "current_piece": piece.to_dict() if piece else None,
            "lesson_notes": draft.context,
            "previous_session_summary": draft.previous_context,
        },
        "constraints": {
            "short_summary": rules,
            "available_callout_ids": sorted(theory),
            "language": ctx.config.locale,
        },
        "instructions": [
            "Return one JSON object matching `schema`, and nothing else.",
            "Base every statement on `lesson_notes`. Do not invent progress.",
            "Say what is still difficult plainly; pair each difficulty with a fix.",
            "Use `previous_session_summary` to judge what is new, not to repeat it.",
            "Only use callout ids from `available_callout_ids`; never write theory text.",
            f"Write in the language of this profile ({ctx.config.locale}).",
        ],
    }

    ctx.report.result(
        payload,
        human=json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return Exit.OK


def handle_ingest(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    staging = _staging(ctx)
    draft = staging.require(learner.id, learner.name)
    payload = _read_payload(ctx)
    theory = _theory(ctx)

    # Raises ContractError (exit 4) with every violation. Nothing below runs
    # until the payload is acceptable, so a rejected summary is never stored.
    summary = contracts.validate_lesson_summary(
        payload,
        known_callouts=set(theory) if theory else None,
        **_short_summary_rules(ctx),
    )

    draft.summary = summary
    draft.status = SUMMARISED
    staging.save(draft)

    ctx.report.result(
        draft.summary_view(),
        human=f"Accepted summary for {learner.name} — "
        f"{ctx.config.label('session')} {draft.session_number}\n"
        f'  next: baton lesson render "{learner.name}"',
    )
    return Exit.OK


def handle_render(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    draft = _staging(ctx).require(learner.id, learner.name)
    if draft.summary is None:
        raise UsageError(
            f"No summary has been accepted for {learner.name} yet.",
            remedy=f'Run `baton lesson contract "{learner.name}"`, then `ingest`.',
        )

    sections = ctx.config.get("summary.sections", {})
    theory = _theory(ctx)

    if ctx.args.format == "blocks":
        blocks = render.to_blocks(
            draft.summary,
            sections=sections,
            callout_texts=theory,
            callout_icon=str(ctx.config.get("summary.callout_icon", "")),
        )
        ctx.report.result(
            {"blocks": blocks, "count": len(blocks)},
            human=json.dumps(blocks, ensure_ascii=False, indent=2),
        )
        return Exit.OK

    if ctx.args.format == "message":
        message = render.short_message(
            draft.summary,
            bullet=str(ctx.config.get("summary.short_summary.bullet", "•")),
            labels=ctx.config.get("summary.short_summary.labels", {}),
        )
        ctx.report.result({"message": message}, human=message)
        return Exit.OK

    markdown = render.to_markdown(draft.summary, sections=sections, callout_texts=theory)
    ctx.report.result({"markdown": markdown}, human=markdown)
    return Exit.OK


def handle_list(ctx: Context) -> Exit:
    drafts = _staging(ctx).list()
    payload = {"lessons": [draft.summary_view() for draft in drafts], "count": len(drafts)}

    if not drafts:
        ctx.report.result(payload, human="No lessons staged.")
        return Exit.OK

    label = ctx.config.label("session")
    width = max(len(draft.learner_name) for draft in drafts)
    lines = [
        f"  {draft.learner_name:<{width}}  {label} {draft.session_number:<3} {draft.status}"
        + ("  summary ✓" if draft.summary else "  summary —")
        for draft in drafts
    ]
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_show(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    draft = _staging(ctx).require(learner.id, learner.name)
    ctx.report.result(
        draft.to_dict(), human=json.dumps(draft.to_dict(), ensure_ascii=False, indent=2)
    )
    return Exit.OK


def handle_publish(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    staging = _staging(ctx)
    draft = staging.require(learner.id, learner.name)

    if draft.summary is None:
        raise UsageError(
            f"No summary has been accepted for {learner.name} yet.",
            remedy=f'Run `baton lesson contract "{learner.name}"`, then `ingest`.',
        )
    if not draft.doc_id:
        raise UsageError(
            f"{ctx.config.label('session').capitalize()} {draft.session_number} for "
            f"{learner.name} has no document.",
            remedy="Add the document id to the session record, then re-run.",
        )

    # Two places remember a publish, and only one of them survives. The draft
    # is a single file per learner that `stage` overwrites wholesale, so a
    # studio that re-stages the same session to fix a title clears the mark and
    # the gate opens again. The published record is written per session and no
    # `stage` touches it, so it is the one that can still answer tomorrow.
    published = draft.target_done("docs") or (
        _published(ctx).get(learner.id, draft.session_number) is not None
    )

    publisher = SummaryPublisher(
        open_docs(ctx.config),
        PreservePolicy.from_config(ctx.config.get("docs.preserve", [])),
        sections=ctx.config.get("summary.sections", {}),
        callout_icon=str(ctx.config.get("summary.callout_icon", "")),
    )
    theory = _theory(ctx)
    label = ctx.config.label("session")

    if ctx.args.dry_run:
        plan = publisher.plan(draft.doc_id, draft.summary, callout_texts=theory)
        ctx.report.result(
            {**plan, "dry_run": True},
            human=f"Would append {plan['would_append']} blocks, delete "
            f"{plan['would_delete']}, and keep {plan['would_preserve']} "
            f"({', '.join(plan['preserved_types']) or 'nothing protected'}), then "
            f"mark the {label} done.",
        )
        return Exit.OK

    if published and not ctx.args.force:
        # Appending the same summary twice leaves two copies on the page and
        # nothing to tell them apart, so a repeat is refused rather than done.
        # Finishing the session is a separate write, though, and if that is
        # what failed last time then re-running is exactly how to fix it —
        # the blocks are already where they belong.
        docs_state = draft.targets.get("docs", {})
        if docs_state.get("status") == "ok" and not docs_state.get("completed"):
            return _finish_session(ctx, publisher, staging, draft, learner.name, resumed=True)

        ctx.report.result(
            {**draft.summary_view(), "skipped": "already published"},
            human=f"{learner.name} — {label} {draft.session_number} "
            "was already published. Use --force to publish again.",
        )
        return Exit.OK

    ctx.report.step(f"publishing {learner.name} — {label} {draft.session_number}")
    try:
        result = publisher.publish(draft.doc_id, draft.summary, callout_texts=theory)
    except Exception as exc:
        draft.record_target("docs", "failed", error=str(exc))
        staging.save(draft)
        raise

    draft.record_target("docs", "ok", appended=result.appended, deleted=result.deleted)
    draft.status = PUBLISHED
    staging.save(draft)

    message = render.short_message(
        draft.summary,
        bullet=str(ctx.config.get("summary.short_summary.bullet", "•")),
        labels=ctx.config.get("summary.short_summary.labels", {}),
    )
    _published(ctx).save(draft, short_message=message, doc_url=result.doc_url)

    youtube_result = _update_youtube_description(
        ctx, open_docs(ctx.config), draft.doc_id, learner, draft
    )
    if youtube_result is not None:
        status = youtube_result["status"]
        extra = {key: value for key, value in youtube_result.items() if key != "status"}
        draft.record_target("youtube", status, **extra)
        staging.save(draft)

    _finish_session(ctx, publisher, staging, draft, learner.name)

    youtube_line = ""
    if youtube_result and youtube_result["status"] == "ok":
        youtube_line = "\n  YouTube description updated"
    elif youtube_result and youtube_result["status"] == "error":
        youtube_line = f"\n  YouTube description NOT updated: {youtube_result['error']}"

    ctx.report.result(
        {
            **result.to_dict(),
            "completed": draft.targets["docs"].get("completed", {}),
            "youtube": youtube_result,
        },
        human=f"Published {learner.name} — {label} "
        f"{draft.session_number}\n"
        f"  appended {result.appended}, removed {result.deleted}, kept {result.preserved}\n"
        f"  marked the {label} done"
        f"{youtube_line}",
    )
    return Exit.OK


def handle_remove(ctx: Context) -> Exit:
    store = open_store(ctx.config)
    try:
        learner = _resolve(ctx, store, ctx.args.name)
    finally:
        store.close()

    removed = _staging(ctx).remove(learner.id)
    ctx.report.result(
        {"learner": learner.to_dict(), "removed": removed},
        human=f"Discarded the draft for {learner.name}."
        if removed
        else f"No draft was staged for {learner.name}.",
    )
    return Exit.OK


def handle_clear(ctx: Context) -> Exit:
    if not ctx.args.yes:
        raise UsageError(
            "`lesson clear` discards every staged draft.",
            remedy="Re-run with --yes if that is what you want.",
        )
    removed = _staging(ctx).clear()
    ctx.report.result({"removed": removed}, human=f"Discarded {removed} draft(s).")
    return Exit.OK
