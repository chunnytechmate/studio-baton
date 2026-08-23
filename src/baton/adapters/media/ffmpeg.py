"""Combining clips with ffmpeg.

Two properties carried over from the original, both learned the hard way:

**A watchdog.** An encode that wedges holds the whole nightly run until someone
notices the next morning. Every invocation has a timeout and is killed when it
expires.

**Atomic output.** The encode writes to a temp file and renames on success, so
a killed run leaves nothing at the destination. A half-written mp4 is
indistinguishable from a finished one, and the next run would happily upload it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...errors import ConfigError, UpstreamError
from .base import EncodeProfile

#: Filter chain for the "1080p" profile: fix rotation, tone-map HDR down to
#: SDR, and fit inside 1920x1080 without distorting the frame.
_SDR_1080P = (
    "scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv420p"
)

#: Encoder args per `EncodeProfile.codec`. Deliberately CPU-decode,
#: CPU-filter (rotation/tone-map/concat), GPU-encode-only when a codec asks
#: for NVENC — offloading decode and the concat filter too would need a
#: hwaccel/hwupload pipeline (format-mismatch prone, and a real VRAM cost for
#: every concurrent decode surface); the encode itself is the expensive step
#: that was timing out, and NVENC's own footprint for it is small regardless
#: of how little VRAM the card has to spare.
_CODEC_ARGS = {
    "libx264": ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20"],
}


def _one_line(stderr: str | None) -> str:
    """The most informative line of an ffmpeg failure, as a single line.

    ffmpeg reports a fault across several lines, often with the interesting
    one in the middle. Reports and job records are line-oriented, so a
    multi-line message turns a status table into unreadable wrap — the last
    substantive line is nearly always the diagnosis.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return "no output"
    # Drop the trailing "Error opening input file <very long path>" that only
    # repeats a path the caller already knows.
    meaningful = [line for line in lines if not line.startswith("Error opening input file")]
    chosen = (meaningful or lines)[-1]
    return chosen[:200]


class FfmpegEncoder:
    """A :class:`~baton.adapters.media.base.VideoEncoder` backed by ffmpeg."""

    driver = "ffmpeg"

    def __init__(self, binary: str = "ffmpeg") -> None:
        self.binary = binary

    def health(self) -> None:
        """Confirm ffmpeg is installed and runnable."""
        if shutil.which(self.binary) is None:
            raise ConfigError(
                f"`{self.binary}` is not on PATH.",
                remedy="Install ffmpeg, or set media.encode.binary to its full path.",
            )

    def _args(self, inputs: list[Path], output: Path, profile: EncodeProfile) -> list[str]:
        """Build the command line for one encode."""
        args = [self.binary, "-y", "-hide_banner", "-loglevel", "error"]
        for source in inputs:
            args += ["-i", str(source)]

        if len(inputs) > 1:
            # concat filter rather than the demuxer: the clips come from
            # phones and rarely share a codec or resolution, which the demuxer
            # requires and the filter does not.
            streams = "".join(f"[{index}:v:0][{index}:a:0]" for index in range(len(inputs)))
            filtergraph = f"{streams}concat=n={len(inputs)}:v=1:a=1[v][a]"
            if profile.name == "1080p":
                filtergraph += f";[v]{_SDR_1080P}[vout]"
                args += ["-filter_complex", filtergraph, "-map", "[vout]", "-map", "[a]"]
            else:
                args += ["-filter_complex", filtergraph, "-map", "[v]", "-map", "[a]"]
        elif profile.name == "1080p":
            args += ["-vf", _SDR_1080P]

        if profile.name == "passthrough" and len(inputs) == 1:
            args += ["-c", "copy"]
        else:
            codec_args = _CODEC_ARGS.get(profile.codec)
            if codec_args is None:
                raise ConfigError(
                    f"Unknown media.encode.codec `{profile.codec}`.",
                    remedy=f"Set it to one of: {', '.join(sorted(_CODEC_ARGS))}.",
                )
            args += [*codec_args, "-c:a", "aac"]

        args += profile.extra_args
        args.append(str(output))
        return args

    def combine(self, inputs: list[Path], output: Path, profile: EncodeProfile) -> Path:
        """Combine ``inputs`` into ``output``.

        Raises:
            ConfigError: No inputs, ffmpeg is missing, or the profile names an
                unknown ``codec``.
            UpstreamError: ffmpeg failed or exceeded its timeout — this is also
                what a GPU codec configured but not actually available at run
                time (driver gone, card busy) surfaces as, since ffmpeg is the
                one that discovers that, not Baton.
        """
        if not inputs:
            raise ConfigError("Cannot combine an empty list of clips.")
        self.health()
        output.parent.mkdir(parents=True, exist_ok=True)

        # Same directory as the destination so the rename is atomic; a temp
        # file on another filesystem would fall back to a copy.
        handle, temp_name = tempfile.mkstemp(
            dir=output.parent, prefix=".baton-encode-", suffix=output.suffix or ".mp4"
        )
        os.close(handle)
        temp_path = Path(temp_name)

        try:
            completed = subprocess.run(
                self._args(inputs, temp_path, profile),
                capture_output=True,
                text=True,
                timeout=profile.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            temp_path.unlink(missing_ok=True)
            raise UpstreamError(
                f"ffmpeg exceeded its {profile.timeout_seconds}s limit and was killed.",
                service="ffmpeg",
                remedy="Raise media.encode.timeout_minutes, or check whether the "
                "source clips are corrupt.",
            ) from exc

        if completed.returncode != 0:
            temp_path.unlink(missing_ok=True)
            raise UpstreamError(
                f"ffmpeg failed: {_one_line(completed.stderr)}",
                service="ffmpeg",
                remedy="Check the source clips are playable. Run the same encode by "
                "hand for the full output.",
            )

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            temp_path.unlink(missing_ok=True)
            raise UpstreamError("ffmpeg reported success but produced no output.", service="ffmpeg")

        os.replace(temp_path, output)
        return output
