"""Crash-safe JSON and text persistence.

Ported from the original workspace ``scripts/utils.py``, with the POSIX-only
``fcntl`` dependency replaced by a portable lock so Baton runs on Windows too.

The guarantee callers rely on: a kill at any instant leaves either the previous
file fully intact or the new file fully written — never a truncated one. Every
piece of resumable pipeline state in Baton goes through here.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

if sys.platform == "win32":  # pragma: no cover - platform specific
    import msvcrt
else:
    import fcntl


@contextmanager
def _locked(path: Path, exclusive: bool) -> Iterator[None]:
    """Hold an advisory lock on a sidecar ``.lock`` file for the duration.

    Advisory locking is best-effort by design: it serialises Baton against
    itself (two commands touching one state file), which is the only contention
    that actually happens. It is not a defence against an unrelated process
    rewriting the file.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        try:
            if sys.platform == "win32":  # pragma: no cover - platform specific
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                if sys.platform == "win32":  # pragma: no cover - platform specific
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass


def backup_path(path: Path) -> Path:
    """Sidecar snapshot path used as the recovery source for a corrupt file."""
    return path.with_suffix(path.suffix + ".bak")


def write_json(path: str | Path, data: Any, *, backup: bool = True) -> None:
    """Write ``data`` as JSON atomically, optionally snapshotting the old file.

    Writes to a temp file in the same directory, fsyncs it, then ``os.replace``
    (atomic on one filesystem) into place while holding an exclusive lock.

    Args:
        path: Destination file.
        data: Any JSON-serialisable value.
        backup: Copy the existing file to ``<name>.bak`` before replacing it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with _locked(path, exclusive=True):
        if backup and path.exists():
            backup_path(path).write_bytes(path.read_bytes())
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON, falling back to the ``.bak`` snapshot. Never raises.

    A missing file yields ``default``. A corrupt or unreadable file is retried
    from the backup snapshot before giving up — so a state file truncated by a
    power cut degrades to the previous good state instead of crashing a
    pipeline mid-run.

    Args:
        path: File to read.
        default: Returned when neither the file nor its backup is usable.

    Returns:
        The parsed value, or ``default``.
    """
    path = Path(path)

    def _load(candidate: Path) -> tuple[bool, Any]:
        if not candidate.exists():
            return False, None
        try:
            with _locked(candidate, exclusive=False), open(candidate, encoding="utf-8") as handle:
                return True, json.load(handle)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False, None

    ok, value = _load(path)
    if ok:
        return value

    ok, value = _load(backup_path(path))
    if ok:
        return value

    return default


def write_text(path: str | Path, text: str, *, backup: bool = True) -> None:
    """Write text atomically with the same crash guarantees as :func:`write_json`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with _locked(path, exclusive=True):
        if backup and path.exists():
            backup_path(path).write_bytes(path.read_bytes())
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
