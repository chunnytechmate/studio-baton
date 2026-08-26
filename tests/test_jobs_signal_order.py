"""``supervise()`` must install its SIGTERM/SIGINT handlers before spawning
the child process.

Regression (M28): the handlers were installed *after* ``subprocess.Popen``
returned. A SIGTERM landing in that window found no handler to catch it and
no ``child`` reference yet to terminate — meta stayed stuck at "running"
until orphan detection found it on a later pass.
"""

from __future__ import annotations

from baton.core.jobs import JobRunner

JOB_ID = "20260826-030000-000-sigo"


class _FakeChild:
    """Stands in for ``subprocess.Popen``'s return value."""

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_signal_handlers_installed_before_popen(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    (state_dir / "jobs" / JOB_ID).mkdir(parents=True)
    runner = JobRunner(state_dir=state_dir, config_file=tmp_path / "baton.yaml")

    call_order: list[str] = []

    def recording_signal(signum, handler):
        call_order.append("signal")
        return None

    def recording_popen(argv, **kwargs):
        call_order.append("popen")
        return _FakeChild()

    monkeypatch.setattr("baton.core.jobs.signal.signal", recording_signal)
    monkeypatch.setattr("baton.core.jobs.subprocess.Popen", recording_popen)

    exit_code = runner.supervise(JOB_ID, ["true"])

    assert exit_code == 0
    # Two handlers (SIGTERM, SIGINT) must both precede the single Popen call.
    assert call_order == ["signal", "signal", "popen"]
