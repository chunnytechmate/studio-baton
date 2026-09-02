"""Google Calendar.

Behind the ``google`` extra, like the media drivers, and for the same reason:
someone using Baton without a calendar never installs the SDK, and turning it
on without it gets a sentence rather than an ImportError.

Every call goes through :func:`_calendar_call`, which does two things the raw
client does not. It retries rate limiting and the 5xx family with the same
backoff the rest of Baton uses: the pipeline this replaced had exactly that,
and dropping it made a booking fail on a transient 429 that one more attempt
would have carried. And it turns a vendor exception into an
:class:`~baton.errors.UpstreamError`, so a calendar fault reaches the operator
as a sentence and an exit code rather than a traceback.

That matters most on `create`. By the time it runs, the session document has
already been marked in progress, and the ordering in
:mod:`baton.pipelines.schedule` deliberately puts the document first so the two
records cannot disagree. A transient failure here is the one case that can
still split them, which is precisely the case worth retrying.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from ...core.config import Config
from ...core.retry import RETRYABLE_STATUS, retry
from ...errors import BatonError, ConfigError, UpstreamError
from .. import google_http
from .base import CalendarEvent

_T = TypeVar("_T")


class _Transient(Exception):
    """A calendar fault worth another attempt. Never escapes this module."""


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status a googleapiclient error carries, if it carries one.

    The attribute moved between client releases: newer ones expose
    ``status_code``, older ones only the underlying ``resp.status``, and
    pinning either one alone means the retry silently stops working after an
    upgrade.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _calendar_call(what: str, operation: Callable[[], _T], *, attempts: int = 3) -> _T:
    """Run one calendar request, retrying transient faults, mapping the rest.

    A fault with no HTTP status is only retried when it is an ``OSError``:
    a dropped connection or a timeout. Anything else without a status is a
    bug in this process, and retrying a bug three times only delays the
    traceback while hiding what caused it.
    """

    def attempt() -> _T:
        try:
            return operation()
        except BatonError:
            raise
        except Exception as exc:
            status = _http_status(exc)
            if status in RETRYABLE_STATUS or (status is None and isinstance(exc, OSError)):
                raise _Transient(str(exc)) from exc
            raise UpstreamError(
                f"Google Calendar refused the {what}: {exc}",
                service="google-calendar",
                status=status,
            ) from exc

    try:
        return retry(attempt, attempts=attempts, exceptions=(_Transient,))
    except _Transient as exhausted:
        raise UpstreamError(
            f"Google Calendar did not answer the {what} after {attempts} attempts: {exhausted}",
            service="google-calendar",
            attempts=attempts,
            remedy="The fault looked transient. Re-run once the service is back; "
            "nothing partial was written.",
        ) from exhausted.__cause__ or exhausted


def _require_google() -> Any:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ConfigError(
            "The Google client libraries are not installed.",
            remedy="Install them with `pip install 'studio-baton[google]'`.",
        ) from exc
    return build, Credentials


class GoogleCalendar:
    """A :class:`~baton.adapters.cal.base.CalendarStore` over Google Calendar."""

    driver = "google"

    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/calendar"]

    def __init__(
        self,
        config: Config,
        calendar_id: str = "primary",
        *,
        attempts: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self.config = config
        self.calendar_id = calendar_id
        self.attempts = max(1, attempts)
        # Deliberately far below the media default: booking is something a
        # person waits on, and a minute of silence from Calendar already means
        # the answer is not coming.
        self.timeout = timeout
        self._service: Any = None

    @classmethod
    def from_config(cls, config: Config) -> GoogleCalendar:
        return cls(
            config,
            calendar_id=str(config.get("calendar.google.calendar_id", "primary")),
            attempts=int(config.get("calendar.google.attempts", 3)),
            timeout=float(config.get("calendar.google.timeout_seconds", 60.0)),
        )

    @property
    def service(self) -> Any:
        if self._service is None:
            build, Credentials = _require_google()
            credentials = Credentials(
                token=None,
                refresh_token=str(self.config.secret("calendar.google.refresh_token_env")),
                client_id=str(self.config.secret("calendar.google.client_id_env")),
                client_secret=str(self.config.secret("calendar.google.client_secret_env")),
                token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 - public endpoint
                scopes=self.SCOPES,
            )
            self._service = build(
                "calendar",
                "v3",
                cache_discovery=False,
                **google_http.build_kwargs(credentials, self.timeout),
            )
        return self._service

    @staticmethod
    def _event(raw: dict[str, Any]) -> CalendarEvent:
        start = raw.get("start", {}) or {}
        end = raw.get("end", {}) or {}
        return CalendarEvent(
            id=str(raw.get("id", "")),
            title=str(raw.get("summary", "")),
            start=str(start.get("dateTime") or start.get("date") or ""),
            end=str(end.get("dateTime") or end.get("date") or ""),
            description=str(raw.get("description", "")),
        )

    def create(self, event: CalendarEvent) -> CalendarEvent:
        request = self.service.events().insert(
            calendarId=self.calendar_id,
            body={
                "summary": event.title,
                "description": event.description,
                "start": {"dateTime": event.start},
                "end": {"dateTime": event.end},
            },
        )
        created = _calendar_call("booking", request.execute, attempts=self.attempts)
        return self._event(created)

    def list_between(self, start: str, end: str) -> list[CalendarEvent]:
        request = self.service.events().list(
            calendarId=self.calendar_id,
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        response = _calendar_call("listing", request.execute, attempts=self.attempts)
        return [self._event(item) for item in response.get("items", [])]

    def delete(self, event_id: str) -> None:
        request = self.service.events().delete(calendarId=self.calendar_id, eventId=event_id)

        def once() -> None:
            try:
                request.execute()
            except Exception as exc:
                # Already gone is the desired state, not a failure: a cancel
                # run twice must not report an error the second time. Checked
                # before the retry wrapper sees it, because 404 and 410 are
                # not retryable and would otherwise become an UpstreamError.
                if _http_status(exc) in (404, 410):
                    return
                raise

        _calendar_call("delete", once, attempts=self.attempts)

    def health(self) -> None:
        """Prove the credentials still work.

        Google refreshes the access token lazily, on the first call, and a
        refresh token that has been revoked or has expired fails there with a
        `RefreshError` rather than anything Baton defines. Left alone it reaches
        `doctor` as a traceback, which is the one thing doctor must never
        print, since its whole job is to name every problem at once.
        """
        try:
            request = self.service.calendars().get(calendarId=self.calendar_id)
            _calendar_call("credential check", request.execute, attempts=self.attempts)
        except BatonError:
            raise
        except Exception as exc:  # any vendor failure is a failed check, not a crash
            raise UpstreamError(
                f"Google Calendar refused the credentials: {exc}",
                service="google-calendar",
                remedy="Re-authorise the calendar and update the refresh token, "
                "then run `baton doctor` again.",
            ) from exc
