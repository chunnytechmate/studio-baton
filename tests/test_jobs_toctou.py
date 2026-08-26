"""``get()`` must survive the heartbeat file vanishing mid-read.

Regression (M3): ``JobRunner.get`` checked ``hb_path.exists()`` and then called
``hb_path.stat()`` — two separate filesystem calls. A prune pass running
concurrently can remove the job dir between them, so ``stat()`` raised a raw
``FileNotFoundError`` that escaped the error envelope callers are promised.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from baton.core.jobs import JobRunner

JOB_ID = "20260826-011204-000-toct"


def _running_job(state_dir: Path) -> Path:
    """A job dir whose meta says 'running' under a supervisor that is alive."""
    job_dir = state_dir / "jobs" / JOB_ID
    job_dir.mkdir(parents=True)
    (job_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": JOB_ID,
                "name": "racy",
                "command": ["true"],
                "status": "running",
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    return job_dir


def test_get_tolerates_heartbeat_vanishing_between_exists_and_stat(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    job_dir = _running_job(state_dir)
    runner = JobRunner(state_dir=state_dir, config_file=tmp_path / "baton.yaml")
    heartbeat = job_dir / "heartbeat"

    real_exists = Path.exists
    real_stat = Path.stat

    def exists_that_won_its_half_of_the_race(self, *args, **kwargs):
        # The exists() check inside get() still sees the heartbeat file...
        if self == heartbeat:
            return True
        return real_exists(self, *args, **kwargs)

    def stat_that_lost_the_race(self, *args, **kwargs):
        # ...but prune removes the job dir before the very next stat().
        if self == heartbeat:
            raise FileNotFoundError(2, "No such file or directory")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists_that_won_its_half_of_the_race)
    monkeypatch.setattr(Path, "stat", stat_that_lost_the_race)

    info = runner.get(JOB_ID)

    # A vanished heartbeat is "no heartbeat", not a crash: get() answers per
    # its contract instead of letting a raw FileNotFoundError escape.
    assert info is not None
    assert info.heartbeat_age is None
    assert info.status == "running"
