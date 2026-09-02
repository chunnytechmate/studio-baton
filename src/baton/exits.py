"""Exit code contract.

Every ``baton`` command exits with one of these codes. The codes are the
machine-readable half of the CLI's interface: an agent driving Baton branches
on the *number*, never on the wording of a message. That is the whole point:
a small local model cannot reliably parse prose, but it can reliably compare
an integer.

One documented exception: ``job wait`` inherits the exit code of the command it
supervised, which may be anything a wrapped program returns (ffmpeg's 1, a
shell's 127). Mapping those onto a contract code would tell an agent the job
hit a *configuration* problem when it hit no such thing.

Codes are frozen. Adding a new one is a minor release; changing the meaning of
an existing one is a breaking release.
"""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    """Process exit codes, ordered from "fine" to "someone must intervene"."""

    OK = 0
    """Command completed. Any requested writes happened (or `--dry-run` ran clean)."""

    USAGE = 1
    """Bad invocation: unknown command, missing argument, mutually exclusive flags."""

    CONFIG = 2
    """Environment or configuration is incomplete/invalid. Nothing was attempted.

    Fix `baton.yaml` or the environment, then re-run. Never retry as-is.
    """

    NEEDS_HUMAN = 3
    """The command refused to guess and needs a person to choose.

    Raised by identity resolution when a name is ambiguous or unknown. The
    payload always carries a `candidates` list. An agent must surface the
    candidates to the user verbatim and re-run with an exact name: it must
    never pick one itself.
    """

    CONTRACT = 4
    """Model-authored content failed schema validation. Nothing was written.

    The payload carries per-field errors. An agent should correct its JSON and
    call the same command again.
    """

    GATE = 5
    """A fail-closed safety gate blocked the operation. Nothing was sent.

    Required data is missing or inconsistent. There is deliberately no override
    flag: the fix is to supply the missing data, not to force the send.
    """

    UPSTREAM = 6
    """A remote service failed after the configured retries were exhausted.

    Transient by nature: re-running later is reasonable, and resumable
    pipelines will skip the steps that already succeeded.
    """

    STATE = 7
    """Local state is inconsistent and needs an audit before the job can resume.

    Raised when a resumable job's recorded steps contradict what is on disk,
    or when a detached job's supervisor is gone without recording an outcome.
    """

    RUNNING = 8
    """A background job is still running, or is in the way.

    Returned by ``job wait`` when its timeout expires with the job unfinished,
    and by any run that collides with a live run lock. The caller should wait,
    poll, or stop the existing job, never start a second one.

    Payload always carries the job ``id``.
    """

    INTERNAL = 9
    """Baton itself failed: an unexpected exception escaped a command.

    Nothing here is the caller's fault, so re-running the same command with
    different arguments is pointless: this is a bug report. The payload
    carries the exception type and a `traceback` an operator can paste.

    It exists because the alternative was worse: an uncaught exception used to
    exit 1, which the contract reserves for *bad invocation*, so an agent
    branching on the number would read a crash as its own typo and loop trying
    to fix an argument list that was never wrong.
    """

    INTERRUPTED = 130
    """Ctrl-C. Matches the shell convention (128 + SIGINT)."""

    TERMINATED = 143
    """SIGTERM. Matches the shell convention (128 + SIGTERM).

    This is how a command normally dies under an agent harness: the harness
    caps how long one call may run and kills what overruns. Baton catches the
    signal so that death still produces the usual envelope: a caller learns
    the run was cut short rather than reading a silent, empty stdout.
    """


#: Short, stable slugs used in JSON output so callers do not have to map ints.
SLUG: dict[Exit, str] = {
    Exit.OK: "ok",
    Exit.USAGE: "usage",
    Exit.CONFIG: "config",
    Exit.NEEDS_HUMAN: "needs_human",
    Exit.CONTRACT: "contract",
    Exit.GATE: "gate",
    Exit.UPSTREAM: "upstream",
    Exit.STATE: "state",
    Exit.RUNNING: "running",
    Exit.INTERNAL: "internal",
    Exit.INTERRUPTED: "interrupted",
    Exit.TERMINATED: "terminated",
}
