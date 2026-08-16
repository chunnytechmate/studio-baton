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

Handler = Callable[["Context"], Exit]


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
        cmd_notes,
        cmd_job,
    ):
        module.register(subparsers)

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv``, dispatch, and translate the outcome into an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

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
