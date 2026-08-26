"""``supervise`` must refuse an id nobody spawned, not mint a job dir for it.

Regression (M26): ``_write_meta`` -> ``write_json`` unconditionally
``mkdir(parents=True)``s the job directory, so any string passed to
``job supervise --id`` became a real job dir before its validity was ever
checked. A typo'd id silently accumulated garbage in state instead of
failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baton.core.jobs import JobRunner
from baton.errors import UsageError

BOGUS_ID = "does-not-exist"


def test_supervise_rejects_an_id_with_no_job_dir(tmp_path):
    state_dir = tmp_path / "state"
    runner = JobRunner(state_dir=state_dir, config_file=tmp_path / "baton.yaml")

    with pytest.raises(UsageError, match=BOGUS_ID):
        runner.supervise(BOGUS_ID, ["true"])

    assert not (state_dir / "jobs" / BOGUS_ID).exists()
    assert not Path(state_dir / "jobs" / BOGUS_ID / "meta.json").exists()


def test_spawn_then_supervise_flow_still_works(tmp_path, monkeypatch):
    """The real flow: spawn() pre-creates the dir, so supervise() proceeds."""
    state_dir = tmp_path / "state"
    runner = JobRunner(state_dir=state_dir, config_file=tmp_path / "baton.yaml")

    class _FakeChild:
        pid = 4242

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int | None:
            return 0

        def terminate(self) -> None:  # pragma: no cover - not exercised here
            pass

        def kill(self) -> None:  # pragma: no cover - not exercised here
            pass

    monkeypatch.setattr("baton.core.jobs.subprocess.Popen", lambda *a, **k: _FakeChild())
    monkeypatch.setattr("baton.core.jobs.signal.signal", lambda *a, **k: None)

    info = runner.spawn(["true"], name="probe")
    exit_code = runner.supervise(info.id, ["true"])

    assert exit_code == 0
    assert (state_dir / "jobs" / info.id / "meta.json").exists()
