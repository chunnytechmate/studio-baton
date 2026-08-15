"""Configuration layering, secret indirection, and the required-key contract."""

from __future__ import annotations

import pytest

from baton.core import config as config_module
from baton.errors import ConfigError


def test_profile_overrides_packaged_defaults(profile):
    cfg = config_module.load(profile)

    # From the profile:
    assert cfg.get("timezone") == "Asia/Bangkok"
    # From the packaged defaults, untouched by the profile:
    assert cfg.get("docs.notion.api_version") == "2022-06-28"


def test_nested_merge_keeps_sibling_defaults(profile):
    cfg = config_module.load(profile)

    # The profile sets only docs.properties.status; the siblings survive.
    assert cfg.get("docs.properties.status") == "Status"
    assert cfg.get("docs.properties.titles") == "Titles"


def test_environment_override_wins_over_profile(profile, monkeypatch):
    monkeypatch.setenv("BATON__TIMEZONE", "UTC")
    monkeypatch.setenv("BATON__DOCS__PROPERTIES__STATUS", "State")

    cfg = config_module.load(profile)

    assert cfg.get("timezone") == "UTC"
    assert cfg.get("docs.properties.status") == "State"


def test_environment_override_coerces_scalars(profile, monkeypatch):
    monkeypatch.setenv("BATON__CALENDAR__REQUIRE_DOC_UPDATE", "false")

    cfg = config_module.load(profile)

    assert cfg.get("calendar.require_doc_update") is False


def test_missing_required_key_is_a_config_error(profile):
    cfg = config_module.load(profile)

    with pytest.raises(ConfigError) as excinfo:
        cfg.get("docs.properties.nonexistent")

    assert "nonexistent" in str(excinfo.value)


def test_missing_key_with_default_is_returned(profile):
    cfg = config_module.load(profile)

    assert cfg.get("docs.properties.nonexistent", "fallback") == "fallback"


def test_secret_reads_the_named_environment_variable(profile, monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "secret-value")
    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env") == "secret-value"


def test_missing_secret_names_the_variable_not_the_value(profile):
    cfg = config_module.load(profile)

    with pytest.raises(ConfigError) as excinfo:
        cfg.secret("docs.notion.token_env")

    message = str(excinfo.value)
    assert "NOTION_API_TOKEN" in message
    assert excinfo.value.details["env"] == "NOTION_API_TOKEN"


def test_optional_secret_returns_none_when_unset(profile):
    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env", required=False) is None


def test_relative_paths_resolve_inside_the_profile(profile):
    cfg = config_module.load(profile)

    assert cfg.path("db.sqlite.path") == profile / "data" / "baton.db"


def test_absolute_paths_are_left_alone(profile, tmp_path):
    (profile / "baton.yaml").write_text(
        f"version: 1\ndb:\n  sqlite:\n    path: {tmp_path / 'elsewhere.db'}\n",
        encoding="utf-8",
    )
    cfg = config_module.load(profile)

    assert cfg.path("db.sqlite.path") == tmp_path / "elsewhere.db"


def test_unknown_version_is_rejected(profile):
    (profile / "baton.yaml").write_text("version: 99\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        config_module.load(profile)

    assert "version" in str(excinfo.value).lower()


def test_malformed_yaml_reports_the_file(profile):
    (profile / "baton.yaml").write_text("version: 1\ndocs: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        config_module.load(profile)

    assert "baton.yaml" in str(excinfo.value)


def test_missing_profile_lists_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        config_module.load()

    assert excinfo.value.details["searched"]


def test_labels_fall_back_to_the_key(profile):
    cfg = config_module.load(profile)

    assert cfg.label("learner") == "student"
    assert cfg.label("unmapped") == "unmapped"


def test_example_profile_is_valid():
    """The shipped example must load, or the quickstart is a lie."""
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "profiles" / "example"
    cfg = config_module.load(example)

    assert cfg.get("chat.driver") == "telegram"
    assert cfg.get("docs.properties.titles") == "Repertoire"
