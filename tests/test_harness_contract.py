"""What the CLI owes a harness that is not a person at a terminal.

Claude Code and OpenClaw both drive Baton the same way: run a command, read the
exit code, parse one JSON document. Both also do two things a terminal never
does: they kill a call that overruns their own time limit, and they branch on
the number rather than reading the message. Every test here is about a way that
combination used to produce a wrong conclusion.
"""

from __future__ import annotations

import io
import json
import os
import signal

import pytest

from baton.cli import app
from baton.cli.app import run
from baton.exits import SLUG, Exit


def _install(monkeypatch, handler):
    """Route every invocation of `config path` to ``handler``.

    A fake command is the only honest way to test the shell's own behaviour:
    the point is what happens around a handler, whatever the handler does.
    """
    original = app.build_parser

    def build_parser():
        parser = original()
        for action in parser._subparsers._group_actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict) and "config" in choices:
                for sub in choices["config"]._subparsers._group_actions:
                    if isinstance(getattr(sub, "choices", None), dict):
                        sub.choices["path"].set_defaults(handler=handler)
        return parser

    monkeypatch.setattr(app, "build_parser", build_parser)


# -- an unexpected exception is Baton's fault, and says so ------------------


def test_an_unexpected_exception_exits_internal_not_usage(profile, monkeypatch, capsys):
    """Exit 1 meant "you called this wrongly", and a crash used to claim it.

    An agent reading 1 rewrites its arguments and tries again: forever, since
    the arguments were never the problem.
    """

    def explode(_ctx):
        raise ZeroDivisionError("division by zero")

    _install(monkeypatch, explode)

    code = run(["--profile", str(profile), "--json", "config", "path"])

    assert code == int(Exit.INTERNAL) == 9
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == SLUG[Exit.INTERNAL]
    assert payload["exit_code"] == 9
    assert "ZeroDivisionError" in payload["message"]
    assert "ZeroDivisionError" in payload["details"]["traceback"]


def test_a_crash_still_produces_one_parseable_document(profile, monkeypatch, capsys):
    """stdout used to be empty on a crash, with a traceback on stderr.

    A caller that parses stdout and finds nothing cannot tell a crash from a
    command that produced no output.
    """

    def explode(_ctx):
        raise RuntimeError("boom")

    _install(monkeypatch, explode)

    run(["--profile", str(profile), "--json", "config", "path"])

    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "internal"
    assert captured.out.count('"ok"') == 1


def test_a_crash_in_human_mode_shows_the_traceback(profile, monkeypatch, capsys):
    def explode(_ctx):
        raise RuntimeError("boom")

    _install(monkeypatch, explode)

    run(["--profile", str(profile), "config", "path"])

    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "Traceback" in err


# -- SIGTERM is how a harness ends a long call ------------------------------


def test_sigterm_reports_itself_instead_of_dying_silently(profile, monkeypatch, capsys):
    """A harness kills what overruns its time limit; 143 is that, named.

    Without the handler the process died where it stood: no envelope, no exit
    code from the contract, and no way for the caller to tell "cut short" from
    "finished and printed nothing".
    """

    def slow(_ctx):
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM did not interrupt the handler")

    _install(monkeypatch, slow)

    code = run(["--profile", str(profile), "--json", "config", "path"])

    assert code == int(Exit.TERMINATED) == 143
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == SLUG[Exit.TERMINATED]
    assert payload["exit_code"] == 143
    # The caller must not conclude the work did or did not happen.
    assert "unknown" in payload["remedy"]


def test_the_previous_sigterm_disposition_is_restored(profile, monkeypatch):
    """The handler belongs to one command, not to the process.

    `job supervise` installs its own SIGTERM handler for the child it owns;
    leaving this one behind would take that over.
    """
    before = signal.getsignal(signal.SIGTERM)

    _install(monkeypatch, lambda _ctx: Exit.OK)
    run(["--profile", str(profile), "config", "path"])

    assert signal.getsignal(signal.SIGTERM) is before


# -- Thai must survive whatever the container says its encoding is ----------


def test_streams_are_forced_to_utf8(monkeypatch):
    """`PYTHONIOENCODING=ascii` used to lose the whole document.

    Baton dumps JSON with `ensure_ascii=False` on purpose (a parent reads the
    message), so an ascii stdout raised UnicodeEncodeError *after* the send or
    publish had already happened, and the report of what happened was lost.
    """
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    app._force_utf8_streams()

    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"
    stdout.write("สวัสดี")  # would have raised before


def test_forcing_utf8_survives_a_stream_that_cannot_be_reconfigured(monkeypatch):
    """A captured or replaced stdout is normal, not an error worth dying on."""

    class Plain:
        def write(self, _text: str) -> int:
            return 0

    monkeypatch.setattr("sys.stdout", Plain())
    monkeypatch.setattr("sys.stderr", Plain())

    app._force_utf8_streams()  # no raise


# -- what version am I talking to ------------------------------------------


def test_doctor_reports_the_version_it_is(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "--json", "doctor", "--offline"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["version"]


def test_every_exit_code_has_a_slug():
    """The slug is what JSON callers branch on; a code without one is a hole."""
    assert set(SLUG) == set(Exit)
    assert len(set(SLUG.values())) == len(SLUG)


@pytest.mark.parametrize(
    ("code", "value"),
    [(Exit.INTERNAL, 9), (Exit.INTERRUPTED, 130), (Exit.TERMINATED, 143)],
)
def test_the_new_codes_are_the_numbers_the_docs_promise(code, value):
    assert int(code) == value
