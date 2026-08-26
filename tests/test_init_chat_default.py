"""`init`'s chat-driver prompt default must come from `defaults.yaml`, not a
second hardcoded literal.

Regression (M25): the prompt default was the literal string `"telegram"`
while `defaults.yaml` sets `chat.driver: line` — a user who pressed enter
through the prompt got a chat driver different from what an empty
`baton.yaml` (which inherits the packaged defaults) would have given them.
"""

from __future__ import annotations

import json

from baton.cli.app import run
from baton.core import config as config_module
from baton.exits import Exit


def init(tmp_path, *args):
    return run(["--json", "init", str(tmp_path / "studio"), "--yes", *args])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_the_chat_default_matches_defaults_yaml(tmp_path, capsys):
    """Today's packaged default (`line`) is what an unanswered prompt gives."""
    assert init(tmp_path) == Exit.OK
    payload = out(capsys)

    assert payload["answers"]["chat"] == config_module.packaged_defaults()["chat"]["driver"]
    assert payload["answers"]["chat"] == "line"


def test_the_chat_default_is_read_from_packaged_defaults_not_hardcoded(
    tmp_path, capsys, monkeypatch
):
    """Changing the packaged default must change the prompt default with it —
    the single-source proof, not just today's values happening to agree."""
    from baton.cli import cmd_init

    monkeypatch.setattr(
        cmd_init,
        "packaged_defaults",
        lambda: {**config_module.packaged_defaults(), "chat": {"driver": "webhook"}},
    )

    assert init(tmp_path) == Exit.OK
    assert out(capsys)["answers"]["chat"] == "webhook"


def test_an_explicit_chat_flag_still_wins_over_the_default(tmp_path, capsys):
    assert init(tmp_path, "--chat", "telegram") == Exit.OK
    assert out(capsys)["answers"]["chat"] == "telegram"
