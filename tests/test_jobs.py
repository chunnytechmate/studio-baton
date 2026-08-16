"""Detached jobs and whole-run locking.

These spawn real processes — that is the point. Everything else in the suite is
in-process; a background runner can only be trusted after it has actually
survived its parent's exit, recorded a real outcome, and been stopped by a real
signal.
"""

from __future__ import annotations

import contextlib
import json
import sys
import textwrap

import pytest

from baton.cli.app import run
from baton.core.jobs import pid_alive
from baton.errors import BusyError
from baton.exits import Exit


def _py(code: str) -> list[str]:
    """A short python command, as argv."""
    return [sys.executable, "-c", textwrap.dedent(code)]


def _spawn(profile, capsys, code: str, name: str = "test") -> str:
    """Spawn a job through the real CLI and return its id."""
    run(["--profile", str(profile), "--json", "job", "spawn", "--name", name, "--", *_py(code)])
    return json.loads(capsys.readouterr().out)["id"]


def test_spawn_records_done_and_captures_output(profile, capsys):
    job_id = _spawn(
        profile,
        capsys,
        """
        print("hello from the job")
        """,
    )

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done"
    assert payload["exit_code"] == 0

    run(["--profile", str(profile), "job", "logs", job_id])
    assert "hello from the job" in capsys.readouterr().out


def test_spawn_records_the_childs_real_exit_code(profile, capsys):
    job_id = _spawn(
        profile,
        capsys,
        """
        import sys
        print("about to fail")
        sys.exit(3)
        """,
    )

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])

    # wait inherits the job's own verdict, so a caller of a detached run sees
    # exactly what a foreground run would have exited with.
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 3


def test_wait_timeout_exits_running_and_reports_state(profile, capsys):
    job_id = _spawn(
        profile,
        capsys,
        """
        import time
        print("sleeping", flush=True)
        time.sleep(30)
        """,
    )

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "1"])

    assert code == Exit.RUNNING
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "running"

    # Clean up: this test's sleeper would otherwise outlive the suite.
    run(["--profile", str(profile), "--json", "job", "stop", job_id])
    capsys.readouterr()


def test_stop_terminates_a_running_job(profile, capsys):
    job_id = _spawn(
        profile,
        capsys,
        """
        import time
        time.sleep(60)
        """,
    )

    # Wait until the job is genuinely supervising before stopping it.
    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "1"])
    assert code == Exit.RUNNING
    capsys.readouterr()

    code = run(["--profile", str(profile), "--json", "job", "stop", job_id, "--grace", "15"])

    payload = json.loads(capsys.readouterr().out)
    assert code == Exit.OK
    assert payload["status"] in ("stopped", "failed")


def test_detached_process_survives_its_parent(profile, capsys, tmp_path):
    """The whole reason --detach exists: the job must outlive the CLI call.

    Spawns a job that writes a file, from a `baton` process that has already
    exited by the time we check — if the child died with its parent, the marker
    would never appear.
    """
    marker = tmp_path / "survivor.txt"
    job_id = _spawn(
        profile,
        capsys,
        f"""
        import time
        time.sleep(2)
        open({str(marker)!r}, "w").write("alive")
        """,
    )

    assert not marker.exists()  # parent has exited; job still working

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])
    assert code == Exit.OK
    assert marker.read_text() == "alive"


def test_list_shows_spawned_jobs(profile, capsys):
    first = _spawn(profile, capsys, "print('one')")
    second = _spawn(profile, capsys, "print('two')")

    code = run(["--profile", str(profile), "--json", "job", "list"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    ids = [job["id"] for job in payload["jobs"]]
    assert second in ids and first in ids
    # Newest first.
    assert ids.index(second) < ids.index(first)


def test_error_paths_emit_exactly_one_json_document(profile, capsys):
    """Regression: a command that reports a result *and* raises used to print
    two JSON documents on stdout. Every failure must stay one document."""
    code = run(["--profile", str(profile), "--json", "job", "status", "nope-000000"])

    assert code == Exit.USAGE
    # json.loads raises on extra data, so this asserts single-document-ness.
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_unknown_job_id_is_a_usage_error(profile, capsys):
    code = run(["--profile", str(profile), "--json", "job", "status", "nope-000000"])

    assert code == Exit.USAGE
    payload = json.loads(capsys.readouterr().out)
    assert "nope-000000" in payload["message"]


def test_spawn_requires_a_command(profile, capsys):
    code = run(["--profile", str(profile), "job", "spawn"])

    assert code == Exit.USAGE


def test_crashed_job_reads_as_orphaned(profile, capsys):
    """SIGKILL the supervisor so it cannot write a final record."""
    import os
    import signal
    import subprocess

    job_id = _spawn(
        profile,
        capsys,
        """
        import time
        time.sleep(60)
        """,
    )
    run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "1"])
    payload = json.loads(capsys.readouterr().out)
    supervisor_pid = payload.get("pid")
    assert supervisor_pid, "job record should carry the supervisor pid"

    # Kill hard enough that no handler can record anything.
    os.kill(supervisor_pid, signal.SIGKILL)

    # In real use the spawning CLI exits immediately and init adopts (and
    # reaps) the supervisor. In this test the spawner is pytest itself, so do
    # the reaping here — otherwise the zombie keeps answering pid probes.
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(supervisor_pid, 0)

    # Reaped above, so the pid is gone for good: the very next read derives
    # the orphaned status without anything having persisted the crash.
    code = run(["--profile", str(profile), "--json", "job", "status", job_id])

    assert code == Exit.STATE
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "state"
    assert payload["details"]["job"]["status"] == "orphaned"

    # The orphaned grandchild sleeper is still napping; take it with us.
    subprocess.run(["pkill", "-f", "time.sleep(60)"], capture_output=True, check=False)


def test_prune_removes_only_old_finished_jobs(profile, capsys):
    from datetime import datetime, timedelta, timezone

    job_id = _spawn(profile, capsys, "print('old job')")
    run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])
    capsys.readouterr()

    # Age the finished job past the cutoff by rewriting its record.
    meta_path = profile / "state" / "jobs" / job_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["ended_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="seconds"
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    code = run(["--profile", str(profile), "--json", "job", "prune", "--older-than", "14"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"] >= 1
    assert not meta_path.exists()


# -- run lock ---------------------------------------------------------------


def test_run_lock_blocks_a_second_holder(profile):
    import os

    from baton.core.jobs import run_lock

    first = run_lock(profile / "state", "video")
    first.acquire()
    try:
        second = run_lock(profile / "state", "video")
        with pytest.raises(BusyError) as excinfo:
            second.acquire()
        assert "video" in str(excinfo.value)
        # The collision message names the holder, so the operator can decide
        # whether to wait on that run or stop it.
        assert excinfo.value.details["holder_pid"] == str(os.getpid())
    finally:
        first.release()


def test_run_lock_is_reusable_after_release(profile):
    from baton.core.jobs import run_lock

    with run_lock(profile / "state", "video"):
        pass

    # Released cleanly → immediately acquirable again.
    with run_lock(profile / "state", "video"):
        pass


def test_run_lock_writes_the_holder_pid(profile):
    import os

    from baton.core.jobs import run_lock

    lock = run_lock(profile / "state", "encode")
    with lock:
        holder = lock.path.read_text(encoding="utf-8").strip()
        assert holder == str(os.getpid())


def test_pid_alive_of_a_real_process():
    import os

    assert pid_alive(os.getpid()) is True
    # pid 1 is init on POSIX; on Windows the System process. Both exist.
    assert pid_alive(1) is True


def test_pid_alive_rejects_nonsense():
    assert pid_alive(None) is False
    assert pid_alive(0) is False
    assert pid_alive(-1) is False


def test_wait_mirrors_an_exit_code_outside_the_contract(profile, capsys):
    """ffmpeg and shell scripts exit with codes the contract does not name.
    `job wait` must still report the job's own verdict — as a number, not a
    ValueError traceback with no envelope."""
    job_id = _spawn(
        profile,
        capsys,
        """
        import sys
        sys.exit(9)
        """,
    )

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])

    assert code == 9
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 9


def test_wait_reports_a_signal_death_as_the_shell_would(profile, capsys):
    job_id = _spawn(
        profile,
        capsys,
        """
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)
        """,
    )

    code = run(["--profile", str(profile), "--json", "job", "wait", job_id, "--timeout", "60"])

    assert code == 137  # 128 + SIGKILL, the convention an operator reads
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == -9  # wait()'s raw verdict is preserved in the record
