"""What the video pipeline needs from the outside world.

Three separate concerns, three protocols: somewhere clips arrive, something
turns several clips into one file, somewhere the result is published. Keeping
them apart is what lets the orchestrator be tested end to end without ffmpeg,
a Google account, or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

#: Extensions treated as video, shared by every source driver. Anything else
#: in a source folder (a photo, a note, a stray pdf) is not a clip, and a
#: driver that picks it up sends it to ffmpeg as one.
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"})


@dataclass(frozen=True)
class SourceClip:
    """One video file waiting to be collected."""

    id: str
    name: str
    learner_folder: str
    size_bytes: int = 0
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "learner_folder": self.learner_folder,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class UploadResult:
    """Where a published video ended up."""

    video_id: str
    url: str
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"video_id": self.video_id, "url": self.url, "title": self.title}


@dataclass(frozen=True)
class CombineResult:
    """One combined file, and how it was produced.

    ``method`` says which of the two paths produced it: ``"stream-copy"`` when
    the clips already agreed on enough that they could be joined at packet
    boundaries without decoding, ``"encode"`` when they were decoded,
    normalised, and re-encoded. The job record carries it, so an operator can
    see which sessions skipped the encode and which did not.
    """

    path: Path
    method: str


@dataclass
class EncodeProfile:
    """How to turn source clips into one deliverable file."""

    name: str = "auto"
    timeout_seconds: int = 1800
    extra_args: list[str] = field(default_factory=list)
    #: Which encoder writes the output. "libx264" (CPU, the long-standing
    #: default) or "h264_nvenc" (NVIDIA GPU: much faster, needs the driver
    #: and a codec-capable ffmpeg build; see FfmpegEncoder._args).
    codec: str = "libx264"
    #: Tone-map HDR clips down to BT.709 before encoding. On by default: an
    #: untouched HDR source plays back washed out on the SDR screens most
    #: viewers use. Only clips that probe as HDR are affected. Turn off to
    #: keep HDR through to the deliverable.
    tone_map_hdr: bool = True
    #: Force every segment to this frame rate. 0 leaves each clip's own rate
    #: alone, which is the default because forcing 30 on 60fps phone footage
    #: throws away half the motion for no benefit the viewer asked for.
    fps: int = 0
    #: Join clips at packet boundaries, without decoding or re-encoding, when
    #: every probed clip agrees on the parameters the concat demuxer needs.
    #: On by default: a session filmed as one burst on one phone is the common
    #: case, and copying it is both faster by orders of magnitude and free of
    #: a second lossy generation. Any disagreement falls back to the encode
    #: path, as does anything that asks for a change copy cannot make (a
    #: forced frame rate, tone-mapping, the 1080p profile on other sizes).
    copy_when_safe: bool = True


@runtime_checkable
class MediaSource(Protocol):
    """Where recordings arrive: a Drive folder, a watched directory."""

    def list_pending(self) -> list[SourceClip]:
        """Every clip currently waiting, across all learner folders."""
        ...

    def download(self, clip: SourceClip, destination: Path) -> Path:
        """Fetch one clip. Implementations verify the size after transfer."""
        ...

    def trash(self, clip_ids: list[str]) -> int:
        """Move clips out of the way. Returns how many were moved.

        Called only after everything else for that learner has succeeded:
        see :mod:`baton.pipelines.video` on deferred trashing.
        """
        ...

    def health(self) -> None: ...


@runtime_checkable
class VideoEncoder(Protocol):
    """Combines and normalises clips."""

    def combine(self, inputs: list[Path], output: Path, profile: EncodeProfile) -> CombineResult:
        """Produce one file from several.

        Must write atomically: a killed encode leaves no partial file at
        ``output``, because a partial file is indistinguishable from a finished
        one on the next run.
        """
        ...

    def health(self) -> None: ...


@runtime_checkable
class VideoPublisher(Protocol):
    """Publishes the finished file and returns a link."""

    def upload(
        self, path: Path, *, title: str, description: str = "", privacy: str = "unlisted"
    ) -> UploadResult: ...

    def update_description(self, video_id: str, description: str) -> None: ...

    def health(self) -> None: ...
