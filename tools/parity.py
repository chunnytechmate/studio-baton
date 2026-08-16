#!/usr/bin/env python3
"""Compare a legacy script's answer with Baton's, on the same input.

A rewrite is trustworthy when it gives the same answers as the thing it
replaces, on that studio's own data — not when its tests pass. Tests were
written from the same understanding as the code, so they share its blind spots;
the old script does not.

Run this against the real system for a week before retiring anything. Read-only
by design: it runs lookups, never a send, a publish, or an upload.

    tools/parity.py --spec parity.yaml

The spec pairs commands and says which fields must agree:

    cases:
      - name: latest done for each learner
        for_each_learner: true
        legacy: "python3 {legacy}/student-management/scripts/student_lookup.py
                 --student {learner} --latest-done --json"
        baton: "baton learner latest {learner} --json"
        compare:
          legacy: week
          baton: latest_done.number

Exit 0 when everything agreed, 1 on any mismatch, 2 if the spec is unusable.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def dig(payload: Any, path: str) -> Any:
    """Read a dotted path out of parsed JSON, tolerating lists.

    Returns ``None`` for anything absent rather than raising: a missing field
    is a difference to report, not a crash that ends the comparison.
    """
    cursor = payload
    for part in path.split("."):
        if cursor is None:
            return None
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(cursor, dict):
            cursor = cursor.get(part)
            continue
        return None
    return cursor


def normalise(value: Any) -> Any:
    """Fold differences that are formatting rather than disagreement.

    The old scripts print week numbers as strings and Baton as integers; a
    parity report full of `"3" != 3` hides the one real difference in the noise.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped.casefold() if not stripped.isdigit() else stripped
    if isinstance(value, list):
        return [normalise(item) for item in value]
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in value.items()}
    return value


@dataclass
class Outcome:
    """One comparison."""

    case: str
    subject: str
    agreed: bool
    legacy: Any = None
    baton: Any = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {"case": self.case, "subject": self.subject, "agreed": self.agreed}
        if not self.agreed:
            payload.update({"legacy": self.legacy, "baton": self.baton})
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass
class Runner:
    """Runs both sides of each case and collects the differences."""

    spec: dict[str, Any]
    timeout: float = 120.0
    outcomes: list[Outcome] = field(default_factory=list)

    def _run(self, command: str) -> tuple[Any, str]:
        """Run a command, returning (parsed JSON or None, error description)."""
        try:
            completed = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"could not run: {exc}"

        if completed.returncode != 0:
            # A non-zero exit is information, not a failure of the harness:
            # "the old one errors here and the new one answers" is exactly the
            # kind of difference worth seeing.
            return None, f"exit {completed.returncode}: {(completed.stderr or '').strip()[:200]}"

        try:
            return json.loads(completed.stdout), ""
        except json.JSONDecodeError:
            return None, "output was not JSON"

    def _learners(self) -> list[str]:
        """Every learner name, from Baton — the only side that must be right."""
        payload, error = self._run(self.spec.get("learners_command", "baton learner list --json"))
        if payload is None:
            print(f"error: could not list learners ({error})", file=sys.stderr)
            raise SystemExit(2)
        return [str(item["name"]) for item in payload.get("learners", [])]

    def _compare(self, case: dict[str, Any], subject: str, substitutions: dict[str, str]) -> None:
        legacy_command = case["legacy"].format(**substitutions)
        baton_command = case["baton"].format(**substitutions)

        legacy_payload, legacy_error = self._run(legacy_command)
        baton_payload, baton_error = self._run(baton_command)

        if legacy_error or baton_error:
            self.outcomes.append(
                Outcome(
                    case=case["name"],
                    subject=subject,
                    agreed=False,
                    legacy=legacy_error or "ok",
                    baton=baton_error or "ok",
                    note="one side did not produce JSON",
                )
            )
            return

        fields = case.get("compare", {})
        legacy_value = normalise(dig(legacy_payload, fields["legacy"]))
        baton_value = normalise(dig(baton_payload, fields["baton"]))

        self.outcomes.append(
            Outcome(
                case=case["name"],
                subject=subject,
                agreed=legacy_value == baton_value,
                legacy=legacy_value,
                baton=baton_value,
            )
        )

    def run(self) -> None:
        substitutions = {
            "legacy": str(self.spec.get("legacy_root", "")),
            **{k: str(v) for k, v in (self.spec.get("vars") or {}).items()},
        }

        learners: list[str] | None = None
        for case in self.spec.get("cases", []):
            if case.get("for_each_learner"):
                if learners is None:
                    learners = self._learners()
                for learner in learners:
                    self._compare(case, learner, {**substitutions, "learner": shlex.quote(learner)})
            else:
                self._compare(case, case["name"], substitutions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path, help="YAML describing the cases.")
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if not args.spec.is_file():
        print(f"error: no spec at {args.spec}", file=sys.stderr)
        return 2

    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8")) or {}
    if not spec.get("cases"):
        print("error: the spec has no cases", file=sys.stderr)
        return 2

    runner = Runner(spec=spec, timeout=args.timeout)
    runner.run()

    disagreed = [outcome for outcome in runner.outcomes if not outcome.agreed]

    if args.json:
        print(
            json.dumps(
                {
                    "compared": len(runner.outcomes),
                    "agreed": len(runner.outcomes) - len(disagreed),
                    "disagreed": len(disagreed),
                    "outcomes": [outcome.to_dict() for outcome in runner.outcomes],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"{len(runner.outcomes)} compared, {len(disagreed)} disagreed\n")
        for outcome in disagreed:
            print(f"  ✗ {outcome.case} — {outcome.subject}")
            print(f"      legacy: {outcome.legacy!r}")
            print(f"      baton : {outcome.baton!r}")
            if outcome.note:
                print(f"      {outcome.note}")
        if not disagreed:
            print("  Every case agreed.")

    return 1 if disagreed else 0


if __name__ == "__main__":
    raise SystemExit(main())
