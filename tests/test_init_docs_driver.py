"""`init` does not ask for the document driver — on purpose (M30).

The registry has exactly one real implementation, so the prompt would be a
question with one answer. This test pins both halves of that rationale: the
single-driver fact that makes not-asking correct today, and the fact that
init indeed never asks. When a second driver lands, the first assert fails —
that is the reminder to add the prompt next to db/chat.
"""

from __future__ import annotations

import json

from baton.adapters.docs import DRIVERS
from baton.cli.app import run
from baton.exits import Exit


def test_init_skips_the_docs_prompt_because_there_is_one_driver(tmp_path, capsys):
    assert DRIVERS == ("notion",)

    assert run(["--json", "init", str(tmp_path / "studio"), "--yes"]) == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert "docs" not in payload["answers"]
