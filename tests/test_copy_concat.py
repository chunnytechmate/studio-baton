"""The stream-copy fast path: joining clips that already agree.

Most of a lesson's clips come from one phone filming one session, and joining
those at packet boundaries needs no decode at all. Whether a set of clips
qualifies is decided by :func:`_copy_safe`, a pure function over probed
traits, so every dimension of disagreement is pinned here without ffmpeg
present. The integration tests at the bottom run the real thing and are
skipped wherever ffmpeg is not installed.

The list of disagreement dimensions is not guesswork: each one is something
the concat *demuxer* is strict about where the concat *filter* was forgiving,
which is the whole difference between the two paths.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from baton.adapters.media.base import EncodeProfile
from baton.adapters.media.ffmpeg import (
    ClipTraits,
    FfmpegEncoder,
    _concat_list,
    _copy_args,
    _copy_eligible,
    _copy_safe,
)

ENCODER = FfmpegEncoder()

has_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.usefixtures("tmp_path_cwd")


@pytest.fixture
def tmp_path_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the integration cases from inside the temp directory.

    The copy path resolves clip paths for the demuxer playlist; doing that
    from a stable working directory keeps every generated file absolute.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _known(**overrides: object) -> ClipTraits:
    """Traits a probe can fully see: two of these can always copy."""
    values: dict[str, object] = {
        "width": 1920,
        "height": 1080,
        "sar": "0:1",
        "hdr": False,
        "sample_rate": "48000",
        "channel_layout": "stereo",
        "codec": "h264",
        "profile": "High",
        "pix_fmt": "yuv420p",
        "fps": "30/1",
        "time_base": "1/15360",
        "audio_codec": "aac",
    }
    values.update(overrides)
    return ClipTraits(**values)  # type: ignore[arg-type]


# -- what qualifies ---------------------------------------------------------


def test_two_fully_known_and_matching_clips_can_copy():
    assert _copy_safe([_known(), _known()])


def test_a_single_clip_can_copy():
    """A one-clip session has nothing to disagree with; the copy path is a
    remux, and a remux never re-encodes a single-clip lesson either."""
    assert _copy_safe([_known()])


def test_an_empty_list_never_copies():
    assert not _copy_safe([])


def test_an_unprobed_clip_refuses_the_copy_path():
    """A probe that fails reads as unknown, and an unseen parameter is not an
    agreed one. Falling back to the encode path is what it is for."""
    assert not _copy_safe([_known(), ClipTraits()])


def test_a_codec_disagreement_refuses_the_copy_path():
    assert not _copy_safe([_known(), _known(codec="hevc")])


def test_a_profile_disagreement_refuses_the_copy_path():
    assert not _copy_safe([_known(profile="Baseline"), _known(profile="High")])


def test_a_pixel_format_disagreement_refuses_the_copy_path():
    assert not _copy_safe([_known(), _known(pix_fmt="yuv420p10le")])


def test_a_frame_rate_disagreement_refuses_the_copy_path():
    assert not _copy_safe([_known(fps="60/1"), _known(fps="30/1")])


def test_a_time_base_disagreement_refuses_the_copy_path():
    """Different time bases are the quiet one: the join succeeds and the
    second clip plays at the wrong speed."""
    assert not _copy_safe([_known(time_base="1/15360"), _known(time_base="1/900")])


def test_a_rotation_disagreement_refuses_the_copy_path():
    """Two clips that both probe as 1920x1080 but carry different display
    matrices are different orientations; the copy path must see that, because
    the demuxer will not stop it."""
    assert not _copy_safe([_known(), _known(width=1080, height=1920)])


def test_an_audio_disagreement_refuses_the_copy_path():
    assert not _copy_safe([_known(), _known(sample_rate="44100")])
    assert not _copy_safe([_known(), _known(channel_layout="mono")])
    assert not _copy_safe([_known(), _known(audio_codec="opus")])


def test_a_clip_without_audio_refuses_the_copy_path():
    """Silence on both sides could copy, but a half-known audio shape is the
    probe's way of saying it could not look, and the encode path handles
    audio-less clips on its own terms."""
    assert not _copy_safe([_known(audio_codec=""), _known(audio_codec="")])


# -- what the profile allows -------------------------------------------------


def test_the_switch_can_turn_the_copy_path_off():
    assert not _copy_eligible([_known(), _known()], EncodeProfile(copy_when_safe=False))


def test_a_forced_frame_rate_blocks_the_copy_path():
    """Retime is a change to every frame's timestamp; a copy cannot make it."""
    assert not _copy_eligible([_known(), _known()], EncodeProfile(fps=30))


def test_tone_mapping_blocks_the_copy_path_for_hdr_clips():
    """Uniform HDR clips agree with each other; the profile still promised to
    rewrite their pixels, and copying would break that promise."""
    hdr = _known(hdr=True)
    assert _copy_eligible([hdr, hdr], EncodeProfile(tone_map_hdr=False))
    assert not _copy_eligible([hdr, hdr], EncodeProfile(tone_map_hdr=True))


def test_the_1080p_profile_only_copies_clips_already_there():
    assert _copy_eligible([_known(), _known()], EncodeProfile(name="1080p"))
    assert not _copy_eligible(
        [_known(width=1080, height=1920), _known(width=1080, height=1920)],
        EncodeProfile(name="1080p"),
    )


def test_encoder_args_do_not_block_the_copy_path():
    """They tune an encoder the copy path never runs; the fallback encode
    still uses them."""
    assert _copy_eligible([_known(), _known()], EncodeProfile(extra_args=["-preset", "faster"]))


# -- the command line and the playlist --------------------------------------


def test_the_copy_command_uses_the_concat_demuxer_and_copies_streams(tmp_path: Path):
    args = _copy_args(tmp_path / "list.txt", tmp_path / "out.mp4")

    assert args[args.index("-f") + 1] == "concat"
    assert args[args.index("-safe") + 1] == "0"
    assert args[args.index("-c") + 1] == "copy"
    assert "-filter_complex" not in args
    assert "-c:v" not in args


def test_the_playlist_holds_absolute_escaped_paths(tmp_path: Path):
    one = tmp_path / "IMG_0001.MOV"
    one.touch()
    list_path = tmp_path / "list.txt"
    _concat_list(list_path, [one])

    body = list_path.read_text(encoding="utf-8")
    assert body.startswith("file '")
    assert str(one.resolve()) in body


def test_a_quote_in_a_filename_is_escaped_not_injected(tmp_path: Path):
    hostile = tmp_path / "it's.mov"
    hostile.touch()
    list_path = tmp_path / "list.txt"
    _concat_list(list_path, [hostile])

    body = list_path.read_text(encoding="utf-8")
    assert body.count("file '") == 1
    assert "'\\''" in body


# -- the real thing ----------------------------------------------------------


def _make_clip(path: Path, *, size: str, rate: int = 30) -> Path:
    """One second of agreeable footage: h264, yuv420p, aac 48k stereo."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate={rate}:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_matching_clips_join_by_stream_copy(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", size="320x240")
    b = _make_clip(tmp_path / "b.mp4", size="320x240")

    result = ENCODER.combine([a, b], tmp_path / "out.mp4", EncodeProfile())

    assert result.method == "stream-copy"
    duration = ENCODER._duration(result.path)
    assert duration == pytest.approx(2.0, abs=0.5)


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_matching_clips_keep_every_original_frame(tmp_path: Path):
    """Copy means first-generation: the output's video stream is the same
    codec its sources were, not something an encoder wrote."""
    a = _make_clip(tmp_path / "a.mp4", size="320x240")
    b = _make_clip(tmp_path / "b.mp4", size="320x240")

    result = ENCODER.combine([a, b], tmp_path / "out.mp4", EncodeProfile())

    probed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,profile,pix_fmt",
            "-of",
            "csv=p=0",
            str(result.path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert probed == "h264,High,yuv420p"


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_a_single_clip_is_remuxed_not_re_encoded(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", size="320x240")

    result = ENCODER.combine([a], tmp_path / "out.mp4", EncodeProfile())

    assert result.method == "stream-copy"
    assert ENCODER._duration(result.path) == pytest.approx(1.0, abs=0.5)


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_disagreeing_clips_fall_back_to_the_encode_path(tmp_path: Path):
    """Different frame sizes never qualified; the output must come from the
    encoder, and the job record must be able to say so."""
    a = _make_clip(tmp_path / "a.mp4", size="320x240")
    b = _make_clip(tmp_path / "b.mp4", size="240x320")

    result = ENCODER.combine([a, b], tmp_path / "out.mp4", EncodeProfile())

    assert result.method == "encode"
    assert ENCODER._duration(result.path) == pytest.approx(2.0, abs=0.5)


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg and ffprobe are needed")
def test_a_switched_off_profile_encodes_even_matching_clips(tmp_path: Path):
    a = _make_clip(tmp_path / "a.mp4", size="320x240")
    b = _make_clip(tmp_path / "b.mp4", size="320x240")

    result = ENCODER.combine([a, b], tmp_path / "out.mp4", EncodeProfile(copy_when_safe=False))

    assert result.method == "encode"
