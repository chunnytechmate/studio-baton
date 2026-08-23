"""FfmpegEncoder's command-line construction — the encoder that was timing
out on CPU alone, and the GPU codec option added to fix that.

`_args()` is pure (no subprocess, no real ffmpeg needed), so these pin the
exact flags without touching a GPU or spawning a process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baton.adapters.media.base import EncodeProfile
from baton.adapters.media.ffmpeg import FfmpegEncoder
from baton.errors import ConfigError

ENCODER = FfmpegEncoder()
CLIP = Path("/clips/a.mov")
OUT = Path("/work/out.mp4")


def test_the_default_codec_is_still_libx264():
    args = ENCODER._args([CLIP], OUT, EncodeProfile())

    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-crf" in args


def test_h264_nvenc_replaces_the_video_codec_only():
    args = ENCODER._args([CLIP], OUT, EncodeProfile(codec="h264_nvenc"))

    assert args[args.index("-c:v") + 1] == "h264_nvenc"
    assert "-rc" in args and args[args.index("-rc") + 1] == "vbr"
    assert "-cq" in args
    # Audio is unaffected by the video codec choice either way.
    assert args[args.index("-c:a") + 1] == "aac"


def test_an_unknown_codec_is_refused_before_ffmpeg_ever_runs():
    with pytest.raises(ConfigError, match=r"Unknown media\.encode\.codec"):
        ENCODER._args([CLIP], OUT, EncodeProfile(codec="vp9_potato"))


def test_gpu_codec_still_concatenates_multiple_clips_on_cpu():
    """Decode and the concat filter stay untouched by the codec choice —
    only -c:v changes. Confirms the GPU path didn't accidentally also try to
    move the filter graph, which would need a whole different (and much more
    fragile) hwaccel pipeline."""
    args = ENCODER._args([CLIP, Path("/clips/b.mov")], OUT, EncodeProfile(codec="h264_nvenc"))

    assert "-filter_complex" in args
    graph = args[args.index("-filter_complex") + 1]
    assert "concat=n=2" in graph
    assert "hwupload" not in graph and "cuda" not in graph


def test_passthrough_ignores_the_codec_entirely():
    """A single clip with no re-encode needed skips codec selection — an
    unknown codec here must not raise, since it's never used."""
    args = ENCODER._args([CLIP], OUT, EncodeProfile(name="passthrough", codec="something_invalid"))

    assert args[-2:] != []  # sanity: args were built
    assert "-c" in args
    assert args[args.index("-c") + 1] == "copy"
    assert "-c:v" not in args


def test_gpu_codec_composes_with_the_1080p_profile():
    args = ENCODER._args(
        [CLIP, Path("/clips/b.mov")], OUT, EncodeProfile(name="1080p", codec="h264_nvenc")
    )

    graph = args[args.index("-filter_complex") + 1]
    assert "scale=1920:1080" in graph  # the SDR/1080p filter still applies
    assert args[args.index("-c:v") + 1] == "h264_nvenc"
