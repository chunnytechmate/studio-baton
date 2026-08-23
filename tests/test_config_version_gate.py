"""The config version gate must reject lookalike values, not just wrong numbers.

`True == 1` and `1.0 == 1` in Python, so a plain ``version != 1`` comparison lets
`version: true` and `version: 1.0` through the gate silently. These tests pin the
type-strict behaviour.
"""

from __future__ import annotations

import textwrap

import pytest

from baton.core import config as config_module
from baton.errors import ConfigError

# The same minimal valid profile the `profile` fixture writes, with only the
# version line swapped, so any ConfigError below is the version gate and not a
# missing-required-key complaint.
VALID_PROFILE = textwrap.dedent(
    """
    version: {version}
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
).strip() + "\n"


def _write_version(profile, version: str) -> None:
    (profile / "baton.yaml").write_text(
        VALID_PROFILE.format(version=version), encoding="utf-8"
    )


def test_boolean_version_is_rejected(profile):
    _write_version(profile, "true")

    with pytest.raises(ConfigError) as excinfo:
        config_module.load(profile)

    assert "version" in str(excinfo.value).lower()


def test_float_version_is_rejected(profile):
    _write_version(profile, "1.0")

    with pytest.raises(ConfigError) as excinfo:
        config_module.load(profile)

    assert "version" in str(excinfo.value).lower()


def test_integer_version_one_still_loads(profile):
    _write_version(profile, "1")

    cfg = config_module.load(profile)

    # The merge pipeline is untouched: profile value, packaged default, and the
    # version itself all read back exactly as before.
    assert cfg.get("version") == 1
    assert type(cfg.get("version")) is int
    assert cfg.get("timezone") == "Asia/Bangkok"
    assert cfg.get("docs.notion.api_version") == "2022-06-28"
