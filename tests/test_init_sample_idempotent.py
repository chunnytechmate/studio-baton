"""`init --force --sample-data` run twice must not leak a raw traceback.

Regression (M13): `seed_example.sql` inserted the sample rows with plain
`INSERT INTO` and explicit ids for `pieces`/`learners`. A second
`init --force --sample-data` against the same directory re-ran the same
script against the schema `--force` had left in place, colliding on those
ids: a raw ``sqlite3.IntegrityError`` escaped past the JSON error envelope
instead of a clean result.

The fix appends ``ON CONFLICT DO NOTHING`` to every insert in the seed
script, so a repeat run is a no-op rather than a collision, and
deliberately leaves ``--force``'s own semantics untouched, so a bare
`init --force` (no `--sample-data`) still only re-runs the idempotent
schema DDL and never touches, let alone deletes, an existing profile's
real data.
"""

from __future__ import annotations

import json

from baton.cli.app import run
from baton.exits import Exit


def init(tmp_path, *args):
    return run(["--json", "init", str(tmp_path / "studio"), "--yes", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_force_sample_data_survives_two_runs(tmp_path, capsys):
    assert init(tmp_path, "--sample-data") == Exit.OK
    first = out(capsys)
    assert first["learners"] == 4

    code = init(tmp_path, "--force", "--sample-data")

    # A leaked sqlite3.IntegrityError is not a Baton exit code at all; this
    # is the assertion that would fail loudest if the raw traceback escaped.
    assert code == Exit.OK
    second = out(capsys)
    assert second["learners"] == 4


def test_bare_force_never_touches_existing_rows(tmp_path, capsys):
    """A plain `--force` re-init (no --sample-data) must not lose real data.

    This is the regression the first version of this fix introduced and a
    fresh verifier caught: deleting the sqlite file on any `--force` wiped
    whatever a learner already had, whether or not `--sample-data` was ever
    passed. The seed-idempotency fix touches nothing about `--force`, so it
    cannot reintroduce that risk: asserted here directly.
    """
    init(tmp_path, "--sample-data")
    capsys.readouterr()

    import sqlite3

    db_path = tmp_path / "studio" / "data" / "studio.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO learners (name, instrument) VALUES (?, ?)", ("Real Kid", "cello")
        )
        connection.commit()
    finally:
        connection.close()

    assert init(tmp_path, "--force") == Exit.OK
    capsys.readouterr()

    connection = sqlite3.connect(db_path)
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM learners")}
    finally:
        connection.close()
    assert "Real Kid" in names
