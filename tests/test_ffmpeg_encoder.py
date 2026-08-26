"""FfmpegEncoder's command-line construction — the encoder that was timing
out on CPU alone, and the GPU codec option added to fix that.

`_args()` is pure (no subprocess, no real ffmpeg needed), so these pin the
exact flags without touching a GPU or spawning a process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from baton.adapters.media.base import EncodeProfile
from baton.adapters.media.ffmpeg import (
    ClipTraits,
    FfmpegEncoder,
    _one_line,
    _traits_from_streams,
)
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


# --------------------------------------------------------------------------
# Segment normalisation — the concat filter shares codecs but not frame sizes
# --------------------------------------------------------------------------

LANDSCAPE = ClipTraits(width=1920, height=1080, sample_rate="48000", channel_layout="stereo")
PORTRAIT = ClipTraits(width=1080, height=1920, sample_rate="48000", channel_layout="stereo")
HDR_LANDSCAPE = ClipTraits(
    width=1920, height=1080, hdr=True, sample_rate="48000", channel_layout="stereo"
)
TWO_CLIPS = [CLIP, Path("/clips/b.mov")]


def _graph(args: list[str]) -> str:
    return args[args.index("-filter_complex") + 1]


def test_a_quarter_turn_is_applied_to_the_probed_size():
    """The failure this whole path exists for: ffprobe says 1920x1080, the
    display matrix says -90, and the filter graph gets 1080x1920."""
    traits = _traits_from_streams(
        [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            },
            {"codec_type": "audio", "sample_rate": "48000", "channel_layout": "stereo"},
        ]
    )

    assert traits.width == 1080
    assert traits.height == 1920


def test_an_unrotated_clip_keeps_its_probed_size():
    traits = _traits_from_streams([{"codec_type": "video", "width": 1920, "height": 1080}])

    assert (traits.width, traits.height) == (1920, 1080)


def test_the_legacy_rotate_tag_counts_too():
    traits = _traits_from_streams(
        [{"codec_type": "video", "width": 1920, "height": 1080, "tags": {"rotate": "270"}}]
    )

    assert (traits.width, traits.height) == (1080, 1920)


def test_mixed_orientation_is_normalised_before_the_concat_not_after():
    """Landscape mixed with portrait is exactly what failed in production.
    Each input needs its own fit chain; a single chain after the concat never
    runs, because concat refuses to configure its output pad first."""
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, PORTRAIT])
    graph = _graph(args)

    assert graph.startswith("[0:v:0]scale=1920:1080")
    assert "[1:v:0]scale=1920:1080" in graph
    assert "setsar=1" in graph
    # The concat reads the normalised pads, never the raw ones.
    assert "[v0][0:a:0][v1][1:a:0]concat=n=2" in graph
    assert graph.index("scale=1920:1080") < graph.index("concat=n=2")


def test_a_uniform_session_is_left_exactly_as_it_was():
    """Every clip already agreeing is the common case and it already worked.
    Normalisation must not touch it — no rescale, no pillarbox, no re-tagging
    of output that has been going out to parents unchanged for months."""
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, LANDSCAPE])

    assert _graph(args) == "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[v][a]"


def test_an_all_portrait_session_stays_portrait_under_auto():
    """`auto` means "make them agree", not "make them landscape". A lesson
    filmed entirely in portrait has nothing to reconcile, so forcing it into a
    1920x1080 frame would add pillarbox the session never had."""
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [PORTRAIT, PORTRAIT])

    assert "scale=" not in _graph(args)


def test_the_1080p_profile_still_fits_a_uniform_session():
    """`1080p` is the explicit ask, so it resizes whether or not it has to."""
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(name="1080p"), [PORTRAIT, PORTRAIT])

    assert "scale=1920:1080" in _graph(args)


def test_only_the_hdr_clip_is_tone_mapped():
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [HDR_LANDSCAPE, LANDSCAPE])
    graph = _graph(args)

    assert graph.count("zscale") == 2  # linearise + back to bt709, on one clip
    assert graph.startswith("[0:v:0]zscale")
    assert "[1:v:0]zscale" not in graph


def test_tone_mapping_can_be_turned_off():
    args = ENCODER._args(
        TWO_CLIPS, OUT, EncodeProfile(tone_map_hdr=False), [HDR_LANDSCAPE, LANDSCAPE]
    )

    assert "zscale" not in _graph(args)


def test_an_all_sdr_session_gains_no_tone_map_step():
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, LANDSCAPE])

    assert "zscale" not in _graph(args)


def test_audio_is_reconciled_only_when_the_clips_disagree():
    mono = ClipTraits(width=1920, height=1080, sample_rate="44100", channel_layout="mono")

    mixed = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, mono]))
    uniform = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, LANDSCAPE]))

    assert mixed.count("aformat=sample_rates=48000:channel_layouts=stereo") == 2
    # Video already agreed, so only the audio pads are the normalised ones.
    assert "[0:v:0][a0][1:v:0][a1]concat=n=2" in mixed
    assert "aformat" not in uniform


def test_the_frame_rate_knob_is_off_by_default_and_applies_to_every_segment():
    default = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, LANDSCAPE]))
    forced = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(fps=30), [LANDSCAPE, LANDSCAPE]))

    assert "fps=" not in default
    assert forced.count("fps=30") == 2


def test_a_normalised_segment_ends_on_one_pixel_format():
    """Whatever a chain did — rotate, tone-map, rescale — the concat has to
    receive the same pixel format from every branch."""
    graph = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, PORTRAIT]))

    assert graph.count("format=yuv420p") == 2


def test_unknown_traits_normalise_rather_than_guess():
    """A probe that failed reads as empty traits. Empty disagrees with a real
    clip, so the graph fits both rather than betting they happen to match."""
    graph = _graph(ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, ClipTraits()]))

    assert graph.count("scale=1920:1080") == 2


def test_edit_list_flags_are_set_per_input_and_at_the_muxer():
    """iPhone clips trimmed in Photos without "Save as New Clip" carry an edit
    list; the demuxer flags go before each -i, the muxer flag before output."""
    args = ENCODER._args(TWO_CLIPS, OUT, EncodeProfile(), [LANDSCAPE, LANDSCAPE])

    assert args.count("+genpts+igndts") == 2
    for index in [i for i, item in enumerate(args) if item == "-i"]:
        assert args[index - 1] == "+genpts+igndts"
    assert args[args.index("-avoid_negative_ts") + 1] == "make_zero"


def test_passthrough_of_a_single_clip_is_still_untouched():
    """-c copy decodes nothing, so there is nothing to normalise and no place
    to put a filter."""
    args = ENCODER._args([CLIP], OUT, EncodeProfile(name="passthrough"), [HDR_LANDSCAPE])

    assert "-filter_complex" not in args
    assert "-vf" not in args
    assert args[args.index("-c") + 1] == "copy"


def test_a_single_clip_that_needs_work_gets_a_graph_not_a_dangling_map():
    args = ENCODER._args([CLIP], OUT, EncodeProfile(name="1080p"), [HDR_LANDSCAPE])
    graph = _graph(args)

    assert (
        graph
        == "[0:v:0]"
        + ",".join(
            [
                "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709:t=bt709:m=bt709:r=tv",
                "scale=1920:1080:force_original_aspect_ratio=decrease",
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                "setsar=1",
                "format=yuv420p",
            ]
        )
        + "[v0]"
    )
    assert args[args.index("-map") + 1] == "[v0]"


# --------------------------------------------------------------------------
# Reporting — the job record keeps one line, so it has to be the right one
# --------------------------------------------------------------------------

CONCAT_FAILURE = "\n".join(
    [
        "[Parsed_concat_0 @ 0x55ef812c4640] Input link in0:v0 parameters "
        "(size 1080x1920, SAR 0:1) do not match the corresponding output link "
        "in0:v0 parameters (1920x1080, SAR 0:1)",
        "[Parsed_concat_0 @ 0x55ef812c4640] Failed to configure output pad on Parsed_concat_0",
        "Error reinitializing filters!",
        "Failed to inject frame into filter network: Invalid argument",
        "Error while processing the decoded data for stream #2:1",
    ]
)


def test_the_reported_line_is_the_diagnosis_not_the_last_thing_to_unwind():
    """Verbatim stderr from the production failure. The old code reported the
    final line — "stream #2:1" — which names neither the cause nor a clip."""
    reported = _one_line(CONCAT_FAILURE)

    assert "do not match" in reported
    assert "1080x1920" in reported
    assert "stream #2:1" not in reported


def test_the_heap_address_is_stripped_but_the_filter_name_survives():
    assert _one_line(CONCAT_FAILURE).startswith("[Parsed_concat_0] Input link")


def test_a_failure_with_nothing_but_cascade_still_reports_something():
    reported = _one_line("Conversion failed!\nError while processing the decoded data\n")

    assert reported == "Conversion failed!"


def test_empty_stderr_is_said_plainly():
    assert _one_line("") == "no output"
    assert _one_line(None) == "no output"


def test_the_reported_line_stays_within_the_record_budget():
    assert len(_one_line("x" * 500)) == 200
