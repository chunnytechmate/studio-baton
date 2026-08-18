"""Configuration layering, secret indirection, and the required-key contract."""

from __future__ import annotations

import os

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

    assert cfg.path("db.sqlite.path") == profile / "data" / "studio.db"


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


# -- the profile's .env ----------------------------------------------------
#
# The quickstart tells a new user to copy `.env.example` to `.env` and fill it
# in. These pin that this actually does something: for a long time it did not,
# and `doctor` answered "not set" to a credential the user had just written
# down, with a remedy telling them to write it down again.


def test_env_file_supplies_a_credential(profile):
    (profile / ".env").write_text("NOTION_API_TOKEN=from-the-file\n", encoding="utf-8")

    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env") == "from-the-file"


def test_real_environment_wins_over_the_env_file(profile, monkeypatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "from-the-shell")
    (profile / ".env").write_text("NOTION_API_TOKEN=from-the-file\n", encoding="utf-8")

    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env") == "from-the-shell"


def test_a_blank_variable_counts_as_unset(profile, monkeypatch):
    """`export TOKEN=` is a hole, not a decision — the file fills it."""
    monkeypatch.setenv("NOTION_API_TOKEN", "")
    (profile / ".env").write_text("NOTION_API_TOKEN=from-the-file\n", encoding="utf-8")

    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env") == "from-the-file"


def test_env_file_syntax_a_hand_written_file_will_contain(profile):
    (profile / ".env").write_text(
        "# a comment\n"
        "\n"
        "export NOTION_API_TOKEN=exported\n"
        "TELEGRAM_BOT_TOKEN='single quoted'\n"
        'LINE_CHANNEL_ACCESS_TOKEN="double quoted"\n'
        "  SUPABASE_PROJECT_URL = spaced out \n",
        encoding="utf-8",
    )

    config_module.load(profile)

    assert os.environ["NOTION_API_TOKEN"] == "exported"
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "single quoted"
    assert os.environ["LINE_CHANNEL_ACCESS_TOKEN"] == "double quoted"
    assert os.environ["SUPABASE_PROJECT_URL"] == "spaced out"


def test_a_hash_inside_a_value_is_part_of_the_value(profile):
    """No inline-comment stripping: that would truncate a credential."""
    (profile / ".env").write_text("NOTION_API_TOKEN=abc#def\n", encoding="utf-8")

    cfg = config_module.load(profile)

    assert cfg.secret("docs.notion.token_env") == "abc#def"


def test_env_file_can_carry_a_config_override(profile):
    """It is loaded before overrides are collected, so `BATON__…` counts."""
    (profile / ".env").write_text("BATON__TIMEZONE=UTC\n", encoding="utf-8")

    cfg = config_module.load(profile)

    assert cfg.get("timezone") == "UTC"


def test_a_malformed_env_line_says_which_line(profile):
    (profile / ".env").write_text(
        "NOTION_API_TOKEN=fine\nthis is not an assignment\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        config_module.load(profile)

    assert excinfo.value.details["line"] == 2


def test_no_env_file_is_not_an_error(profile):
    assert not (profile / ".env").exists()

    assert config_module.load(profile).get("timezone") == "Asia/Bangkok"
