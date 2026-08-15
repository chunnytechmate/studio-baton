"""``baton job`` — detached execution for work that outlives a session.

Long pipelines are started with ``job spawn`` (or a pipeline's own ``--detach``
flag) and managed with ``list`` / ``status`` / ``wait`` / ``stop`` / ``logs``.
``supervise`` is the internal half that runs inside the detached process; it is
registered but hidden from help.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from ..core.jobs import JobInfo, JobRunner
from ..errors import StateError, UsageError
from ..exits import Exit

if TYPE_CHECKING:
    from .app import Context

_MARKS = {
    "running": "▶",
    "done": "✓",
    "failed": "✗",
    "stopped": "⏹",
    "orphaned": "?",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``job`` command group."""
    parser = subparsers.add_parser(
        "job",
        help="Manage detached background jobs.",
        description="Start long work detached, then check on it, wait for it, or stop it.",
    )
    group = parser.add_subparsers(dest="job_command", metavar="<subcommand>")
    parser.set_defaults(handler=_require_subcommand)

    spawn = group.add_parser(
        "spawn",
        help="Run a command detached under job supervision.",
        description="Start a command in the background and return at once with a job id.",
    )
    spawn.add_argument("--name", help="Label for listings (default: command basename).")
    spawn.add_argument(
        "argv",
        nargs="*",
        metavar="COMMAND",
        help="Command to run. Put `--` before it if it starts with a dash.",
    )
    spawn.set_defaults(handler=handle_spawn)

    listing = group.add_parser("list", help="List jobs, newest first.")
    listing.set_defaults(handler=handle_list)

    status = group.add_parser("status", help="Show one job's lifecycle record.")
    status.add_argument("id", metavar="JOB_ID")
    status.set_defaults(handler=handle_status)

    wait = group.add_parser(
        "wait",
        help="Block until a job finishes.",
        description="Exits with the job's own exit code; 8 if the timeout expires first.",
    )
    wait.add_argument("id", metavar="JOB_ID")
    wait.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Give up after this long and exit 8. Omit to wait forever.",
    )
    wait.set_defaults(handler=handle_wait)

    stop = group.add_parser("stop", help="Ask a running job to stop.")
    stop.add_argument("id", metavar="JOB_ID")
    stop.add_argument(
        "--grace", type=float, default=10.0, metavar="SECONDS", help="Kill after this long."
    )
    stop.set_defaults(handler=handle_stop)

    logs = group.add_parser("logs", help="Print a job's captured output.")
    logs.add_argument("id", metavar="JOB_ID")
    logs.add_argument("--tail", type=int, default=None, metavar="N", help="Last N lines only.")
    logs.set_defaults(handler=handle_logs)

    prune = group.add_parser(
        "prune", help="Delete finished jobs older than a cutoff. Running jobs are kept."
    )
    prune.add_argument("--older-than", type=int, default=14, metavar="DAYS")
    prune.set_defaults(handler=handle_prune)

    # Internal: the process spawned by `job spawn` runs under this. Hidden.
    supervise = group.add_parser("supervise", help=argparse.SUPPRESS)
    supervise.add_argument("--id", required=True)
    supervise.add_argument("argv", nargs="*", metavar="COMMAND")
    supervise.set_defaults(handler=handle_supervise)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton job` needs a subcommand.",
        remedy="Try `baton job spawn -- <command>`, or `baton job list`.",
    )


def _runner(ctx: Context) -> JobRunner:
    config = ctx.config
    return JobRunner(config.state_dir, config.config_file)


def _lookup(ctx: Context, job_id: str) -> JobInfo:
    """Fetch a job, raising a usage error naming the id when it is unknown."""
    info = _runner(ctx).get(job_id)
    if info is None:
        raise UsageError(
            f"No job matches id `{job_id}`.",
            remedy="Run `baton job list` to see the ids that exist.",
        )
    return info


def handle_spawn(ctx: Context) -> Exit:
    argv = list(ctx.args.argv)
    if not argv:
        raise UsageError(
            "`job spawn` needs a command to run.",
            remedy="Example: baton job spawn -- ffmpeg -i in.mp4 out.mp4",
        )
    info = _runner(ctx).spawn(argv, name=ctx.args.name)

    ctx.report.result(
        info.to_dict(),
        human=f"▶ job {info.id} started in the background ({info.name})\n"
        f"    status : baton job status {info.id}\n"
        f"    wait   : baton job wait {info.id}\n"
        f"    logs   : baton job logs {info.id}",
    )
    return Exit.OK


def handle_list(ctx: Context) -> Exit:
    jobs = _runner(ctx).list()
    payload = {"jobs": [job.to_dict() for job in jobs]}

    if not jobs:
        ctx.report.result(payload, human="No jobs recorded.")
        return Exit.OK

    lines = []
    for job in jobs:
        mark = _MARKS.get(job.status, "·")
        detail = job.name
        if job.status in ("done", "failed", "stopped"):
            detail += f"  (exit {job.exit_code})"
        lines.append(f"  {mark} {job.id}  {job.status:<8} {detail}")
    ctx.report.result(payload, human="\n".join(lines))
    return Exit.OK


def handle_status(ctx: Context) -> Exit:
    info = _lookup(ctx, ctx.args.id)

    if info.status == "orphaned":
        # Reported as an error envelope, not a result: the caller must branch
        # on this, and only one JSON document may reach stdout.
        raise StateError(
            f"Job {info.id} died without recording an outcome.",
            remedy="Re-run the command that started it; resumable pipelines "
            "continue from the steps that already succeeded.",
            details={"id": info.id, "job": info.to_dict()},
        )

    lines = [f"job {info.id}", f"  status : {info.status}"]
    if info.pid is not None:
        lines.append(f"  pid    : {info.pid}")
    lines.append(f"  command: {' '.join(info.command)}")
    lines.append(f"  started: {info.started_at}")
    if info.ended_at:
        lines.append(f"  ended  : {info.ended_at}  (exit {info.exit_code})")
    if info.heartbeat_age is not None and info.status == "running":
        lines.append(f"  heartbeat: {info.heartbeat_age:.0f}s ago")
    lines.append(f"  log    : {info.log}")

    ctx.report.result(info.to_dict(), human="\n".join(lines))
    return Exit.OK


def handle_wait(ctx: Context) -> Exit:
    runner = _runner(ctx)
    _lookup(ctx, ctx.args.id)
    info = runner.wait(ctx.args.id, timeout=ctx.args.timeout)
    if info is None:  # pragma: no cover - _lookup proved it exists
        raise UsageError(f"No job matches id `{ctx.args.id}`.")

    if info.status == "running":
        ctx.report.result(
            info.to_dict(),
            human=f"▶ job {info.id} is still running ({ctx.args.timeout:.0f}s waited).",
        )
        return Exit.RUNNING
    if info.status == "orphaned":
        raise StateError(
            f"Job {info.id} died without recording an outcome.",
            remedy="Re-run the command that started it; resumable pipelines "
            "continue from the steps that already succeeded.",
            details={"id": info.id, "job": info.to_dict()},
        )

    mark = _MARKS.get(info.status, "·")
    ctx.report.result(
        info.to_dict(),
        human=f"{mark} job {info.id} {info.status}  (exit {info.exit_code})",
    )
    # The waiter inherits the job's own verdict — including non-zero codes, so
    # an agent waiting on `video run --detach` sees exactly what a foreground
    # run would have exited with.
    return Exit(info.exit_code if info.exit_code is not None else Exit.OK)


def handle_stop(ctx: Context) -> Exit:
    runner = _runner(ctx)
    before = _lookup(ctx, ctx.args.id)
    if before.status in ("done", "failed", "stopped", "orphaned"):
        ctx.report.result(
            before.to_dict(),
            human=f"{_MARKS.get(before.status, '·')} job {before.id} already {before.status}.",
        )
        return Exit.OK

    ctx.report.step(f"stopping job {before.id} (pid {before.pid})")
    info = runner.stop(ctx.args.id, grace=ctx.args.grace)
    ctx.report.result(
        info.to_dict(),
        human=f"⏹ job {info.id} {info.status}  (exit {info.exit_code})",
    )
    return Exit.OK


def handle_logs(ctx: Context) -> Exit:
    _lookup(ctx, ctx.args.id)
    text = _runner(ctx).logs(ctx.args.id, tail=ctx.args.tail)
    ctx.report.result(
        {"id": ctx.args.id, "log": text},
        human=text or "(no output)",
    )
    return Exit.OK


def handle_prune(ctx: Context) -> Exit:
    removed = _runner(ctx).prune(older_than_days=ctx.args.older_than)
    ctx.report.result(
        {"removed": removed, "older_than_days": ctx.args.older_than},
        human=f"Removed {removed} finished job(s) older than {ctx.args.older_than} day(s).",
    )
    return Exit.OK


def handle_supervise(ctx: Context) -> Exit:
    """Internal: run as the detached supervisor and mirror the child's exit."""
    runner = _runner(ctx)
    return Exit(runner.supervise(ctx.args.id, list(ctx.args.argv)))
