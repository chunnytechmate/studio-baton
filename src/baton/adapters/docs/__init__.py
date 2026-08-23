"""Session-document drivers, selected by one line of configuration."""

from __future__ import annotations

from ...core.config import Config
from ...errors import ConfigError
from .base import Block, DocStatus, DocStore, PreservePolicy, PreserveRule, find_video_link
from .notion import NotionDocStore

#: Every driver this build implements.
DRIVERS = ("notion",)


def open_docs(config: Config) -> DocStore:
    """Build the configured document store.

    Raises:
        ConfigError: The driver is unknown or its credentials are missing.
    """
    driver = str(config.get("docs.driver", "notion"))
    if driver == "notion":
        return NotionDocStore.from_config(config)
    raise ConfigError(
        f"Unknown document driver `{driver}`.",
        remedy=f"Set docs.driver to one of: {', '.join(DRIVERS)}.",
        details={"driver": driver, "supported": list(DRIVERS)},
    )


__all__ = [
    "DRIVERS",
    "Block",
    "DocStatus",
    "DocStore",
    "NotionDocStore",
    "PreservePolicy",
    "PreserveRule",
    "find_video_link",
    "open_docs",
]
