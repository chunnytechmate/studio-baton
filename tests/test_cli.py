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


def test_a_missing_argument_names_the_argument_not_the_command(profile, capsys):
    assert run(["--json", "learner", "sessions"]) == int(Exit.USAGE)

    payload = json.loads(capsys.readouterr().out)
    assert "NAME" in payload["message"]
    assert "Unknown command `learner`" not in payload["message"]


def test_help_still_exits_through_argparse(profile, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(["--help"])
    assert excinfo.value.code == 0


def test_version_is_a_result_not_an_argparse_exit(profile, capsys):
    """`--version` goes through the reporter like every other answer.

    argparse's own `action="version"` printed prose and raised SystemExit
    before the JSON machinery existed, so `--json --version` answered in a
    format no caller could parse — and version is precisely the question an
    agent has to ask before it trusts the rest.
    """
    assert run(["--version"]) == int(Exit.OK)
    assert capsys.readouterr().out.startswith("baton ")


def test_version_in_json_lists_the_commands_that_exist(profile, capsys):
    assert run(["--json", "--version"]) == int(Exit.OK)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["version"]
    # A harness compares this against what its skills call, instead of
    # discovering the mismatch one `usage` exit at a time.
    assert "send lesson" in payload["commands"]
    assert "lesson publish" in payload["commands"]
    # The supervisor is an implementation detail of `job spawn`, not surface.
    assert not any(name.endswith("supervise") for name in payload["commands"])


def test_internal_job_supervisor_is_hidden_from_help(profile, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(["job", "--help"])

    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    assert "supervise" not in output
    assert "==SUPPRESS==" not in output


# -- what doctor was not checking ------------------------------------------
#
# `learner in-progress` reads the calendar every morning, and until now doctor
# said "all checks passed" with no calendar credential in sight — so an expired
# refresh token surfaced as a failed lesson lookup instead of a failed check.


def test_doctor_reports_the_calendar_credentials(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "doctor", "--offline"])

    assert "GOOGLE_CALENDAR_REFRESH_TOKEN" in capsys.readouterr().out


def test_a_calendar_nobody_configured_does_not_fail_doctor(profile, monkeypatch, capsys):
    """A studio that books no lessons through Baton is not misconfigured."""
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(profile), "doctor", "--offline"])

    assert code == Exit.OK
    assert "✗" not in capsys.readouterr().out


def test_strict_requires_the_calendar_credentials(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    code = run(["--profile", str(profile), "doctor", "--offline", "--strict"])

    assert code == Exit.CONFIG
    assert "GOOGLE_CALENDAR_REFRESH_TOKEN" in capsys.readouterr().out


def test_doctor_reports_the_encoder_binary(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")

    run(["--profile", str(profile), "doctor", "--offline"])

    assert "ffmpeg" in capsys.readouterr().out


def test_a_missing_encoder_fails_only_under_strict(profile, monkeypatch, capsys):
    monkeypatch.setenv("NOTION_API_TOKEN", "t")
    monkeypatch.setenv("BATON_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setenv("BATON__MEDIA__ENCODE__BINARY", "definitely-not-installed-xyz")

    assert run(["--profile", str(profile), "doctor", "--offline"]) == Exit.OK
    capsys.readouterr()

    assert run(["--profile", str(profile), "doctor", "--offline", "--strict"]) == Exit.CONFIG
    assert "not found" in capsys.readouterr().out
