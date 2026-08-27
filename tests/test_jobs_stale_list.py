"""`job list` must not present days-old finished jobs as live work.

Regression, from the 2026-08-27 production night: a video job that failed on
Aug 22 (exit 6, heartbeat four and a half days stale) was still sitting in
`job list` five days later — indistinguishable from tonight's failure until
the ids were read. A terminal record older than the threshold is history:
hidden from the default listing, still readable by id, still prunable.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from baton.cli.app import run
from baton.exits import Exit


def _finished_job(profile: Path, capsys, label: str) -> str:
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
    return job_id


def _age_beyond_threshold(profile: Path, job_id: str, days: int) -> None:
    """Rewrite the record's end time, exactly as time would."""
    meta_path = profile / "state" / "jobs" / job_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["ended_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def test_a_stale_finished_job_leaves_the_default_listing(profile, capsys):
    old = _finished_job(profile, capsys, "old")
    _age_beyond_threshold(profile, old, days=5)

    code = run(["--profile", str(profile), "--json", "job", "list"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert old not in [job["id"] for job in payload["jobs"]]


def test_all_lists_stale_finished_jobs_again(profile, capsys):
    old = _finished_job(profile, capsys, "old")
    _age_beyond_threshold(profile, old, days=5)

    code = run(["--profile", str(profile), "--json", "job", "list", "--all"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert old in [job["id"] for job in payload["jobs"]]


def test_a_recent_finished_job_stays_listed(profile, capsys):
    recent = _finished_job(profile, capsys, "recent")

    code = run(["--profile", str(profile), "--json", "job", "list"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert recent in [job["id"] for job in payload["jobs"]]


def test_an_empty_listing_with_hidden_history_says_so(profile, capsys):
    old = _finished_job(profile, capsys, "old")
    _age_beyond_threshold(profile, old, days=5)
    run(["--profile", str(profile), "--json", "job", "list"])
    capsys.readouterr()

    code = run(["--profile", str(profile), "job", "list"])

    assert code == Exit.OK
    assert "Older finished ones are hidden" in capsys.readouterr().out


def test_a_stale_job_is_still_readable_by_id(profile, capsys):
    """Hidden from the listing is not gone: `job status` and `job logs` keep
    working for an operator who arrives with the id already in hand."""
    old = _finished_job(profile, capsys, "old")
    _age_beyond_threshold(profile, old, days=5)

    code = run(["--profile", str(profile), "--json", "job", "status", old])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == old
