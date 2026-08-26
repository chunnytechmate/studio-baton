"""`job stop` says how the stop went, not just that it returned (M27).

Exit 0 is kept deliberately — it reports the stop command, not the job's
fate, which travels in the payload. The human line is where a stop that
escalated past SIGTERM is said out loud instead of reading like a clean one.
"""

from __future__ import annotations

from baton.cli.cmd_job import _stop_line
from baton.core.jobs import JobInfo


def _info(status: str, exit_code: int | None = None) -> JobInfo:
    return JobInfo(
        id="j1",
        name="demo",
        command=["true"],
        status=status,
        exit_code=exit_code,
    )


def test_clean_stop_reads_like_one():
    assert _stop_line(_info("stopped", 0)) == "⏹ job j1 stopped  (exit 0)"


def test_sigkill_escalation_is_said_out_loud():
    line = _stop_line(_info("orphaned"))
    assert "killed hard" in line
    assert "orphaned" in line


def test_unsettled_job_points_at_show():
    line = _stop_line(_info("running"))
    assert "still running" in line
    assert "job show" in line
