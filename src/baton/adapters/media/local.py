"""A watched directory as a clip source.

For a studio that already has the recordings on disk (an SD card copied into
a folder, a NAS share) and for trying the pipeline without a Google account.

"Trashing" moves files into a ``.collected`` subdirectory rather than deleting
them. The pipeline's contract is "the source is the only copy until the upload
succeeds", and honouring it here means a mistake is recoverable with `mv`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ...core.config import Config
from ...errors import ConfigError
from .base import VIDEO_SUFFIXES, SourceClip

COLLECTED_DIRNAME = ".collected"


class LocalSource:
    """Clips in per-learner subdirectories of one root."""

    driver = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @classmethod
    def from_config(cls, config: Config) -> LocalSource:
        return cls(config.path("media.source.local.path"))

    def list_pending(self) -> list[SourceClip]:
        if not self.root.is_dir():
            return []
        clips: list[SourceClip] = []
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.name == COLLECTED_DIRNAME:
                continue
            for item in sorted(folder.iterdir()):
                if not item.is_file() or item.suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                try:
                    size = item.stat().st_size
                except OSError:
                    # Removed between listing and stat; not this run's clip.
                    continue
                clips.append(
                    SourceClip(
                        id=str(item.resolve()),
                        name=item.name,
                        learner_folder=folder.name,
                        size_bytes=size,
                    )
                )
        return clips

    def download(self, clip: SourceClip, destination: Path) -> Path:
        """Copy rather than move: the source stays the only copy until trashed."""
        source = Path(clip.id)
        if not source.is_file():
            raise ConfigError(f"The clip {clip.name} is no longer at {source}.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def trash(self, clip_ids: list[str]) -> int:
        moved = 0
        for clip_id in clip_ids:
            source = Path(clip_id)
            if not source.is_file():
                continue
            target = self.root / COLLECTED_DIRNAME / source.parent.name
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target / source.name))
            moved += 1
        return moved

    def health(self) -> None:
        if not self.root.is_dir():
            raise ConfigError(
                f"No clip directory at {self.root}.",
                remedy="Create it, or correct media.source.local.path.",
            )
