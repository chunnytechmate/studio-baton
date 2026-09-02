"""The ``baton`` command-line entry point.

One shell owns argument parsing, configuration loading, error rendering, and
the exit code. Command modules contribute a parser and a handler and do nothing
else: they never print failures and never call ``sys.exit``. That is what
keeps the exit contract in :mod:`baton.exits` true for every command rather
than true for the commands someone remembered to wire up correctly.
"""

from __future__ import annotations

import argparse
import signal
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, NoReturn

from .. import __version__
from ..adapters.db.fallback import degradation_notices
from ..core import config as config_module
from ..core.i18n import Translator, translator
from ..core.output import Reporter, format_error
from ..errors import BatonError, UsageError
from ..exits import SLUG, Exit

Handler = Callable[["Context"], "Exit | int"]


class _ParseFailure(Exception):
    pass


class _Terminated(Exception):
    """SIGTERM arrived. Raised from the handler so the envelope still happens."""


@contextmanager
def _terminate_as_exception() -> Any:
    """Turn SIGTERM into an exception for the duration of a command.

    An agent harness caps how long one call may run and kills what overruns:
    that is the ordinary end of a long Baton command, not an exotic one. The
    default disposition kills the process outright, so stdout stays empty and
    the caller cannot tell "cut short" from "crashed before printing". Raising
    instead lets the same envelope every other failure produces come out.

    Restored on the way out, and skipped entirely off the main thread, where
    ``signal.signal`` raises. The supervisor in :mod:`baton.core.jobs` installs
    its own SIGTERM handler for its own child; this one is for the foreground.
    """
    try:
        previous = signal.getsignal(signal.SIGTERM)
    except (ValueError, OSError, AttributeError):  # pragma: no cover - exotic platform
        yield
        return

    def _raise(_signum: int, _frame: Any) -> None:
        raise _Terminated()

    try:
        signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError):  # pragma: no cover - not the main thread
        yield
        return
    try:
        yield
    finally:
        with suppress(ValueError, OSError):
            signal.signal(signal.SIGTERM, previous)


def _force_utf8_streams() -> None:
    """Make stdout and stderr carry Thai whatever the environment claims.

    Baton's output is Thai as often as English, and ``json.dumps`` is called
    with ``ensure_ascii=False`` on purpose: a parent reads the message, not an
    escape sequence. A stream that cannot encode Thai therefore does not
    degrade, it raises, and the whole document is lost after the work already
    happened.

    Python already coerces a bare C/POSIX locale to UTF-8, so this only matters
    when something set the encoding explicitly: ``PYTHONIOENCODING=ascii`` in a
    container image, or a genuinely non-UTF-8 locale. ``backslashreplace``
    rather than plain UTF-8 for stderr: diagnostics must never be the thing
    that kills a run.
    """
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with suppress(Exception):
            reconfigure(encoding="utf-8", errors=errors)


class _ArgumentParser(argparse.ArgumentParser):
    """Raise parse failures so the CLI can preserve its exit/JSON contract."""

    def error(self, message: str) -> NoReturn:
        raise _ParseFailure(message)


@dataclass
class Context:
    """Everything a command handler is given."""

    args: argparse.Namespace
    report: Reporter
    _config: config_module.Config | None = None
    _translator: Translator | None = None

    @property
    def config(self) -> config_module.Config:
        """The effective configuration, loaded on first use.

        Loading lazily means ``baton --help`` and ``baton --version`` work in a
        directory with no profile, which is where most people meet the tool.
        """
        if self._config is None:
            self._config = config_module.load(getattr(self.args, "profile", None))
        return self._config

    @property
    def t(self) -> Translator:
        """Translator for the configured locale."""
        if self._translator is None:
            try:
                locale = self.config.locale
            except BatonError:
                locale = "en"
            self._translator = translator(locale)
        return self._translator


_PROFILE_HELP = (
    "Profile directory or baton.yaml to use (default: $BATON_PROFILE, then ./baton.yaml)."
)
_JSON_HELP = "Emit a single JSON document on stdout. Use this when driving Baton from an agent."
_QUIET_HELP = "Suppress progress output on stderr."


def global_flags() -> argparse.ArgumentParser:
    """A fresh parent parser holding the flags every command accepts.

    `baton learner list --json` is what a person types and what every skill
    documents, so it must work as well as `baton --json learner list`.

    Two details make that safe, and both were learned by getting them wrong:

    The copies default to ``SUPPRESS``, so a subcommand that was not given the
    flag leaves the namespace alone. Without it, the subparser's own default
    overwrites whatever the top level already parsed, and the leading form
    silently stops working.

    And this is a *function*, not a shared instance. ``parents=`` copies actions
    by reference, and ``set_defaults`` mutates ``action.default`` in place, so
    one ``set_defaults`` call on the top-level parser would erase the SUPPRESS
    on every subparser at once, which is exactly the bug this comment exists to
    prevent a repeat of.
    """
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--profile", metavar="PATH", default=argparse.SUPPRESS, help=_PROFILE_HELP)
    shared.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        default=argparse.SUPPRESS,
        help=_JSON_HELP,
    )
    shared.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS, help=_QUIET_HELP)
    return shared


def _with_global_flags(action: argparse._SubParsersAction) -> argparse._SubParsersAction:
    """Make every parser this action creates inherit the global flags.

    Done by wrapping ``add_parser`` rather than asking each command module to
    remember: a module that forgot would produce one command where `--json`
    silently does not work at the end, which is exactly the kind of
    inconsistency nobody notices until an agent is looping on exit 2.

    Recurses, so `baton learner list --json` and `baton job wait <id> --json`
    both work.
    """
    original = action.add_parser

    def add_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        parents = [*kwargs.pop("parents", []), global_flags()]
        created = original(name, parents=parents, **kwargs)

        nested = created.add_subparsers

        def add_subparsers(**sub_kwargs: Any) -> argparse._SubParsersAction:
            return _with_global_flags(nested(**sub_kwargs))

        created.add_subparsers = add_subparsers
        return created

    action.add_parser = add_parser  # type: ignore[method-assign]
    return action


def build_parser() -> argparse.ArgumentParser:
    """Assemble the top-level parser and register every command group."""
    parser = _ArgumentParser(
        prog="baton",
        description="Scripted operations for a one-to-one teaching studio.",
        epilog="Every command exits 0 on success, 1 for bad invocation, "
        "2 for configuration problems, "
        "3 when a person must choose, 4 on invalid submitted content, "
        "5 when a safety gate blocks the action, 6 on upstream failure, "
        "7 on inconsistent local state, 8 while a background job is still "
        "running or another run holds the lock, 9 when Baton itself crashed, "
        "and 130/143 when it was interrupted or killed.",
    )
    # Not argparse's `action="version"`: that prints prose and raises
    # SystemExit before the JSON machinery is reached, so `--json --version`
    # answered in a format no caller could parse. Version is the one question
    # an agent must ask *before* it trusts anything else: a skill written for
    # a newer Baton meets an older one as an "unknown command" it cannot tell
    # from its own typo.
    parser.add_argument("--version", action="store_true", help="Print the version and exit.")
    # Declared directly rather than inherited: the top level is the one place
    # these carry real defaults, and mixing that with the shared SUPPRESS
    # copies is what broke them last time.
    parser.add_argument("--profile", metavar="PATH", default=None, help=_PROFILE_HELP)
    parser.add_argument(
        "--json", dest="json_mode", action="store_true", default=False, help=_JSON_HELP
    )
    parser.add_argument("--quiet", action="store_true", default=False, help=_QUIET_HELP)

    subparsers = _with_global_flags(parser.add_subparsers(dest="command", metavar="<command>"))

    # Registered here rather than auto-discovered: an explicit list means a
    # half-finished command module cannot accidentally ship as a real command.
    from . import (
        cmd_calendar,
        cmd_config,
        cmd_course,
        cmd_doctor,
        cmd_init,
        cmd_job,
        cmd_learner,
        cmd_lesson,
        cmd_notes,
        cmd_prep,
        cmd_send,
        cmd_song,
        cmd_video,
    )

    for module in (
        cmd_init,
        cmd_doctor,
        cmd_config,
        cmd_learner,
        cmd_song,
        cmd_lesson,
        cmd_send,
        cmd_video,
        cmd_calendar,
        cmd_course,
        cmd_notes,
        cmd_job,
        cmd_prep,
    ):
        module.register(subparsers)

    return parser


def command_names(parser: argparse.ArgumentParser | None = None) -> list[str]:
    """Every invocable command, as the words a caller would type.

    Reported by ``--version`` so a harness can check that the Baton in front of
    it has the commands its skills were written against, instead of discovering
    the mismatch one exit code at a time.

    Read off the parser rather than kept as a list: a hand-maintained inventory
    is exactly the kind that goes stale and answers the question wrongly, which
    is worse than not answering it.
    """
    parser = parser or build_parser()
    names: list[str] = []
    for action in parser._subparsers._group_actions if parser._subparsers else []:
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for name, sub in choices.items():
            if name == "supervise":  # internal; not part of the surface
                continue
            nested = command_names(sub)
            if nested:
                names.extend(f"{name} {child}" for child in nested)
            else:
                names.append(name)
    return names


def _offending_token(argv: Sequence[str]) -> str:
    """Best-effort name of what the person got wrong, for the envelope.

    A flag after a valid command ("learner --nope") is the offender; with no
    such flag, the command itself is ("frobnicate"). Global flags and their
    values are noise either way.
    """
    value_flags = {"--profile"}
    switches = {"--json", "--quiet"}
    cleaned: list[str] = []
    skip_value = False
    for part in argv:
        if skip_value:
            skip_value = False
            continue
        if part in value_flags:
            skip_value = True
            continue
        if part in switches:
            continue
        cleaned.append(part)

    positional = next((part for part in cleaned if not part.startswith("-")), "")
    later_flag = ""
    if positional:
        rest = cleaned[cleaned.index(positional) + 1 :]
        later_flag = next((part for part in rest if part.startswith("-")), "")
    return later_flag or positional or (cleaned[0] if cleaned else "")


def _usage_failure(argv: Sequence[str], parse_message: str = "") -> tuple[Reporter, dict[str, Any]]:
    """A usage error caught at parse time, as the envelope every other
    failure already emits.

    argparse prints its own line to stderr and raises ``SystemExit(2)``;
    left alone, that escapes as a bare exit 2: the code the contract
    reserves for *configuration* problems, which is exactly the
    misdiagnosis an agent branching on exit codes would make.
    """
    # The flags could not be parsed, so --json cannot be known for sure;
    # a --json anywhere in the line is the honest guess.
    report = Reporter(json_mode="--json" in argv, quiet=False)

    try:
        locale = config_module.load(None).locale
    except BatonError:
        locale = "en"
    t = translator(locale)

    offending = _offending_token(argv) or "?"
    message = (
        t("error.unknown_command", command=offending)
        if "invalid choice" in parse_message
        else parse_message or t("error.unknown_command", command=offending)
    )
    payload: dict[str, Any] = {
        "ok": False,
        "error": "usage",
        "exit_code": int(Exit.USAGE),
        "message": message,
        "remedy": t("error.unknown_command.remedy"),
    }
    return report, payload


def run(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv``, dispatch, and translate the outcome into an exit code."""
    parser = build_parser()
    tokens = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        args = parser.parse_args(argv)
    except _ParseFailure as exc:
        report, payload = _usage_failure(tokens, str(exc))
        report.failure(payload, human=format_error(payload))
        return int(Exit.USAGE)

    report = Reporter(json_mode=args.json_mode, quiet=args.quiet)

    if getattr(args, "version", False):
        report.result(
            {"version": __version__, "commands": sorted(command_names())},
            human=f"baton {__version__}",
        )
        return int(Exit.OK)

    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return int(Exit.USAGE)

    context = Context(args=args, report=report)

    try:
        with _terminate_as_exception(), degradation_notices(report.warn):
            return int(handler(context))
    except BatonError as err:
        payload = err.to_dict()
        report.failure(payload, human=format_error(payload))
        return int(err.exit_code)
    except KeyboardInterrupt:
        interrupted: dict[str, Any] = {
            "ok": False,
            "error": SLUG[Exit.INTERRUPTED],
            "exit_code": int(Exit.INTERRUPTED),
            "message": "Interrupted.",
            "remedy": "Nothing was left half-written; re-run when ready. "
            "A resumable pipeline skips the steps that already succeeded.",
        }
        report.failure(interrupted, human=format_error(interrupted))
        return int(Exit.INTERRUPTED)
    except _Terminated:
        terminated: dict[str, Any] = {
            "ok": False,
            "error": SLUG[Exit.TERMINATED],
            "exit_code": int(Exit.TERMINATED),
            "message": "Terminated before the command finished (SIGTERM).",
            "remedy": "The run was cut short, not refused: whether its work "
            "completed is unknown. Check with `baton video status` or the "
            "relevant `show` command before re-running, and prefer `--detach` "
            "for work that outlives one call.",
        }
        report.failure(terminated, human=format_error(terminated))
        return int(Exit.TERMINATED)
    except Exception as err:
        # Without this, an unexpected exception left stdout empty and exited 1:
        # the code reserved for *bad invocation*. An agent branching on the
        # number then reads a bug in Baton as a mistake of its own and loops
        # rewriting arguments that were never wrong.
        crash: dict[str, Any] = {
            "ok": False,
            "error": SLUG[Exit.INTERNAL],
            "exit_code": int(Exit.INTERNAL),
            "message": f"Baton failed unexpectedly: {type(err).__name__}: {err}",
            "remedy": "This is a bug in Baton, not in how the command was "
            "called: re-running with different arguments will not help. "
            "Report it with the traceback in `details`.",
            "details": {
                "exception": type(err).__name__,
                "traceback": traceback.format_exc(),
            },
        }
        # Suppressed: the envelope is a courtesy at this point, and a stdout
        # that is closed or unencodable (one plausible cause of the crash
        # itself) must not turn the report of a failure into a second one.
        with suppress(Exception):
            report.failure(crash, human=f"{format_error(crash)}\n\n{crash['details']['traceback']}")
        return int(Exit.INTERNAL)


def main() -> None:
    """Console-script entry point."""
    _force_utf8_streams()
    sys.exit(run())


__all__ = [
    "Context",
    "Handler",
    "UsageError",
    "build_parser",
    "command_names",
    "main",
    "run",
]
