"""The SQLite driver, run against a real database built from the shipped
migration — so a migration that drifts from `defaults.yaml` fails here."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.db import open_store
from baton.adapters.db.sqlite import SqliteStore
from baton.core import config as config_module
from baton.domain.models import Work
from baton.errors import ConfigError

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"


@pytest.fixture
def store(profile):
    """A profile whose SQLite database is built from the real migration+seed."""
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    opened = open_store(config_module.load(profile))
    yield opened
    opened.close()


def test_the_shipped_migration_satisfies_the_default_mapping(store):
    """If defaults.yaml names a column the migration does not create, this
    fails — which is the only thing keeping the two files in step."""
    store.health()


def test_lists_learners_by_name(store):
    names = [learner.name for learner in store.list_learners()]

    assert names == ["Ada Whitfield", "Bruno Castell", "Clara Nguyen", "Devon Marsh"]


def test_reads_typed_fields(store):
    ada = next(person for person in store.list_learners() if person.name == "Ada Whitfield")

    assert ada.instrument == "guitar"
    assert ada.tone == "standard"
    assert ada.has_instrument is True
    assert ada.current_piece_id == "2"


def test_sqlite_integer_booleans_become_real_booleans(store):
    bruno = next(person for person in store.list_learners() if person.name == "Bruno Castell")

    # Stored as 0; must not surface as the string "0", which is truthy.
    assert bruno.has_instrument is False


def test_unknown_learner_returns_none_rather_than_raising(store):
    assert store.get_learner("9999") is None


def test_sessions_come_back_in_order(store):
    ada = store.get_learner("1")
    numbers = [session.number for session in store.list_sessions(ada.id)]

    assert numbers == [1, 2, 3]


def test_session_lookup_by_number(store):
    session = store.get_session("1", 2)

    assert session is not None
    assert session.doc_id == "doc-ada-02"
    assert store.get_session("1", 99) is None


def test_pieces_and_optional_links(store):
    pieces = {piece.title: piece for piece in store.list_pieces()}

    assert pieces["Blackbird"].practice_track == ""
    assert pieces["Autumn Leaves"].practice_track.endswith("autumn.mp3")


def test_works_are_newest_first(store):
    titles = [work.title for work in store.list_works("1")]

    assert titles == ["Blackbird", "Autumn Leaves"]


def test_add_work_returns_the_stored_row_with_its_id(store):
    created = store.add_work(
        Work(
            id="",
            learner_id="2",
            title="Take Five",
            type="cover",
            video_link="https://example.invalid/watch/bruno",
            performed_date="2026-08-01",
        )
    )

    assert created.id
    assert created.title == "Take Five"
    assert [w.title for w in store.list_works("2")] == ["Take Five"]


def test_set_current_piece_round_trips_including_clearing(store):
    store.set_current_piece("4", "1")
    assert store.get_learner("4").current_piece_id == "1"

    store.set_current_piece("4", None)
    assert store.get_learner("4").current_piece_id is None


def test_raw_keeps_columns_baton_does_not_model(store):
    ada = store.get_learner("1")

    # created_at is in the schema but not in the domain model; losing it would
    # make Baton a lossy layer over the studio's own data.
    assert "created_at" in ada.raw


# -- misconfiguration -------------------------------------------------------


def test_a_renamed_column_is_reported_as_config_not_a_crash(profile):
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
              fields:
                learner:
                  name: full_name
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    opened = open_store(config_module.load(profile))
    try:
        with pytest.raises(ConfigError) as excinfo:
            opened.list_learners()
        # The error names the column that is missing, not just "schema error" —
        # that difference is the whole point of checking the table definition
        # instead of waiting for the query to fail.
        assert excinfo.value.details["missing"] == ["full_name"]
        assert "full_name" in str(excinfo.value)
        assert "migrations/sqlite.sql" in (excinfo.value.remedy or "")
    finally:
        opened.close()


@pytest.mark.parametrize(
    "bad_name",
    ["learners; DROP TABLE learners", "learners-1", "", "2learners", "learners WHERE 1=1"],
)
def test_illegal_identifiers_are_refused_before_reaching_sql(profile, bad_name):
    """Table and column names are interpolated (SQLite cannot bind them), so
    they are validated as plain identifiers first."""
    (profile / "baton.yaml").write_text(
        f"version: 1\ndb:\n  driver: sqlite\n  tables:\n    learners: {bad_name!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as excinfo:
        open_store(config_module.load(profile))

    assert "db.tables.learners" in str(excinfo.value.details.get("setting", ""))


def test_missing_database_file_names_the_migration(profile):
    (profile / "baton.yaml").write_text(
        "version: 1\ndb:\n  driver: sqlite\n  sqlite:\n    path: data/absent.db\n",
        encoding="utf-8",
    )
    config = config_module.load(profile)
    store = SqliteStore.from_config(config)
    try:
        # Connecting creates an empty file, so health fails on the missing
        # tables rather than the missing file — either way it names the fix.
        with pytest.raises(ConfigError) as excinfo:
            store.health()
        assert "migrations/sqlite.sql" in (excinfo.value.remedy or "")
    finally:
        store.close()


def test_empty_table_still_validates_its_columns(profile):
    """A schema check that only works once there is data is no check at all."""
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.close()

    (profile / "baton.yaml").write_text(
        "version: 1\ndb:\n  driver: sqlite\n  sqlite:\n    path: data/studio.db\n",
        encoding="utf-8",
    )
    opened = open_store(config_module.load(profile))
    try:
        opened.health()
        assert opened.list_learners() == []
    finally:
        opened.close()
