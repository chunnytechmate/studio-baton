#!/usr/bin/env python3
"""Guard against the three things that made the original workspace unpublishable.

1. Harness-specific absolute paths (``~/.openclaw/workspace``, ``/home/node``).
   These are why the original scripts could not be installed anywhere else.
2. Credential-shaped literals. Config names environment variables; a token
   appearing as a value is always a mistake.
3. Real personal data. Public CI cannot ship a list of real names, so the list
   lives outside the repository: point ``BATON_DENYLIST`` at a file with one
   term per line (the private overlay repo keeps one) and this check enforces
   it. Without that variable the check is skipped and says so: it never
   pretends to have verified something it did not.

Run directly, or as the `leaks` job in CI. Exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Checked in code only. A hardcoded harness path in a module is why the
# original scripts could not be installed anywhere else; the same path in a
# document is an installation instruction for that harness, which is useful.
FORBIDDEN_PATHS = [
    (re.compile(r"\.openclaw/workspace"), "harness-specific path"),
    (re.compile(r"/home/node\b"), "container-specific absolute path"),
    (re.compile(r"/home/[a-z][a-z0-9_-]*/(?!\.\.)"), "developer home directory"),
]

#: Extensions treated as code for the path checks above.
CODE_SUFFIXES = frozenset({".py", ".sql", ".yaml", ".yml", ".toml", ".cfg", ".sh", ".json"})

# Prefixes published by the vendors themselves as identifying a live credential.
SECRET_PATTERNS = [
    (re.compile(r"\bntn_[A-Za-z0-9]{20,}"), "Notion integration token"),
    (re.compile(r"\bsecret_[A-Za-z0-9]{32,}"), "Notion integration token (legacy)"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "Google API key"),
    (re.compile(r"\b1//[0-9A-Za-z_-]{30,}"), "Google OAuth refresh token"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"), "Slack token"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "GitHub personal access token"),
]

FORBIDDEN_NAMES = ("*.bak", "*.orig", "*.rej")

SKIP_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist"}

#: This file necessarily contains the patterns it searches for.
SELF = Path(__file__).name


def tracked_files(root: Path) -> list[Path]:
    """Files git knows about, falling back to a walk outside a repository."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],  # noqa: S607 - developer tool; git is on PATH or we fall back
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names = [n for n in out.split("\0") if n]
        if names:
            return [root / n for n in names]
    except (OSError, subprocess.CalledProcessError):
        pass

    found: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not (SKIP_DIRS & set(path.parts)):
            found.append(path)
    return found


def load_denylist() -> tuple[list[str], str | None]:
    """Read the external denylist, returning (terms, path-or-None)."""
    location = os.environ.get("BATON_DENYLIST")
    if not location:
        return [], None
    path = Path(location).expanduser()
    if not path.is_file():
        print(f"error: BATON_DENYLIST points at {path}, which does not exist", file=sys.stderr)
        raise SystemExit(1)
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return terms, str(path)


def scan(root: Path) -> list[str]:
    """Return one message per finding."""
    findings: list[str] = []
    denylist, denylist_path = load_denylist()

    for path in tracked_files(root):
        relative = path.relative_to(root)

        for pattern in FORBIDDEN_NAMES:
            if path.match(pattern):
                findings.append(f"{relative}: editor/backup file must not be committed")

        if path.name == SELF:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        is_code = path.suffix.lower() in CODE_SUFFIXES
        for number, line in enumerate(text.splitlines(), start=1):
            if is_code:
                for regex, label in FORBIDDEN_PATHS:
                    if regex.search(line):
                        findings.append(f"{relative}:{number}: {label}: {line.strip()[:80]}")
            for regex, label in SECRET_PATTERNS:
                if regex.search(line):
                    findings.append(f"{relative}:{number}: possible {label}")
            for term in denylist:
                if term in line:
                    findings.append(f"{relative}:{number}: denylisted term from {denylist_path}")

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings = scan(root)

    if not os.environ.get("BATON_DENYLIST"):
        print("note: BATON_DENYLIST is not set: personal-name check was not run.")

    if findings:
        print(f"\n{len(findings)} finding(s):\n", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1

    print("No leaks found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
