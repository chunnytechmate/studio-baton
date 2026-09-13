"""Shared CLI plumbing for the moment a typed name becomes a learner.

Every command group has its own thin ``_resolve`` wrapper (they differ in
what they resolve *with*), so the piece they all share lives here instead of
in any one of them: saying it out loud when the name that resolved belongs
to someone who stopped studying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..domain.models import Learner
from ..domain.resolve import inactive_note

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import Context


def warn_if_inactive(ctx: Context, learner: Learner) -> None:
    """Say it on stderr when a resolved learner stopped studying.

    Every command shares this through its resolve wrapper, so the warning
    reads identically wherever an inactive learner's exact name is used.
    In JSON mode the sentence stays off stdout (the envelope stays one
    document); the payload's ``is_active`` field carries the fact there.
    """
    note = inactive_note(learner, ctx.config.label("learner"))
    if note:
        ctx.report.warn(
            f"{note}. Their history stays reachable; reactivate with "
            f'`baton learner activate "{learner.name}"`.'
        )
