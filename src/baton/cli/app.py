"""The ``baton`` command-line entry point.

One shell owns argument parsing, configuration loading, error rendering, and
the exit code. Command modules contribute a parser and a handler and do nothing
else — they never print failures and never call ``sys.exit``. That is what
keeps the exit contract in :mod:`baton.exits` true for every command rather
than true for the commands someone remembered to wire up correctly.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import __version__
from ..core import config as config_module
from ..core.i18n import Translator, translator
from ..core.output import Reporter, format_error
from ..errors import BatonError, UsageError
from ..exits import Exit

Handler = Callable[["Context"], "Exit | int"]


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
    by reference, and ``set_defaults`` mutates ``action.default`` in place — so
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
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Scripted operations for a one-to-one teaching studio.",
        epilog="Every command exits 0 on success, 2 for configuration problems, "
        "3 when a person must choose, 4 on invalid submitted content, "
        "5 when a safety gate blocks the action, 6 on upstream failure, "
        "7 on inconsistent local state, and 8 while a background job is still running.",
    )
    parser.add_argument("--version", action="version", version=f"baton {__version__}")
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
        cmd_send,
        cmd_video,
    )

    for module in (
        cmd_init,
        cmd_doctor,
        cmd_config,
        cmd_learner,
        cmd_lesson,
        cmd_send,
        cmd_video,
        cmd_calendar,
        cmd_course,
        cmd_notes,
        cmd_job,
    ):
        module.register(subparsers)

    return parser


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


def _usage_failure(argv: Sequence[str]) -> tuple[Reporter, dict[str, Any]]:
    """A usage error caught at parse time, as the envelope every other
    failure already emits.

    argparse prints its own line to stderr and raises ``SystemExit(2)``;
    left alone, that escapes as a bare exit 2 — the code the contract
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

    payload: dict[str, Any] = {
        "ok": False,
        "error": "usage",
        "exit_code": int(Exit.USAGE),
        "message": t("error.unknown_command", command=_offending_token(argv) or "?"),
        "remedy": t("error.unknown_command.remedy"),
    }
    return report, payload


def run(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv``, dispatch, and translate the outcome into an exit code."""
    parser = build_parser()
    tokens = list(sys.argv[1:]) if argv is None else list(argv)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code != 2:
            raise  # --help and --version exit 0 by design
        report, payload = _usage_failure(tokens)
        report.failure(payload, human=format_error(payload))
        return int(Exit.USAGE)

    report = Reporter(json_mode=args.json_mode, quiet=args.quiet)

    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return int(Exit.USAGE)

    context = Context(args=args, report=report)

    try:
        return int(handler(context))
    except BatonError as err:
        payload = err.to_dict()
        report.failure(payload, human=format_error(payload))
        return int(err.exit_code)
    except KeyboardInterrupt:
        interrupted: dict[str, Any] = {
            "ok": False,
            "error": "interrupted",
            "exit_code": int(Exit.INTERRUPTED),
            "message": "Interrupted.",
        }
        report.failure(interrupted, human="✗ Interrupted.")
        return int(Exit.INTERRUPTED)


def main() -> None:
    """Console-script entry point."""
    sys.exit(run())


__all__ = ["Context", "Handler", "UsageError", "build_parser", "main", "run"]
