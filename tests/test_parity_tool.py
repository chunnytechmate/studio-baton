"""The parity harness.

It is the thing that decides when the old system can be retired, so its own
comparison logic has to be right — a harness that reports agreement it did not
verify is worse than no harness.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "parity", Path(__file__).resolve().parent.parent / "tools" / "parity.py"
)
parity = importlib.util.module_from_spec(_spec)
# Registered before execution: @dataclass resolves annotations through
# sys.modules, and a module missing from it fails at class-creation time.
sys.modules["parity"] = parity
_spec.loader.exec_module(parity)


@pytest.mark.parametrize(
    ("payload", "path", "expected"),
    [
        ({"a": {"b": 1}}, "a.b", 1),
        ({"a": [{"b": 2}]}, "a.0.b", 2),
        ({"a": {"b": 1}}, "a.c", None),
        ({"a": {"b": 1}}, "x.y.z", None),
        ({"a": None}, "a.b", None),
        ({"a": "text"}, "a.b", None),
    ],
)
def test_dig_reads_a_path_or_returns_none(payload, path, expected):
    """A missing field is a difference to report, not a crash mid-comparison."""
    assert parity.dig(payload, path) == expected


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (3, "3"),
        ("Done", "done"),
        ("  Done  ", "done"),
        (["A", "b"], ["a", "B"]),
    ],
)
def test_formatting_differences_are_folded(left, right):
    """The old scripts print numbers as strings. A report full of `"3" != 3`
    buries the one real difference."""
    assert parity.normalise(left) == parity.normalise(right)


@pytest.mark.parametrize(("left", "right"), [(3, 4), ("Done", "Not started"), (True, False)])
def test_real_differences_survive_normalisation(left, right):
    assert parity.normalise(left) != parity.normalise(right)


def test_booleans_are_not_folded_into_numbers():
    """True == 1 in Python; a status of True and a week of 1 are not the same
    answer, and folding them would hide a real disagreement."""
    assert parity.normalise(True) != parity.normalise(1)


def _spec_file(tmp_path, legacy_output, baton_output, *, legacy_field="week", baton_field="n"):
    """A spec whose two sides are trivial scripts printing fixed JSON."""
    legacy = tmp_path / "legacy.py"
    legacy.write_text(f"import json; print(json.dumps({legacy_output!r}))", encoding="utf-8")
    baton = tmp_path / "baton.py"
    baton.write_text(f"import json; print(json.dumps({baton_output!r}))", encoding="utf-8")

    spec = tmp_path / "spec.yaml"
    spec.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "fixed",
                        "legacy": f"{sys.executable} {legacy}",
                        "baton": f"{sys.executable} {baton}",
                        "compare": {"legacy": legacy_field, "baton": baton_field},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return spec


def test_matching_answers_exit_zero(tmp_path, monkeypatch, capsys):
    spec = _spec_file(tmp_path, {"week": "3"}, {"n": 3})
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec)])

    assert parity.main() == 0
    assert "Every case agreed" in capsys.readouterr().out


def test_a_disagreement_exits_one_and_shows_both_sides(tmp_path, monkeypatch, capsys):
    spec = _spec_file(tmp_path, {"week": "12"}, {"n": 3})
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec)])

    assert parity.main() == 1
    out = capsys.readouterr().out
    assert "1 disagreed" in out
    assert "'12'" in out and "'3'" in out


def test_a_side_that_fails_is_reported_rather_than_assumed_equal(tmp_path, monkeypatch, capsys):
    """The dangerous failure mode: a harness that treats "no output" as
    agreement would give the go-ahead to retire a working system."""
    legacy = tmp_path / "legacy.py"
    legacy.write_text("import sys; sys.exit(1)", encoding="utf-8")
    baton = tmp_path / "baton.py"
    baton.write_text("import json; print(json.dumps({'n': 3}))", encoding="utf-8")
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "one side errors",
                        "legacy": f"{sys.executable} {legacy}",
                        "baton": f"{sys.executable} {baton}",
                        "compare": {"legacy": "week", "baton": "n"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec)])

    assert parity.main() == 1
    assert "did not produce JSON" in capsys.readouterr().out


def test_non_json_output_is_a_disagreement_not_a_pass(tmp_path, monkeypatch, capsys):
    legacy = tmp_path / "legacy.py"
    legacy.write_text("print('W3 done')", encoding="utf-8")
    baton = tmp_path / "baton.py"
    baton.write_text("import json; print(json.dumps({'n': 3}))", encoding="utf-8")
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "legacy prints prose",
                        "legacy": f"{sys.executable} {legacy}",
                        "baton": f"{sys.executable} {baton}",
                        "compare": {"legacy": "week", "baton": "n"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec)])

    assert parity.main() == 1


def test_a_missing_spec_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(tmp_path / "absent.yaml")])

    assert parity.main() == 2


def test_a_spec_with_no_cases_exits_two(tmp_path, monkeypatch, capsys):
    spec = tmp_path / "spec.yaml"
    spec.write_text("cases: []\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec)])

    assert parity.main() == 2


def test_json_mode_reports_every_outcome(tmp_path, monkeypatch, capsys):
    spec = _spec_file(tmp_path, {"week": "12"}, {"n": 3})
    monkeypatch.setattr(sys, "argv", ["parity", "--spec", str(spec), "--json"])

    parity.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["compared"] == 1
    assert payload["disagreed"] == 1
    assert payload["outcomes"][0]["legacy"] == "12"
