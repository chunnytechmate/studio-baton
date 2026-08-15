"""``baton doctor`` — check the installation before it is trusted with real work.

This is the merged descendant of the per-skill preflight scripts. It runs every
cheap check that can fail at 2am and reports all of them at once, because
finding three problems in one run beats discovering them one re-run at a time.

Doctor never mutates anything and never falls back to a secondary store: its
job is to notice that the primary is down, not to paper over it.
"""

from __future__ import annotations

import argparse
import os
import zoneinfo
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core import i18n
from ..exits import Exit

if TYPE_CHECKING:
    from .app import Context

KNOWN_DB_DRIVERS = ("sqlite", "supabase", "postgrest")
KNOWN_DOC_DRIVERS = ("notion",)
KNOWN_CHAT_DRIVERS = ("line", "telegram", "webhook")
KNOWN_CALENDAR_DRIVERS = ("google",)


@dataclass
class Check:
    """One named pass/fail observation."""

    name: str
    passed: bool
    detail: str = ""
    remedy: str = ""


@dataclass
class Report:
    """Accumulated checks for one doctor run."""

    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", remedy: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail, remedy=remedy))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def register(subparsers: argparse._SubParsersAction) -> None:
    """Attach the ``doctor`` command."""
    parser = subparsers.add_parser(
        "doctor",
        help="Check configuration, credentials, and drivers.",
        description="Run every cheap health check and report all failures at once.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also require credentials for drivers that are configured but not currently selected.",
    )
    parser.set_defaults(handler=handle)


def _check_secret(
    ctx: Context, report: Report, t: i18n.Translator, dotted: str, *, required: bool
) -> None:
    """Record whether the credential named by ``dotted`` is present."""
    env_name = ctx.config.get(dotted, None)
    if not env_name:
        return
    label = t("doctor.check.secret", env=env_name)
    present = bool(os.environ.get(str(env_name), ""))
    if present:
        report.add(label, passed=True)
    elif required:
        report.add(
            label,
            passed=False,
            detail="not set",
            remedy=f"Export {env_name}, or add it to the profile's .env.",
        )
    else:
        report.add(label, passed=True, detail="not set (optional)")


def _check_driver(
    ctx: Context, report: Report, t: i18n.Translator, dotted: str, known: tuple[str, ...], kind: str
) -> str:
    """Record whether the configured driver name is one this build implements."""
    driver = str(ctx.config.get(dotted, ""))
    label = t("doctor.check.driver", kind=kind, driver=driver or "-")
    if driver in known:
        report.add(label, passed=True)
    else:
        report.add(
            label,
            passed=False,
            detail=f"expected one of: {', '.join(known)}",
            remedy=f"Set `{dotted}` to a supported driver.",
        )
    return driver


def handle(ctx: Context) -> Exit:
    """Run the checks and report."""
    report = Report()

    # Configuration has to load before anything else can be checked; a failure
    # here raises ConfigError and is rendered by the CLI shell.
    config = ctx.config
    t = ctx.t

    report.add(t("doctor.check.config"), passed=True, detail=str(config.config_file))

    locale = config.locale
    report.add(
        t("doctor.check.locale"),
        passed=locale in i18n.available_locales(),
        detail=locale,
        remedy=f"Available locales: {', '.join(i18n.available_locales())}.",
    )

    try:
        zoneinfo.ZoneInfo(config.timezone)
        report.add(t("doctor.check.timezone"), passed=True, detail=config.timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        report.add(
            t("doctor.check.timezone"),
            passed=False,
            detail=config.timezone,
            remedy="Use an IANA timezone name such as Asia/Bangkok or Europe/London.",
        )

    probe = config.state_dir / ".doctor-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report.add(t("doctor.check.state_dir"), passed=True, detail=str(config.state_dir))
    except OSError as exc:
        report.add(
            t("doctor.check.state_dir"),
            passed=False,
            detail=f"{config.state_dir}: {exc}",
            remedy="Grant write access, or point BATON_STATE_DIR somewhere writable.",
        )

    db_driver = _check_driver(ctx, report, t, "db.driver", KNOWN_DB_DRIVERS, "database")
    doc_driver = _check_driver(ctx, report, t, "docs.driver", KNOWN_DOC_DRIVERS, "documents")
    chat_driver = _check_driver(ctx, report, t, "chat.driver", KNOWN_CHAT_DRIVERS, "chat")
    cal_driver = _check_driver(
        ctx, report, t, "calendar.driver", KNOWN_CALENDAR_DRIVERS, "calendar"
    )

    selected = {
        f"db.{db_driver}": ("url_env", "key_env", "jwt_env"),
        f"docs.{doc_driver}": ("token_env",),
        f"chat.{chat_driver}": ("token_env", "url_env"),
    }
    for section, keys in selected.items():
        for key in keys:
            _check_secret(ctx, report, t, f"{section}.{key}", required=True)

    if ctx.args.strict:
        for kind, drivers in (
            ("db", KNOWN_DB_DRIVERS),
            ("chat", KNOWN_CHAT_DRIVERS),
        ):
            for driver in drivers:
                for key in ("token_env", "url_env", "key_env", "jwt_env"):
                    _check_secret(ctx, report, t, f"{kind}.{driver}.{key}", required=True)

    total = len(report.checks)
    failed = report.failed

    payload = {
        "profile": str(config.profile_dir),
        "config_file": str(config.config_file),
        "locale": locale,
        "timezone": config.timezone,
        "drivers": {
            "db": db_driver,
            "docs": doc_driver,
            "chat": chat_driver,
            "calendar": cal_driver,
        },
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "detail": c.detail,
                **({"remedy": c.remedy} if c.remedy and not c.passed else {}),
            }
            for c in report.checks
        ],
        "passed": total - len(failed),
        "failed": len(failed),
    }

    lines = [
        t("doctor.title"),
        f"  {t('doctor.profile')}: {config.profile_dir}",
        f"  {t('doctor.locale')}: {locale}    {t('doctor.timezone')}: {config.timezone}",
        "",
    ]
    for check in report.checks:
        mark = "✓" if check.passed else "✗"
        suffix = f"  ({check.detail})" if check.detail else ""
        lines.append(f"  {mark} {check.name}{suffix}")
        if not check.passed and check.remedy:
            lines.append(f"      {check.remedy}")
    lines.append("")
    lines.append(
        t("doctor.pass") if not failed else t("doctor.fail", failed=len(failed), total=total)
    )

    ctx.report.result(payload, human="\n".join(lines), ok=not failed)

    if failed:
        # The full report is printed above rather than replaced by an error, so
        # the operator sees every problem in one pass. The exit code still says
        # "configuration is broken" — but quietly, since the detail is already
        # on screen.
        return Exit.CONFIG
    return Exit.OK
