"""`baton init`: the first two minutes.

Whether someone tries this at all is decided here, so the tests check that what
it produces actually runs: the config loads, the database satisfies the mapping,
and `doctor --offline` passes against it without further editing.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from baton.cli.app import run
from baton.core import config as config_module
from baton.exits import Exit


def init(tmp_path, *args):
    return run(["--json", "init", str(tmp_path / "studio"), "--yes", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_a_fresh_profile_is_created(tmp_path, capsys):
    assert init(tmp_path) == Exit.OK

    payload = out(capsys)
    assert (tmp_path / "studio" / "baton.yaml").is_file()
    assert (tmp_path / "studio" / ".env.example").is_file()
    assert payload["answers"]["db"] == "sqlite"


def test_what_it_writes_actually_loads(tmp_path, capsys):
    """A scaffolder that produces a config the tool rejects is worse than none."""
    init(tmp_path)
    capsys.readouterr()

    config = config_module.load(tmp_path / "studio")

    assert config.get("version") == 1
    assert config.get("db.driver") == "sqlite"


def test_the_database_it_creates_satisfies_the_configured_mapping(tmp_path, capsys):
    """The whole point: `baton doctor` passes straight after `init`."""
    init(tmp_path, "--chat", "webhook")
    capsys.readouterr()

    from baton.adapters.db import open_store

    store = open_store(config_module.load(tmp_path / "studio"))
    try:
        store.health()
        assert store.list_learners() == []
    finally:
        store.close()


def test_sample_data_gives_something_to_look_at(tmp_path, capsys):
    assert init(tmp_path, "--sample-data") == Exit.OK

    assert out(capsys)["learners"] == 4


def test_answers_become_the_profile(tmp_path, capsys):
    init(
        tmp_path,
        "--locale",
        "th",
        "--timezone",
        "Asia/Bangkok",
        "--learner-label",
        "นักเรียน",
        "--session-label",
        "สัปดาห์",
        "--chat",
        "line",
    )
    capsys.readouterr()

    config = config_module.load(tmp_path / "studio")

    assert config.locale == "th"
    assert config.timezone == "Asia/Bangkok"
    assert config.label("learner") == "นักเรียน"
    assert config.get("chat.driver") == "line"


def test_the_env_template_lists_exactly_this_profiles_variables(tmp_path, capsys):
    init(tmp_path, "--chat", "telegram", "--db", "supabase")
    capsys.readouterr()

    env = (tmp_path / "studio" / ".env.example").read_text(encoding="utf-8")

    assert "TELEGRAM_BOT_TOKEN=" in env
    assert "SUPABASE_PROJECT_URL=" in env
    assert "NOTION_API_TOKEN=" in env
    # Not this profile's driver, so not in its .env: a template full of
    # variables you do not need is how people stop reading it.
    assert "LINE_CHANNEL_ACCESS_TOKEN" not in env


def test_a_non_sqlite_profile_says_to_run_the_migration_itself(tmp_path, capsys):
    init(tmp_path, "--db", "postgrest")

    payload = out(capsys)
    assert "database" not in payload
    assert any("postgres.sql" in step for step in payload["next_steps"])


def test_an_existing_profile_is_not_overwritten(tmp_path, capsys):
    init(tmp_path)
    capsys.readouterr()
    (tmp_path / "studio" / "baton.yaml").write_text("version: 1\n# mine\n", encoding="utf-8")

    assert init(tmp_path) == Exit.USAGE
    assert "--force" in out(capsys)["remedy"]
    assert "# mine" in (tmp_path / "studio" / "baton.yaml").read_text(encoding="utf-8")


def test_force_overwrites(tmp_path, capsys):
    init(tmp_path)
    capsys.readouterr()

    assert init(tmp_path, "--force") == Exit.OK


def test_an_unknown_timezone_is_refused_before_anything_is_written(tmp_path, capsys):
    assert init(tmp_path, "--timezone", "Mars/Olympus") == Exit.USAGE

    assert not (tmp_path / "studio" / "baton.yaml").exists()


def test_doctor_passes_against_a_fresh_profile(tmp_path, capsys, monkeypatch):
    """The claim the quickstart makes, asserted."""
    init(tmp_path, "--chat", "webhook")
    capsys.readouterr()
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(tmp_path / "studio"), "--json", "doctor", "--offline"])

    assert code == Exit.OK
    assert out(capsys)["failed"] == 0


# -- schema ------------------------------------------------------------------


@pytest.mark.parametrize("driver", ["sqlite", "postgres", "sample-data"])
def test_schema_prints_something_runnable(capsys, driver):
    assert run(["--json", "schema", driver]) == Exit.OK

    sql = out(capsys)["sql"]
    assert "CREATE TABLE" in sql or "INSERT INTO" in sql


def test_the_printed_sqlite_schema_is_the_one_that_actually_runs(capsys, tmp_path):
    """Printed for people with no checkout, so it has to be executable."""
    run(["--json", "schema", "sqlite"])
    sql = out(capsys)["sql"]

    connection = sqlite3.connect(tmp_path / "printed.db")
    try:
        connection.executescript(sql)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()

    assert {"learners", "sessions", "pieces", "works"} <= tables


# -- labels ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("singular", "expected"),
    [
        ("student", "students"),
        ("week", "weeks"),
        ("class", "classes"),
        ("box", "boxes"),
    ],
)
def test_english_labels_are_pluralised(singular, expected):
    from baton.cli.cmd_init import pluralise

    assert pluralise(singular) == expected


@pytest.mark.parametrize("label", ["นักเรียน", "สัปดาห์", "生徒", "Schüler"])
def test_a_non_english_label_is_left_alone(label):
    """Appending "s" to Thai produces broken text on every page it reaches.
    Guessing is limited to where guessing is safe."""
    from baton.cli.cmd_init import pluralise

    assert pluralise(label) == label


def test_a_thai_profile_does_not_get_an_english_plural(tmp_path, capsys):
    init(tmp_path, "--learner-label", "นักเรียน", "--session-label", "สัปดาห์")
    capsys.readouterr()

    config = config_module.load(tmp_path / "studio")

    assert config.label("learners") == "นักเรียน"
    assert config.label("sessions") == "สัปดาห์"


def test_an_explicit_plural_wins(tmp_path, capsys):
    init(tmp_path, "--learner-label", "child", "--learner-plural", "children")
    capsys.readouterr()

    assert config_module.load(tmp_path / "studio").label("learners") == "children"
