"""The CLI's promises: stable exit codes, and a JSON mode that stays parseable.

An agent driving Baton reads two things — the exit code and the JSON document
on stdout. Both are asserted here for success and for every failure class the
shell can produce.
"""

from __future__ import annotations

import json

import pytest

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
    code = run(["--profile", str(profile), "doctor", "--offline"])

    # chat.driver is webhook and BATON_WEBHOOK_URL is unset in the isolated env.
    assert code == Exit.CONFIG
    out = capsys.readouterr().out
    assert "✗" in out


def test_doctor_passes_once_credentials_are_present(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    # --offline: the suite must never depend on reaching Notion or a database.
    code = run(["--profile", str(profile), "doctor", "--offline"])

    assert code == Exit.OK
    assert "✗" not in capsys.readouterr().out


def test_doctor_json_lists_every_check(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(profile), "--json", "doctor", "--offline"])

    assert code == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"] == 0
    assert payload["drivers"]["db"] == "sqlite"
    assert all("name" in check for check in payload["checks"])


def test_doctor_checks_the_schema_mapping_without_a_network(profile, monkeypatch, capsys):
    """The most common misconfiguration — a column that does not exist — is
    caught offline, so it can be checked on a laptop before a deploy."""
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("BATON__DB__TABLES__LEARNERS", "not a table name")

    code = run(["--profile", str(profile), "--json", "doctor", "--offline"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    failed = [c["name"] for c in payload["checks"] if not c["passed"]]
    assert any("schema mapping" in name for name in failed)


def test_doctor_offline_skips_the_reachability_checks(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "--json", "doctor", "--offline"])
    names = [c["name"] for c in json.loads(capsys.readouterr().out)["checks"]]

    assert not any("reachable" in name for name in names)
    assert any("schema mapping" in name for name in names)


def test_doctor_rejects_an_unknown_driver(profile, monkeypatch, capsys):
    monkeypatch.setenv("BATON__DB__DRIVER", "mysql")

    code = run(["--profile", str(profile), "--json", "doctor", "--offline"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    failed = [c for c in payload["checks"] if not c["passed"]]
    assert any("mysql" in c["name"] for c in failed)


def test_doctor_rejects_an_unknown_timezone(profile, monkeypatch, capsys):
    monkeypatch.setenv("BATON__TIMEZONE", "Mars/Olympus")

    code = run(["--profile", str(profile), "--json", "doctor", "--offline"])

    assert code == Exit.CONFIG
    payload = json.loads(capsys.readouterr().out)
    assert any(not c["passed"] and "Mars/Olympus" in c.get("detail", "") for c in payload["checks"])


def test_progress_output_never_pollutes_json_stdout(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "--json", "doctor", "--offline"])

    # Parsing the whole of stdout must succeed — nothing else may be written there.
    json.loads(capsys.readouterr().out)


# -- global flags anywhere ---------------------------------------------------


def test_json_works_before_and_after_the_subcommand(profile, capsys):
    """`baton learner list --json` is what people type and what every skill
    documents. It failing with exit 2 sends an agent into a retry loop."""
    leading = run(["--profile", str(profile), "--json", "config", "show", "docs.driver"])
    first = json.loads(capsys.readouterr().out)

    trailing = run(["--profile", str(profile), "config", "show", "docs.driver", "--json"])
    second = json.loads(capsys.readouterr().out)

    assert leading == trailing == Exit.OK
    assert first == second


def test_a_trailing_flag_does_not_undo_a_leading_one(profile, capsys):
    """The subtle half: a subparser's own default must not overwrite a value
    already set at the top level, or the leading form would silently stop
    working the moment the flags were duplicated."""
    code = run(["--profile", str(profile), "--json", "config", "show", "docs.driver"])

    assert code == Exit.OK
    json.loads(capsys.readouterr().out)  # still JSON, not human output


def test_quiet_and_profile_are_accepted_after_the_subcommand(profile, capsys):
    code = run(["config", "show", "docs.driver", "--profile", str(profile), "--quiet", "--json"])

    assert code == Exit.OK
    assert json.loads(capsys.readouterr().out)["value"] == "notion"


def test_flags_work_at_the_third_level_too(profile, capsys):
    """Sub-subcommands are where this is easiest to get wrong."""
    code = run(["--profile", str(profile), "job", "list", "--json"])

    assert code == Exit.OK
    assert "jobs" in json.loads(capsys.readouterr().out)


# -- usage errors get the contract's exit code, not argparse's ---------------


def test_an_unknown_command_exits_usage_not_config(profile, capsys):
    """argparse raises SystemExit(2) — the code the contract reserves for
    configuration problems. An agent branching on exit codes would diagnose a
    typo as a broken baton.yaml."""
    from baton.exits import Exit

    assert run(["frobnicate"]) == int(Exit.USAGE)

    err = capsys.readouterr().err
    assert "Unknown command `frobnicate`" in err


def test_an_unknown_flag_after_a_valid_command_exits_usage(profile, capsys):
    from baton.exits import Exit

    assert run(["learner", "--nope"]) == int(Exit.USAGE)

    err = capsys.readouterr().err
    assert "--nope" in err


def test_a_usage_error_still_emits_a_json_envelope(profile, capsys):
    assert run(["--json", "frobnicate"]) == int(Exit.USAGE)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "usage"
    assert payload["exit_code"] == 1
    assert "frobnicate" in payload["message"]


def test_help_and_version_still_exit_through_argparse(profile, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(["--version"])
    assert excinfo.value.code == 0
