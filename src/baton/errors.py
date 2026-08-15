"""Typed errors that map one-to-one onto the exit code contract.

Pipelines raise these; the CLI shell catches them, renders them (human or
JSON), and exits with ``err.exit_code``. No pipeline calls ``sys.exit`` and no
pipeline prints its own failure — that keeps the exit contract enforceable in
one place instead of scattered across every command.
"""

from __future__ import annotations

from typing import Any

from .exits import SLUG, Exit


class BatonError(Exception):
    """Base class for every failure Baton reports deliberately.

    Args:
        message: One sentence, addressed to the operator, saying what went
            wrong. No apologies, no stack-trace jargon.
        remedy: One sentence saying what to do about it. Optional only when the
            remedy is genuinely obvious from the message.
        details: Structured payload emitted under ``"details"`` in JSON mode.
    """

    exit_code: Exit = Exit.USAGE

    def __init__(
        self,
        message: str,
        *,
        remedy: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Render as the JSON error envelope shared by every command."""
        payload: dict[str, Any] = {
            "ok": False,
            "error": SLUG[self.exit_code],
            "exit_code": int(self.exit_code),
            "message": self.message,
        }
        if self.remedy:
            payload["remedy"] = self.remedy
        if self.details:
            payload["details"] = self.details
        return payload


class UsageError(BatonError):
    """The command was invoked wrongly."""

    exit_code = Exit.USAGE


class ConfigError(BatonError):
    """Configuration or environment is missing/invalid. Nothing was attempted."""

    exit_code = Exit.CONFIG


class NeedsHumanError(BatonError):
    """Resolution is ambiguous; a person must choose.

    Always carries ``candidates`` so the caller can present real options rather
    than inventing one.
    """

    exit_code = Exit.NEEDS_HUMAN

    def __init__(
        self,
        message: str,
        *,
        candidates: list[dict[str, Any]] | None = None,
        remedy: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        merged["candidates"] = candidates or []
        super().__init__(message, remedy=remedy, details=merged)
        self.candidates = merged["candidates"]


class ContractError(BatonError):
    """Model-authored content failed schema validation. Nothing was written."""

    exit_code = Exit.CONTRACT

    def __init__(
        self,
        message: str,
        *,
        violations: list[dict[str, Any]] | None = None,
        remedy: str | None = None,
    ) -> None:
        super().__init__(
            message,
            remedy=remedy or "Fix the reported fields and submit the JSON again.",
            details={"violations": violations or []},
        )
        self.violations = violations or []


class GateError(BatonError):
    """A fail-closed gate refused the operation. There is no override."""

    exit_code = Exit.GATE

    def __init__(
        self,
        message: str,
        *,
        missing: list[dict[str, Any]] | None = None,
        remedy: str | None = None,
    ) -> None:
        super().__init__(
            message,
            remedy=remedy or "Supply the missing data, then re-run. Do not bypass this check.",
            details={"missing": missing or []},
        )
        self.missing = missing or []


class UpstreamError(BatonError):
    """A remote service failed after retries were exhausted."""

    exit_code = Exit.UPSTREAM

    def __init__(
        self,
        message: str,
        *,
        service: str | None = None,
        status: int | None = None,
        attempts: int | None = None,
        remedy: str | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if service:
            details["service"] = service
        if status is not None:
            details["status"] = status
        if attempts is not None:
            details["attempts"] = attempts
        super().__init__(message, remedy=remedy, details=details)


class StateError(BatonError):
    """Local job state contradicts reality and needs an audit before resuming."""

    exit_code = Exit.STATE


class BusyError(BatonError):
    """Another run already owns the lock this one needs.

    Carries the holder's job id (or pid) so the operator can wait on it or stop
    it. Starting a second run anyway is exactly the collision this exists to
    prevent: two encoders writing one temp file, two uploads of one video.
    """

    exit_code = Exit.RUNNING
