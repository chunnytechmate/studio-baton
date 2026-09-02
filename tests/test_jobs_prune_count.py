"""Prune must count only directories it actually removed.

Regression: the unlink sweep inside ``prune`` cannot delete subdirectories, so
a job dir holding one survives ``rmdir``, but the count still incremented, and
the operator's "Removed N job(s)" reported work that did not happen.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from baton.cli.app import run
from baton.exits import Exit


def _aged_finished_job(profile: Path, capsys, label: str) -> str:
    """Spawn a quick job through the CLI, settle it, and age it past the cutoff."""
    run(
        [
            "--profile",
            str(profile),
            "--json",
            "job",
            "spawn",
            "--name",
            label,
            "--",
            sys.executable,
            "-c",
            f"print({label!r})",
        ]
    )
    job_id = json.loads(capsys.readouterr().out)["id"]
    run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])
    capsys.readouterr()

    # Age the finished job past the cutoff by rewriting its record, exactly as
    # time would.
    meta_path = profile / "state" / "jobs" / job_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["ended_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return job_id


def test_prune_counts_only_dirs_it_actually_removed(profile, capsys):
    blocked = _aged_finished_job(profile, capsys, "blocked")
    clean = _aged_finished_job(profile, capsys, "clean")

    # A subdirectory inside a job dir: the unlink sweep skips it (unlink on a
    # directory raises OSError), so rmdir then refuses the non-empty dir and
    # the job dir survives on disk.
    nested = profile / "state" / "jobs" / blocked / "artifacts"
    nested.mkdir()
    (nested / "clip.mp4").write_bytes(b"not a real video")

    code = run(["--profile", str(profile), "--json", "job", "prune", "--older-than", "14"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    # The blocked dir is still on disk, so it must not be counted; the clean
    # one is gone and is the only removal in the count.
    assert payload["removed"] == 1
    assert not (profile / "state" / "jobs" / clean).exists()
    assert (profile / "state" / "jobs" / blocked).is_dir()
