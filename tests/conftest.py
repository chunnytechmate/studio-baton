"""Shared fixtures.

Every test runs against a throwaway profile. Nothing in the suite reads the
developer's real profile or environment — a test that silently picks up
``$BATON_PROFILE`` would pass on one machine and fail on another.
"""

from __future__ import annotations

import os
import textwrap

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Strip Baton's environment variables and pin HOME to a temp directory."""
    for name in [n for n in os.environ if n.startswith("BATON")]:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "NOTION_API_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "SUPABASE_PROJECT_URL",
        "SUPABASE_PROJECT_API",
    ):
        monkeypatch.delenv(name, raising=False)

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))


@pytest.fixture
def profile(tmp_path):
    """A minimal, valid profile directory."""
    directory = tmp_path / "profile"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            locale: en
            timezone: Asia/Bangkok
            db:
              driver: sqlite
            docs:
              driver: notion
              properties:
                status: Status
            chat:
              driver: webhook
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return directory
