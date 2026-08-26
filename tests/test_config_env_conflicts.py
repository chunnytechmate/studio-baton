"""Env overrides must not have an order-dependent winner (M5).

`BATON__DOCS=x` and `BATON__DOCS__DRIVER=y` both target the `docs` setting —
one as a scalar, one as a key inside it. Which one won used to depend on
``os.environ``'s iteration order, and the loser was silently converted on the
way (a scalar became a dict, or a section was replaced by a scalar). Both
combinations are now an error that names the two variables.
"""

from __future__ import annotations

import pytest

from baton.core import config as config_module
from baton.errors import ConfigError


def test_scalar_and_nested_is_an_error_scalar_first(monkeypatch):
    monkeypatch.setenv("BATON__DOCS", "markdown")
    monkeypatch.setenv("BATON__DOCS__DRIVER", "notion")

    with pytest.raises(ConfigError) as caught:
        config_module._env_overrides()

    message = str(caught.value)
    assert "BATON__DOCS" in message
    assert "BATON__DOCS__DRIVER" in message


def test_scalar_and_nested_is_an_error_nested_first(monkeypatch):
    """The same collision in the opposite insertion order — the outcome must
    not depend on which variable the environment happens to list first."""
    monkeypatch.setenv("BATON__DOCS__DRIVER", "notion")
    monkeypatch.setenv("BATON__DOCS", "markdown")

    with pytest.raises(ConfigError):
        config_module._env_overrides()


def test_case_variant_of_the_same_setting_is_an_error(monkeypatch):
    """`BATON__DOCS__DRIVER` and `BATON__docs__driver` fold onto one setting;
    the winner must not be decided by environment order either."""
    monkeypatch.setenv("BATON__DOCS__DRIVER", "notion")
    monkeypatch.setenv("BATON__docs__driver", "markdown")

    with pytest.raises(ConfigError):
        config_module._env_overrides()


def test_distinct_settings_still_collect(monkeypatch):
    """Only collisions error — unrelated scalar and nested overrides keep
    collecting exactly as before."""
    monkeypatch.setenv("BATON__TIMEZONE", "UTC")
    monkeypatch.setenv("BATON__DOCS__PROPERTIES__STATUS", "State")

    assert config_module._env_overrides() == {
        "timezone": "UTC",
        "docs": {"properties": {"status": "State"}},
    }
