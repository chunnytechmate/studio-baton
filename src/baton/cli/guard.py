"""One writer at a time, per profile.

``video`` has held a whole-run lock since it existed, because two encoders
writing one temp file is obviously wrong. The quieter version of the same
problem is two *agents*: a Claude Code session and an OpenClaw container
pointed at one profile are two processes with no idea the other exists, and the
harness that starts them offers no mutual exclusion of its own.

Nothing in the state layer saved us there. `core.jsonio` locks each individual
write, which makes a file's bytes consistent but does nothing about a
read-modify-write straddling two of them: the shape of every publish, booking,
and send. The failure that produces is not a corrupt file; it is a lesson
published twice, or a booking whose document was marked by one run and whose
event was created by another.

So the writing commands take a named lock and collide loudly (exit ``8``, which
already means "something else is in the way; wait, do not start a second one").
Reads are untouched, and a ``--dry-run`` takes nothing: it writes nothing, and
refusing to *inspect* a gate because another run is in flight would be a worse
answer than letting both look.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from ..core.jobs import run_lock

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import Context

_R = TypeVar("_R")


def guarded(
    name: str, *, when: Callable[[Context], bool] | None = None
) -> Callable[[Callable[[Context], _R]], Callable[[Context], _R]]:
    """Run the decorated handler holding the profile's ``name`` write lock.

    Args:
        name: Lock name, shared by every command that writes the same records.
            Coarse on purpose: ``send`` rather than ``send-to-this-learner``:
            because the collisions worth preventing are between whole workflows,
            and a lock fine enough to never inconvenience anyone would also be
            fine enough to miss the case it exists for.
        when: Optional test for whether *this* invocation writes anything. Some
            commands are two commands wearing one name: `send recording` with
            no ``--pick`` only reads and hands back a list to choose from, and
            making that listing wait on an unrelated send in flight would be
            obstruction with no safety bought.

    A handler whose ``--dry-run`` flag is set also runs without the lock.
    """

    def decorate(handler: Callable[[Context], _R]) -> Callable[[Context], _R]:
        @functools.wraps(handler)
        def wrapper(ctx: Context) -> _R:
            writes = not getattr(ctx.args, "dry_run", False)
            if writes and when is not None:
                writes = when(ctx)
            if not writes:
                return handler(ctx)
            with run_lock(ctx.config.state_dir, name):
                return handler(ctx)

        return wrapper

    return decorate


__all__ = ["guarded"]
