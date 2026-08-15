"""The CLI's promises: stable exit codes, and a JSON mode that stays parseable.

An agent driving Baton reads two things — the exit code and the JSON document
on stdout. Both are asserted here for success and for every failure class the
shell can produce.
"""

from __future__ import annotations

import json

from baton.cli.app import run
from baton.exits import Exit


def test_no_command_prints_help_and_reports_usage(capsys):
    code = run([])

    assert code == Exit.USAGE
    assert "usage" in capsys.readouterr().err.lower()


def test_unknown_key_in_config_show_exits_config(profile, capsys):
    code = run(["--profile", str(profile), "config", "show", "docs.nope"])

    assert code == Exit.CONFIG
    assert "docs.nope" in capsys.readouterr().err


def test_missing_profile_exits_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    code = run(["config", "path"])

    assert code == Exit.CONFIG
    assert "baton.yaml" in capsys.readouterr().err


def test_config_show_emits_a_single_json_document(profile, capsys):
    code = run(["--profile", str(profile), "--json", "config", "show", "docs.driver"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["value"] == "notion"


def test_json_failures_use_the_same_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    code = run(["--json", "config", "path"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "config"
    assert payload["exit_code"] == int(Exit.CONFIG)
    assert payload["remedy"]


def test_config_subcommand_is_required(profile, capsys):
    code = run(["--profile", str(profile), "config"])

    assert code == Exit.USAGE
    assert "subcommand" in capsys.readouterr().err


def test_doctor_fails_when_credentials_are_absent(profile, capsys):
    code = run(["--profile", str(profile), "doctor"])

    # chat.driver is webhook and BATON_WEBHOOK_URL is unset in the isolated env.
    assert code == Exit.CONFIG
    out = capsys.readouterr().out
    assert "✗" in out


def test_doctor_passes_once_credentials_are_present(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(profile), "doctor"])

    assert code == Exit.OK
    assert "✗" not in capsys.readouterr().out


def test_doctor_json_lists_every_check(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(profile), "--json", "doctor"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 0
    assert payload["drivers"]["db"] == "sqlite"
    assert all("name" in check for check in payload["checks"])


def test_doctor_rejects_an_unknown_driver(profile, monkeypatch, capsys):
    monkeypatch.setenv("BATON__DB__DRIVER", "mysql")

    code = run(["--profile", str(profile), "--json", "doctor"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    failed = [c for c in payload["checks"] if not c["passed"]]
    assert any("mysql" in c["name"] for c in failed)


def test_doctor_rejects_an_unknown_timezone(profile, monkeypatch, capsys):
    monkeypatch.setenv("BATON__TIMEZONE", "Mars/Olympus")

    code = run(["--profile", str(profile), "--json", "doctor"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    assert any(not c["passed"] and "Mars/Olympus" in c.get("detail", "") for c in payload["checks"])


def test_progress_output_never_pollutes_json_stdout(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "--json", "doctor"])

    # Parsing the whole of stdout must succeed — nothing else may be written there.
    json.loads(capsys.readouterr().out)
