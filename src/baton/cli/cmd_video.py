"""``baton video`` — collect recordings, combine, publish, link back.

The long one. It takes the whole-run lock so two runs cannot collide over the
same clips, and ``--detach`` hands it to the supervisor from ``baton job`` so
it outlives the session that started it.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from ..adapters.db import open_store
from ..adapters.docs import open_docs
from ..adapters.media import encode_profile, open_encoder, open_publisher, open_source
from ..core.jobs import JobRunner, run_lock
from ..domain.models import Learner
from ..domain.status import StatusVocabulary
from ..errors import UsageError
from ..exits import Exit
from ..pipelines.learner import LearnerHistory
from ..pipelines.video import VideoJobStore, VideoPipeline

if TYPE_CHECKING:
    from .app import Context

_MARK = {"done": "✓", "failed": "✗", "skipped": "·", "in_progress": "▶"}


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``video`` command group."""
    parser = subparsers.add_parser(
        "video",
        help="Collect recordings, combine them, publish, and link them back.",
        description=(
            "Resumable and safe to re-run: a completed upload is never repeated, "
            "and source clips are only trashed once everything else has succeeded."
        ),
    )
    group = parser.add_subparsers(dest="video_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    run_cmd = group.add_parser("run", help="Process every learner with clips waiting.")
    run_cmd.add_argument(
        "--learner",
        action="append",
        dest="only",
        metavar="FOLDER",
        help="Restrict to these source folders. Repeatable.",
    )
    run_cmd.add_argument(
        "--detach",
        action="store_true",
        help="Run in the background under `baton job` and return at once.",
    )
    run_cmd.add_argument("--dry-run", action="store_true", help="Report what would be processed.")
    run_cmd.set_defaults(handler=handle_run)

    resume = group.add_parser(
        "resume",
        help="Continue jobs that did not finish, without collecting new clips.",
        description=(
            "Continues unfinished jobs and re-trashes source clips a completed "
            "job's record claims were already moved. Never starts a new "
            "session's job — new clips are `video run`'s to collect."
        ),
    )
    resume.add_argument("--detach", action="store_true")
    resume.set_defaults(handler=handle_resume)

    status = group.add_parser("status", help="Show every recorded job and its progress.")
    status.set_defaults(handler=handle_status)

    forget = group.add_parser(
        "forget",
        help="Discard a job record so the next run starts it from scratch.",
        description=(
            "Only for a job you have audited. If the upload already happened, "
            "starting over will publish a second copy."
        ),
    )
    forget.add_argument("folder", metavar="FOLDER")
    forget.add_argument("--yes", action="store_true", help="Required: this discards progress.")
    forget.set_defaults(handler=handle_forget)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton video` needs a subcommand.",
        remedy="Try `baton video run`, or `baton video status`.",
    )


def _jobs(ctx: Context) -> VideoJobStore:
    return VideoJobStore(ctx.config.state_dir / "video")


def _detach(ctx: Context, argv: list[str]) -> Exit:
    """Re-run this command under the job supervisor and return at once."""
    runner = JobRunner(ctx.config.state_dir, ctx.config.config_file)
    command = [sys.executable, "-m", "baton", "--profile", str(ctx.config.config_file), *argv]
    info = runner.spawn(command, name="video")
    ctx.report.result(
        info.to_dict(),
        human=f"▶ job {info.id} started in the background\n"
        f"    wait : baton job wait {info.id}\n"
        f"    logs : baton job logs {info.id}",
    )
    return Exit.OK


def session_for_recording(history: LearnerHistory, learner: Learner) -> tuple[int, str] | None:
    """Which lesson this learner's recording belongs to, read from the status.

    The status is the record of what happened, so it is the only thing asked.
    A booked lesson is "In progress" and is the answer while it lasts. Once
    `lesson publish` marks it "Done" the recording still belongs to that
    lesson, so the most recently finished one answers next: encoding outlasts
    the writing of a summary often enough that video running last is ordinary
    rather than a mistake.

    "Not started" is never the answer. It is a page nobody has taught against,
    and the fallback here used to pick exactly that. Publishing turned the real
    session Done, which left nothing in progress, so the recording went onto
    next week's empty page and the lesson it belonged to was reported as having
    no video, after the upload had already succeeded.

    Nothing here writes a status. Only `lesson publish` moves a session to
    Done; this reads what that left behind.

    Returns:
        The session number and its document id, or ``None`` when no session
        says it happened. Guessing at that point is what this exists to stop.
    """
    views = history.sessions(learner)
    active = history.in_progress(views)
    chosen = active[0] if active else history.latest_done(views)
    return (chosen.number, chosen.session.doc_id) if chosen else None


def _build(ctx: Context) -> VideoPipeline:
    """Assemble the pipeline from configuration."""
    config = ctx.config
    store = open_store(config)
    docs = open_docs(config)
    history = LearnerHistory(
        store,
        docs,
        StatusVocabulary.from_config(config.section("docs.statuses")),
        max_parallel_reads=int(config.get("docs.max_parallel_reads", 4)),
    )

    def resolve_session(learner: Learner) -> tuple[int, str] | None:
        return session_for_recording(history, learner)

    return VideoPipeline(
        source=open_source(config),
        encoder=open_encoder(config),
        publisher=open_publisher(config),
        store=store,
        docs=docs,
        jobs=_jobs(ctx),
        workdir=config.state_dir / "video-work",
        profile=encode_profile(config),
        privacy=str(config.get("media.youtube.privacy", "unlisted")),
        session_label=config.label("session"),
        resolve_session=resolve_session,
    )


def _report(ctx: Context, jobs: list, *, verb: str) -> Exit:
    """Render a run's outcome and pick the exit code."""
    payload: dict[str, Any] = {
        "jobs": [job.to_dict() for job in jobs],
        "processed": len(jobs),
        "done": sum(1 for job in jobs if job.status == "done"),
        "failed": sum(1 for job in jobs if job.status == "failed"),
        "skipped": sum(1 for job in jobs if job.status == "skipped"),
    }

    if not jobs:
        ctx.report.result(payload, human="Nothing to process.")
        return Exit.OK

    lines = [f"{verb} {len(jobs)}: {payload['done']} done, {payload['failed']} failed"]
    for job in jobs:
        mark = _MARK.get(job.status, "·")
        detail = job.error or job.video_url or ""
        lines.append(f"  {mark} {job.learner_folder:<20} {job.status:<12} {detail}")
    ctx.report.result(payload, human="\n".join(lines))

    # A partial run is not a success. Its exit code has to say so, while the
    # per-learner report says which ones actually went.
    return Exit.OK if not payload["failed"] else Exit.UPSTREAM


def handle_run(ctx: Context) -> Exit:
    if ctx.args.detach:
        argv = ["video", "run"]
        for folder in ctx.args.only or []:
            argv += ["--learner", folder]
        return _detach(ctx, argv)

    pipeline = _build(ctx)

    if ctx.args.dry_run:
        pending = pipeline.source.list_pending()
        grouped: dict[str, int] = {}
        for clip in pending:
            grouped[clip.learner_folder] = grouped.get(clip.learner_folder, 0) + 1
        payload = {"pending": grouped, "clips": len(pending), "dry_run": True}
        lines = [f"{len(pending)} clip(s) waiting:"] + [
            f"  {folder:<20} {count}" for folder, count in sorted(grouped.items())
        ]
        ctx.report.result(payload, human="\n".join(lines) if grouped else "No clips waiting.")
        return Exit.OK

    # Held for the whole run: two encoders writing one work directory, or two
    # uploads of the same clips, is exactly what this prevents.
    with run_lock(ctx.config.state_dir, "video"):
        ctx.report.step("collecting clips")
        jobs = pipeline.run(only=ctx.args.only)
    return _report(ctx, jobs, verb="processed")


def handle_resume(ctx: Context) -> Exit:
    if ctx.args.detach:
        return _detach(ctx, ["video", "resume"])

    pipeline = _build(ctx)
    with run_lock(ctx.config.state_dir, "video"):
        jobs = pipeline.resume()
        waiting = pipeline.waiting_clips()
    if not jobs and waiting:
        # "Nothing to process." here once sent the operator to bed while a
        # later `run` found clips waiting the whole time.
        ctx.report.result(
            {
                "jobs": [],
                "processed": 0,
                "done": 0,
                "failed": 0,
                "skipped": 0,
                "waiting_clips": waiting,
            },
            human=(
                f"Nothing to resume, but {waiting} clip(s) are waiting under "
                "folders with no unfinished job.\n"
                "    collect : baton video run"
            ),
        )
        return Exit.OK
    return _report(ctx, jobs, verb="resumed")


def handle_status(ctx: Context) -> Exit:
    from ..pipelines.video import STEPS

    jobs = _jobs(ctx).list()
    payload = {"jobs": [job.to_dict() for job in jobs], "count": len(jobs)}

    if not jobs:
        ctx.report.result(payload, human="No video jobs recorded.")
        return Exit.OK

    lines = []
    for job in jobs:
        mark = _MARK.get(job.status, "·")
        progress = "".join("#" if job.done(step) else "." for step in STEPS)
        lines.append(f"  {mark} {job.learner_folder:<20} {progress}  {job.status}")
        if job.error:
            lines.append(f"      {job.error}")
        # Naming the first unfinished step is the whole question an operator
        # has when a job stops; a bar of dots alone does not answer it.
        pending = next((step for step in STEPS if not job.done(step)), "")
        if pending and job.status != "done":
            lines.append(f"      next step: {pending}")
    lines.append("")
    lines.append("  steps: " + " → ".join(STEPS))
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_forget(ctx: Context) -> Exit:
    jobs = _jobs(ctx)
    job = jobs.get(ctx.args.folder)

    if job is None:
        raise UsageError(
            f"No video job recorded for `{ctx.args.folder}`.",
            remedy="Run `baton video status` to see the folders that exist.",
        )
    if not ctx.args.yes:
        warning = ""
        if job.video_id:
            warning = (
                f" This job already uploaded {job.video_id}; starting over will "
                "publish a second copy."
            )
        raise UsageError(
            f"`video forget` discards the recorded progress for {ctx.args.folder}.{warning}",
            remedy="Re-run with --yes if that is what you want.",
        )

    jobs.remove(ctx.args.folder)
    ctx.report.result(
        {"folder": ctx.args.folder, "removed": True},
        human=f"Discarded the job record for {ctx.args.folder}.",
    )
    return Exit.OK
