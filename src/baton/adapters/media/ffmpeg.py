"""Combining clips with ffmpeg.

Properties carried over from the original, all learned the hard way:

**A watchdog.** An encode that wedges holds the whole nightly run until someone
notices the next morning. Every invocation has a timeout and is killed when it
expires.

**Atomic output.** The encode writes to a temp file and renames on success, so
a killed run leaves nothing at the destination. A half-written mp4 is
indistinguishable from a finished one, and the next run would happily upload it.

**Every segment is made concat-compatible first.** ``concat`` the *filter* is
forgiving about codecs, which is why it is used here instead of the demuxer,
but it still requires every segment to share a frame size and sample aspect
ratio, and every audio segment to share a sample rate and channel layout.
Phone clips routinely do not: one lesson filmed half in landscape and half in
portrait produces three files that ffprobe all report as 1920x1080, two of
which carry a -90 display matrix and therefore decode to 1080x1920. Feeding
those straight into ``concat`` fails at filter-configure time. So each input
gets its own normalising chain *before* the concat, not one chain after it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...errors import ConfigError, UpstreamError
from .base import CombineResult, EncodeProfile

#: The deliverable frame. Everything that has to be resized lands here.
_TARGET_WIDTH = 1920
_TARGET_HEIGHT = 1080

#: Fit inside the target without distorting, centred on black, square pixels.
#: ``setsar=1`` is not decoration: concat compares sample aspect ratio as well
#: as pixel dimensions, and phone clips report SAR inconsistently.
_FIT = (
    f"scale={_TARGET_WIDTH}:{_TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={_TARGET_WIDTH}:{_TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
    "setsar=1"
)

#: HDR (HLG or PQ) linearised, mapped to BT.709, ready for an 8-bit SDR
#: encode. Without this an iPhone HDR clip keeps its transfer characteristics
#: and plays back washed out or too dark on the SDR displays that parents
#: actually watch on. Applied per clip, only to clips that probe as HDR, so
#: an all-SDR session encodes exactly as it did before.
_TONE_MAP = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709:t=bt709:m=bt709:r=tv"

#: What every normalised segment ends on, so the concat sees one pixel format.
_PIXEL_FORMAT = "format=yuv420p"

#: Audio shape every segment is brought to when they disagree.
_AUDIO_RATE = 48000
_AUDIO_LAYOUT = "stereo"

#: Demuxer flags for clips trimmed in the iPhone Photos app without "Save as
#: New Clip": those carry an edit list whose timestamps make the container
#: duration disagree with the stream duration, and concat inherits the gap.
_INPUT_FLAGS = ["-fflags", "+genpts+igndts"]

#: Encoder args per `EncodeProfile.codec`. Deliberately CPU-decode,
#: CPU-filter (rotation/scale/tone-map/concat), GPU-encode-only when a codec
#: asks for NVENC: offloading decode and the filter graph too would need a
#: hwaccel/hwupload pipeline (format-mismatch prone, and a real VRAM cost for
#: every concurrent decode surface); the encode itself is the expensive step
#: that was timing out, and NVENC's own footprint for it is small regardless
#: of how little VRAM the card has to spare.
_CODEC_ARGS = {
    "libx264": ["-c:v", "libx264", "-preset", "medium", "-crf", "20"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20"],
}

#: How long a single probe may take before it is abandoned. A probe that hangs
#: must not hold the encode's own watchdog hostage.
_PROBE_TIMEOUT = 120

#: Transfer/primaries/colour-space values that mean HDR, and the pixel formats
#: that only exist above 8-bit. Mirrors what the pipeline this replaced tested.
_HDR_TRANSFERS = ("smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12")
_HDR_PRIMARIES = ("bt2020",)
_HDR_PIXEL_MARKERS = ("10le", "10be", "12le", "12be", "p010", "p012")

#: ffmpeg reports one fault across many lines. Everything below is downstream
#: of the real diagnosis: the encoder shutting down, the muxer finding
#: nothing to write, the task threads unwinding. Skipping these is what makes
#: the difference between a job record that says
#: "Error while processing the decoded data for stream #2:1" and one that says
#: which two clips disagreed about their frame size.
_CASCADE_PREFIXES = (
    "Error opening input file",
    "Error reinitializing filters",
    "Error while filtering",
    "Error while processing the decoded data",
    "Error sending frames to consumers",
    "Failed to inject frame into filter network",
    "Could not open encoder before EOF",
    "Task finished with error code",
    "Terminating thread with return code",
    "Nothing was written into output file",
    "Conversion failed",
    "Error closing file",
)

#: `[Parsed_concat_0 @ 0x55ef812c4640]` -> `[Parsed_concat_0]`. The address is
#: noise that changes every run and eats the message's character budget.
_ADDRESS = re.compile(r"\[([^\]@]+) @ 0x[0-9a-f]+\]")


@dataclass(frozen=True)
class ClipTraits:
    """What one source clip looks like to the concat filter.

    ``width``/``height`` are the *displayed* size: the rotation in the
    display matrix already applied, because that is the size the filter graph
    sees, and disagreeing about it is the failure this class exists to detect.

    The fields below ``channel_layout`` exist for the copy path: the concat
    *demuxer* is far stricter than the filter, and a parameter it needs that
    the filter never asked about is exactly the kind of thing two clips from
    the same phone agree on and two clips from different phones do not.
    """

    width: int = 0
    height: int = 0
    sar: str = ""
    hdr: bool = False
    sample_rate: str = ""
    channel_layout: str = ""
    codec: str = ""
    profile: str = ""
    pix_fmt: str = ""
    fps: str = ""
    time_base: str = ""
    audio_codec: str = ""

    @property
    def video_shape(self) -> tuple[int, int, str]:
        return (self.width, self.height, self.sar)

    @property
    def audio_shape(self) -> tuple[str, str]:
        return (self.sample_rate, self.channel_layout)


def _is_hdr(stream: dict) -> bool:
    """Whether a video stream needs tone-mapping to look right in SDR."""
    transfer = str(stream.get("color_transfer", "")).lower()
    primaries = str(stream.get("color_primaries", "")).lower()
    space = str(stream.get("color_space", "")).lower()
    pixel = str(stream.get("pix_fmt", "")).lower()

    if any(marker in transfer for marker in _HDR_TRANSFERS):
        return True
    if any(marker in primaries or marker in space for marker in _HDR_PRIMARIES):
        return True
    return any(marker in pixel for marker in _HDR_PIXEL_MARKERS)


def _rotation(stream: dict) -> int:
    """Degrees of display rotation, from side data or the legacy tag."""
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(float(side_data["rotation"]))
            except (TypeError, ValueError):
                continue
    tag = (stream.get("tags") or {}).get("rotate")
    try:
        return int(float(str(tag)))
    except (TypeError, ValueError):
        return 0


def _traits_from_streams(streams: list[dict]) -> ClipTraits:
    """Fold one ffprobe stream list into the shape concat cares about."""
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return ClipTraits()

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if abs(_rotation(video)) % 180 == 90:
        # A quarter turn is what makes an ffprobe-reported 1920x1080 arrive at
        # the filter graph as 1080x1920.
        width, height = height, width

    return ClipTraits(
        width=width,
        height=height,
        sar=str(video.get("sample_aspect_ratio") or ""),
        hdr=_is_hdr(video),
        sample_rate=str((audio or {}).get("sample_rate") or ""),
        channel_layout=str((audio or {}).get("channel_layout") or ""),
        codec=str(video.get("codec_name") or ""),
        profile=str(video.get("profile") or ""),
        pix_fmt=str(video.get("pix_fmt") or ""),
        fps=str(video.get("r_frame_rate") or ""),
        time_base=str(video.get("time_base") or ""),
        audio_codec=str((audio or {}).get("codec_name") or ""),
    )


#: Traits the concat demuxer needs agreed before packets can be joined as-is.
#: A missing value means the probe could not see that far into the file, and
#: an unseen parameter is not an agreed one.
_COPY_REQUIRED = (
    "codec",
    "profile",
    "pix_fmt",
    "fps",
    "time_base",
    "audio_codec",
    "sample_rate",
    "channel_layout",
)


def _copy_safe(traits: list[ClipTraits]) -> bool:
    """Whether these clips can be joined at packet boundaries without decoding.

    Everything the concat demuxer is strict about must be both *known* and
    *equal*: codec, profile, pixel format, frame rate and time base, the
    displayed frame (rotation already folded in), sample aspect ratio, HDR
    state, and the whole audio shape. One unknown clip or one disagreement
    means no: the fallback is the encode path this file has always had.
    """
    if not traits:
        return False
    for item in traits:
        if not item.width or not item.height:
            return False
        if any(not getattr(item, name) for name in _COPY_REQUIRED):
            return False
    shapes = {
        (
            item.codec,
            item.profile,
            item.pix_fmt,
            item.width,
            item.height,
            item.sar,
            item.fps,
            item.time_base,
            item.hdr,
            item.audio_codec,
            item.sample_rate,
            item.channel_layout,
        )
        for item in traits
    }
    return len(shapes) == 1


def _copy_eligible(traits: list[ClipTraits], profile: EncodeProfile) -> bool:
    """Whether this run may join at packet boundaries instead of encoding.

    Beyond agreement between the clips, the profile must not be asking for
    anything a copy cannot deliver: a forced frame rate retimes frames, an
    enabled tone-map rewrites pixels, the 1080p profile promises a frame
    size. ``extra_args`` tune an encoder, and the copy path has no encoder
    to tune, so they simply do not apply to it.
    """
    if not profile.copy_when_safe:
        return False
    if profile.fps > 0:
        return False
    if profile.tone_map_hdr and any(item.hdr for item in traits):
        return False
    if profile.name == "1080p" and {(i.width, i.height) for i in traits} != {
        (_TARGET_WIDTH, _TARGET_HEIGHT)
    }:
        return False
    return _copy_safe(traits)


def _concat_list(list_path: Path, inputs: list[Path]) -> None:
    """Write the concat demuxer's playlist.

    Absolute paths, because the demuxer resolves relative entries against the
    list file, not the working directory; the one quote a camera filename
    could carry is escaped the way the format's own docs spell out.
    """
    lines = []
    for source in inputs:
        escaped = str(source.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_args(list_path: Path, output: Path) -> list[str]:
    """Build the stream-copy command line. Pure: no subprocess runs here."""
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *_INPUT_FLAGS,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(output),
    ]


def _one_line(stderr: str | None) -> str:
    """The most informative line of an ffmpeg failure, as a single line.

    ffmpeg reports a fault across several lines and the diagnosis is the
    *first* of them; what follows is the rest of the pipeline unwinding. Job
    records keep only this string, so picking the wrong line costs the operator
    the whole investigation.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        return "no output"

    def body(line: str) -> str:
        return line.split("] ", 1)[-1] if line.startswith("[") else line

    meaningful = [line for line in lines if not body(line).startswith(_CASCADE_PREFIXES)]
    chosen = (meaningful or lines)[0]
    return _ADDRESS.sub(r"[\1]", chosen)[:200]


class FfmpegEncoder:
    """A :class:`~baton.adapters.media.base.VideoEncoder` backed by ffmpeg."""

    driver = "ffmpeg"

    def __init__(self, binary: str = "ffmpeg") -> None:
        self.binary = binary

    @property
    def prober(self) -> str:
        """The ffprobe that ships beside the configured ffmpeg."""
        path = Path(self.binary)
        if path.name.startswith("ffmpeg"):
            return str(path.with_name("ffprobe" + path.name[len("ffmpeg") :]))
        return "ffprobe"

    def health(self) -> None:
        """Confirm ffmpeg is installed and runnable."""
        if shutil.which(self.binary) is None:
            raise ConfigError(
                f"`{self.binary}` is not on PATH.",
                remedy="Install ffmpeg, or set media.encode.binary to its full path.",
            )

    def probe(self, source: Path) -> ClipTraits:
        """What ``source`` looks like to the filter graph.

        A probe that fails is not fatal: empty traits read as "unknown", which
        makes the caller disagree with every other clip and therefore normalise
        everything. Failing safe here is cheaper than refusing to encode.
        """
        command = [self.prober, "-v", "error", "-of", "json", "-show_streams", str(source)]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False
            )
        except (subprocess.TimeoutExpired, OSError):
            return ClipTraits()
        if completed.returncode != 0:
            return ClipTraits()
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ClipTraits()
        return _traits_from_streams(list(payload.get("streams") or []))

    def _duration(self, path: Path) -> float:
        """Container duration in seconds, 0 when it cannot be read."""
        command = [
            self.prober,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT, check=False
            )
        except (subprocess.TimeoutExpired, OSError):
            return 0.0
        if completed.returncode != 0:
            return 0.0
        try:
            return float(completed.stdout.strip() or 0)
        except ValueError:
            return 0.0

    def _try_copy(self, inputs: list[Path], temp_path: Path, profile: EncodeProfile) -> bool:
        """Attempt the stream-copy join. True when it produced a sane file.

        Never raises: every failure, up to and including a copy that succeeds
        but lands on the wrong duration, means the encode path runs instead.
        The durations of the inputs are read first because they are needed to
        judge the output, and cannot be probed after the fact from a file that
        no longer exists.
        """
        expected = sum(self._duration(source) for source in inputs)
        if expected <= 0:
            return False

        list_handle, list_name = tempfile.mkstemp(
            dir=temp_path.parent, prefix=".baton-concat-", suffix=".txt"
        )
        os.close(list_handle)
        list_path = Path(list_name)
        try:
            _concat_list(list_path, inputs)
            try:
                completed = subprocess.run(
                    _copy_args(list_path, temp_path),
                    capture_output=True,
                    text=True,
                    timeout=profile.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                temp_path.unlink(missing_ok=True)
                return False
            if completed.returncode != 0:
                temp_path.unlink(missing_ok=True)
                return False
            if not temp_path.exists() or temp_path.stat().st_size == 0:
                return False

            # A copy that ran can still be wrong: edit lists and per-file time
            # bases both surface as an output whose duration is not the sum of
            # its parts. Judged generously, because trimmed-in-Photos clips
            # carry small container-level disagreements by design.
            actual = self._duration(temp_path)
            if actual <= 0 or abs(actual - expected) > max(1.0, 0.01 * expected):
                temp_path.unlink(missing_ok=True)
                return False
            return True
        finally:
            list_path.unlink(missing_ok=True)

    def _segment_chains(
        self,
        traits: list[ClipTraits],
        profile: EncodeProfile,
    ) -> tuple[list[str], list[str], list[str]]:
        """Per-input normalising chains, and the labels concat should read.

        Returns ``(chains, video_labels, audio_labels)``. When nothing needs
        normalising the chain list comes back empty and the labels are the raw
        input pads, which is byte-for-byte the graph this encoder built before
        segment normalisation existed: an already-uniform session encodes
        exactly as it always did.
        """
        # "1080p" always lands on the target frame. Every other profile only
        # resizes when the clips disagree, so a session filmed entirely in
        # portrait stays portrait instead of gaining pillarbox it never had.
        uniform_video = len({item.video_shape for item in traits}) <= 1
        uniform_audio = len({item.audio_shape for item in traits}) <= 1
        fit = profile.name == "1080p" or not uniform_video

        chains: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []

        for index, item in enumerate(traits):
            filters: list[str] = []
            if profile.tone_map_hdr and item.hdr:
                filters.append(_TONE_MAP)
            if fit:
                filters.append(_FIT)
            if profile.fps > 0:
                filters.append(f"fps={profile.fps}")
            if filters:
                filters.append(_PIXEL_FORMAT)
                chains.append(f"[{index}:v:0]{','.join(filters)}[v{index}]")
                video_labels.append(f"[v{index}]")
            else:
                video_labels.append(f"[{index}:v:0]")

            if uniform_audio:
                audio_labels.append(f"[{index}:a:0]")
            else:
                chains.append(
                    f"[{index}:a:0]"
                    f"aformat=sample_rates={_AUDIO_RATE}:channel_layouts={_AUDIO_LAYOUT}"
                    f"[a{index}]"
                )
                audio_labels.append(f"[a{index}]")

        return chains, video_labels, audio_labels

    def _args(
        self,
        inputs: list[Path],
        output: Path,
        profile: EncodeProfile,
        traits: list[ClipTraits] | None = None,
    ) -> list[str]:
        """Build the command line for one encode.

        Pure: no subprocess runs here. ``traits`` are probed by the caller and
        passed in, so the whole graph stays testable without ffmpeg present.
        Omitting them means "nothing is known", which normalises nothing beyond
        what the profile asks for outright.
        """
        shapes = traits if traits is not None else [ClipTraits() for _ in inputs]

        args = [self.binary, "-y", "-hide_banner", "-loglevel", "error"]
        for source in inputs:
            args += [*_INPUT_FLAGS, "-i", str(source)]

        if profile.name == "passthrough" and len(inputs) == 1:
            # Nothing is decoded, so nothing needs normalising.
            args += ["-c", "copy", *profile.extra_args, str(output)]
            return args

        chains, video_labels, audio_labels = self._segment_chains(shapes, profile)

        if len(inputs) > 1:
            # concat the filter rather than the demuxer: the clips come from
            # phones and rarely share a codec, which the demuxer requires and
            # the filter does not. The filter still requires a shared frame
            # size and sample rate, which is what the chains above guarantee.
            pads = "".join(
                video + audio for video, audio in zip(video_labels, audio_labels, strict=True)
            )
            graph = ";".join([*chains, f"{pads}concat=n={len(inputs)}:v=1:a=1[v][a]"])
            args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
        elif chains:
            graph = ";".join(chains)
            # A lone input's audio never earns a real label (there is nothing
            # to reconcile it with), so what `_segment_chains` handed back is
            # the raw input pad spelled `[0:a:0]`. Inside a filter graph that
            # spelling means "audio stream 0 of input 0"; in `-map`, brackets
            # mean "a label the graph defined", and no graph defines one
            # called `0:a:0`: mapping it verbatim fails the whole command
            # with "Output with label '0:a:0' does not exist in any defined
            # filter graph". This is dormant until a lone clip grows a video
            # chain (a forced fps, the 1080p profile, tone-mapping), which is
            # exactly what media.encode.fps:60 did to every single-clip
            # session on 2026-08-29. Unbracketed, `0:a:0` is a plain stream
            # specifier, which -map accepts beside a graph output; verified
            # against real ffmpeg 5.1.9 and 8.1.2.
            audio_map = audio_labels[0]
            if ":" in audio_map:  # a raw input pad like [0:a:0], not a label like [a0]
                audio_map = audio_map[1:-1]
            args += ["-filter_complex", graph, "-map", video_labels[0], "-map", audio_map]

        codec_args = _CODEC_ARGS.get(profile.codec)
        if codec_args is None:
            raise ConfigError(
                f"Unknown media.encode.codec `{profile.codec}`.",
                remedy=f"Set it to one of: {', '.join(sorted(_CODEC_ARGS))}.",
            )
        args += [*codec_args, "-c:a", "aac"]

        # Muxer-side companion to the demuxer flags above: an edit list can
        # leave the first packet at a negative timestamp.
        args += ["-avoid_negative_ts", "make_zero"]

        args += profile.extra_args
        args.append(str(output))
        return args

    def combine(self, inputs: list[Path], output: Path, profile: EncodeProfile) -> CombineResult:
        """Combine ``inputs`` into ``output``.

        Raises:
            ConfigError: No inputs, ffmpeg is missing, or the profile names an
                unknown ``codec``.
            UpstreamError: ffmpeg failed or exceeded its timeout: this is also
                what a GPU codec configured but not actually available at run
                time (driver gone, card busy) surfaces as, since ffmpeg is the
                one that discovers that, not Baton.
        """
        if not inputs:
            raise ConfigError("Cannot combine an empty list of clips.")
        self.health()
        output.parent.mkdir(parents=True, exist_ok=True)

        traits = [self.probe(source) for source in inputs]

        # Same directory as the destination so the rename is atomic; a temp
        # file on another filesystem would fall back to a copy.
        handle, temp_name = tempfile.mkstemp(
            dir=output.parent, prefix=".baton-encode-", suffix=output.suffix or ".mp4"
        )
        os.close(handle)
        temp_path = Path(temp_name)

        method = "encode"
        if _copy_eligible(traits, profile) and self._try_copy(inputs, temp_path, profile):
            # The clips agreed on everything the demuxer needs and the joined
            # file verified against its parts: nothing was decoded, so nothing
            # was re-encoded, and the deliverable is first-generation.
            os.replace(temp_path, output)
            return CombineResult(output, "stream-copy")

        try:
            completed = subprocess.run(
                self._args(inputs, temp_path, profile, traits),
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
                details={
                    "clips": [
                        {
                            "name": source.name,
                            "size": f"{item.width}x{item.height}" if item.width else "unknown",
                            "hdr": item.hdr,
                        }
                        for source, item in zip(inputs, traits, strict=True)
                    ],
                    "stderr": (completed.stderr or "")[-4000:],
                },
            )

        if not temp_path.exists() or temp_path.stat().st_size == 0:
            temp_path.unlink(missing_ok=True)
            raise UpstreamError("ffmpeg reported success but produced no output.", service="ffmpeg")

        os.replace(temp_path, output)
        return CombineResult(output, method)
