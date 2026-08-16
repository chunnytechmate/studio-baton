"""Chat drivers, selected by one line of configuration."""

from __future__ import annotations

from ...core.config import Config
from ...errors import ConfigError
from .base import Messenger, SendOutcome, resolve_contact
from .drivers import LineMessenger, TelegramMessenger, WebhookMessenger

#: Every driver this build implements.
DRIVERS = ("line", "telegram", "webhook")


def open_chat(config: Config) -> Messenger:
    """Build the configured messenger.

    Raises:
        ConfigError: The driver is unknown or its credentials are missing.
    """
    driver = str(config.get("chat.driver", "line"))
    if driver == "line":
        return LineMessenger.from_config(config)
    if driver == "telegram":
        return TelegramMessenger.from_config(config)
    if driver == "webhook":
        return WebhookMessenger.from_config(config)
    raise ConfigError(
        f"Unknown chat driver `{driver}`.",
        remedy=f"Set chat.driver to one of: {', '.join(DRIVERS)}.",
        details={"driver": driver, "supported": list(DRIVERS)},
    )


__all__ = [
    "DRIVERS",
    "LineMessenger",
    "Messenger",
    "SendOutcome",
    "TelegramMessenger",
    "WebhookMessenger",
    "open_chat",
    "resolve_contact",
]
