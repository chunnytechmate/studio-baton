"""Calendar drivers, selected by configuration."""

from __future__ import annotations

from ...core.config import Config
from ...errors import ConfigError
from .base import CalendarEvent, CalendarStore

#: Drivers this build implements.
DRIVERS = ("google",)


def open_calendar(config: Config) -> CalendarStore:
    """Build the configured calendar."""
    driver = str(config.get("calendar.driver", "google"))
    if driver == "google":
        from .google import GoogleCalendar

        return GoogleCalendar.from_config(config)
    raise ConfigError(
        f"Unknown calendar driver `{driver}`.",
        remedy=f"Set calendar.driver to one of: {', '.join(DRIVERS)}.",
        details={"driver": driver, "supported": list(DRIVERS)},
    )


__all__ = ["DRIVERS", "CalendarEvent", "CalendarStore", "open_calendar"]
