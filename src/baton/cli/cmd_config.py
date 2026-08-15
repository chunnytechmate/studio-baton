"""``baton config`` — inspect what the tool actually believes.

Layered configuration is only debuggable if you can see the resolved result.
``config show`` prints exactly the tree every command reads, after defaults,
profile, and environment overrides have been applied.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import yaml

from ..errors import UsageError
from ..exits import Exit

if TYPE_CHECKING:
    from .app import Context


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``config`` command group."""
    parser = subparsers.add_parser(
        "config",
        help="Inspect the effective configuration.",
        description="Show the configuration after defaults, profile, and environment merge.",
    )
    group = parser.add_subparsers(dest="config_command", metavar="<subcommand>")

    show = group.add_parser("show", help="Print the effective configuration.")
    show.add_argument(
        "key",
        nargs="?",
        help="Dotted key to print, for example docs.properties.status. Omit for the whole tree.",
    )
    show.set_defaults(handler=handle_show)

    path = group.add_parser("path", help="Print the paths this profile resolves to.")
    path.set_defaults(handler=handle_path)

    parser.set_defaults(handler=_require_subcommand)


def _require_subcommand(ctx: Context) -> Exit:
    raise UsageError(
        "`baton config` needs a subcommand.",
        remedy="Try `baton config show` or `baton config path`.",
    )


def handle_show(ctx: Context) -> Exit:
    """Print the effective configuration, or one key from it."""
    config = ctx.config
    key = ctx.args.key

    value = config.get(key) if key else config.data

    if ctx.args.json_mode:
        ctx.report.result({"key": key, "value": value, "source": str(config.config_file)})
        return Exit.OK

    if key and not isinstance(value, dict | list):
        ctx.report.result({"key": key, "value": value}, human=str(value))
        return Exit.OK

    rendered = yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False)
    header = ctx.t("config.source", path=config.config_file)
    ctx.report.result({"key": key, "value": value}, human=f"# {header}\n{rendered.rstrip()}")
    return Exit.OK


def handle_path(ctx: Context) -> Exit:
    """Print the profile, config, and state locations."""
    config = ctx.config
    payload = {
        "profile_dir": str(config.profile_dir),
        "config_file": str(config.config_file),
        "state_dir": str(config.state_dir),
    }
    human = "\n".join(f"{name:<12} {value}" for name, value in payload.items())
    ctx.report.result(payload, human=human)
    return Exit.OK
