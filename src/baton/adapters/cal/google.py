"""Google Calendar.

Behind the ``google`` extra, like the media drivers, and for the same reason:
someone using Baton without a calendar never installs the SDK, and turning it
on without it gets a sentence rather than an ImportError.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ...core.config import Config
from ...errors import BatonError, ConfigError, UpstreamError
from .base import CalendarEvent


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

    def __init__(self, config: Config, calendar_id: str = "primary") -> None:
        self.config = config
        self.calendar_id = calendar_id
        self._service: Any = None

    @classmethod
    def from_config(cls, config: Config) -> GoogleCalendar:
        return cls(config, calendar_id=str(config.get("calendar.google.calendar_id", "primary")))

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
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
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
        created = (
            self.service.events()
            .insert(
                calendarId=self.calendar_id,
                body={
                    "summary": event.title,
                    "description": event.description,
                    "start": {"dateTime": event.start},
                    "end": {"dateTime": event.end},
                },
            )
            .execute()
        )
        return self._event(created)

    def list_between(self, start: str, end: str) -> list[CalendarEvent]:
        response = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start,
                timeMax=end,
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            )
            .execute()
        )
        return [self._event(item) for item in response.get("items", [])]

    def delete(self, event_id: str) -> None:
        from googleapiclient.errors import HttpError

        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            # Already gone is the desired state, not a failure: a cancel run
            # twice must not report an error the second time.
            if getattr(exc, "status_code", None) in (404, 410):
                return
            raise UpstreamError(
                f"Google Calendar refused the delete: {exc}", service="google-calendar"
            ) from exc

    def health(self) -> None:
        """Prove the credentials still work.

        Google refreshes the access token lazily, on the first call, and a
        refresh token that has been revoked or has expired fails there with a
        `RefreshError` rather than anything Baton defines. Left alone it reaches
        `doctor` as a traceback — which is the one thing doctor must never
        print, since its whole job is to name every problem at once.
        """
        try:
            self.service.calendars().get(calendarId=self.calendar_id).execute()
        except BatonError:
            raise
        except Exception as exc:  # any vendor failure is a failed check, not a crash
            raise UpstreamError(
                f"Google Calendar refused the credentials: {exc}",
                service="google-calendar",
                remedy="Re-authorise the calendar and update the refresh token, "
                "then run `baton doctor` again.",
            ) from exc
