import av
import pytest

from bdauthor.model import Media, Severity
from bdauthor.probe import probe
from bdauthor.transcode import (
    AudioPlan,
    TranscodeError,
    converted_info,
    encode_ac3,
    plan_audio,
    validate_with_audio_transcode,
)
from bdauthor.validate import validate
from tests.builders import audio, media_info, video

GIB = 2**30


def aac(**overrides):
    defaults = dict(index=1, codec="aac", profile="HE-AAC", bitrate=77_000)
    return audio(**{**defaults, **overrides})


def test_plan_covers_only_rejected_audio_streams():
    info = media_info(audios=(audio(index=1), aac(index=2)))
    plans = plan_audio(info, validate(info, Media.BD25))
    assert [plan.source_index for plan in plans] == [2]


def test_plan_for_aac_51_uses_ac3_51_at_448k_and_keeps_the_language():
    info = media_info(audios=(aac(channels=6, language="por"),))
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    assert (plan.source_index, plan.layout, plan.channels, plan.bitrate) == (1, "5.1", 6, 448_000)
    assert plan.source_label == "aac (HE-AAC)"
    assert plan.language == "por"
    assert "will be re-encoded from aac (HE-AAC) 6ch to AC3 5.1 at 448 kb/s" in plan.note


@pytest.mark.parametrize(
    ("channels", "layout", "bitrate"),
    [(1, "mono", 128_000), (2, "stereo", 192_000), (4, "quad", 320_000), (5, "5.0", 384_000)],
)
def test_plan_layout_and_bitrate_follow_the_channel_count(channels, layout, bitrate):
    info = media_info(audios=(aac(channels=channels),))
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    assert (plan.layout, plan.bitrate) == (layout, bitrate)


def test_plan_downmixes_more_than_six_channels_to_51():
    info = media_info(audios=(aac(channels=8),))
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    assert (plan.channels, plan.layout, plan.source_channels) == (6, "5.1", 8)
    assert "downmixed from 8 channels" in plan.note


def test_plan_assumes_stereo_when_the_channel_count_is_unknown():
    info = media_info(audios=(aac(channels=None),))
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    assert plan.layout == "stereo"


def test_plan_is_empty_when_no_audio_is_rejected():
    info = media_info()
    assert plan_audio(info, validate(info, Media.BD25)) == []


def test_ac3_with_an_unsupported_sample_rate_is_also_re_encoded():
    info = media_info(audios=(audio(index=1, sample_rate=44100),))
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    assert plan.source_index == 1


def test_converted_info_looks_like_ac3_and_adjusts_the_size():
    info = media_info(audios=(aac(channels=6),), size_bytes=1_000_000_000, duration=1000.0)
    plan = plan_audio(info, validate(info, Media.BD25))
    converted = converted_info(info, plan)
    (stream,) = converted.audio
    assert (stream.codec, stream.channels, stream.sample_rate, stream.bitrate) == (
        "ac3",
        6,
        48000,
        448_000,
    )
    # (448k - 77k) * 1000 s / 8 = 46.375 MB more
    assert converted.size_bytes == 1_000_000_000 + 46_375_000


def test_validate_with_transcode_turns_the_audio_error_into_a_warning():
    info = media_info(audios=(aac(channels=6),))
    assert not validate(info, Media.BD25).passed

    report, plans = validate_with_audio_transcode(info, Media.BD25)
    assert report.passed
    assert len(plans) == 1
    (warning,) = report.warnings
    assert warning.rule == "audio" and "re-encoded" in warning.message
    audio_findings = [f for f in report.findings if f.rule == "audio"]
    assert [f.severity for f in audio_findings] == [Severity.WARNING, Severity.OK]


def test_validate_with_transcode_still_fails_for_problems_that_audio_cannot_fix():
    info = media_info(videos=(video(width=1920, height=800),), audios=(aac(channels=6),))
    report, plans = validate_with_audio_transcode(info, Media.BD25)
    assert not report.passed
    assert plans  # the conversion is still described
    assert any(f.rule == "video" and f.severity is Severity.ERROR for f in report.findings)


def test_validate_with_transcode_is_the_plain_report_when_nothing_needs_converting():
    info = media_info()
    report, plans = validate_with_audio_transcode(info, Media.BD25)
    assert plans == []
    assert report == validate(info, Media.BD25)


def test_the_bigger_ac3_track_can_push_the_disc_over_capacity():
    # 4.0 GiB fits DVD-5 as is, but 128 kb/s -> 448 kb/s over 2 h adds ~0.27 GiB.
    info = media_info(
        audios=(aac(channels=6, bitrate=128_000),), size_bytes=int(4.0 * GIB), duration=7200.0
    )
    assert not any(f.rule == "size" for f in validate(info, Media.DVD5).errors)
    report, _ = validate_with_audio_transcode(info, Media.DVD5)
    assert any(f.rule == "size" and "exceeds DVD-5" in f.message for f in report.errors)


# --- encoding real audio with PyAV -----------------------------------------


def plan_for(path):
    info = probe(path)
    (plan,) = plan_audio(info, validate(info, Media.BD25))
    return plan


def start_seconds(stream) -> float:
    return float(stream.start_time * stream.time_base)


def test_encode_ac3_writes_an_ac3_audio_only_mkv(make_mkv, tmp_path):
    source = make_mkv("aac.mkv", acodec="aac", audio_language="por", seconds=2)
    dest = tmp_path / "audio.mkv"
    progress: list[float] = []

    encode_ac3(source, plan_for(source), dest, progress.append)

    info = probe(dest)
    assert info.video == ()
    (stream,) = info.audio
    assert (stream.codec, stream.channels, stream.sample_rate) == ("ac3", 2, 48000)
    assert stream.language == "por"
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert progress == sorted(progress) and progress[-1] == 100.0


def test_encode_ac3_keeps_the_audio_start_offset_so_sync_survives(make_mkv, tmp_path):
    source = make_mkv("late.mkv", acodec="aac", audio_offset=0.5)
    dest = tmp_path / "audio.mkv"
    encode_ac3(source, plan_for(source), dest)

    with av.open(str(source)) as before, av.open(str(dest)) as after:
        assert start_seconds(before.streams.audio[0]) == pytest.approx(0.5, abs=0.05)
        assert start_seconds(after.streams.audio[0]) == pytest.approx(0.5, abs=0.05)


def test_encode_ac3_downmixes_71_to_51(make_mkv, tmp_path):
    source = make_mkv("flac71.mkv", acodec="flac", audio_layout="7.1")
    plan = plan_for(source)
    assert (plan.source_channels, plan.channels) == (8, 6)

    dest = tmp_path / "audio.mkv"
    encode_ac3(source, plan, dest)
    (stream,) = probe(dest).audio
    assert (stream.codec, stream.channels) == ("ac3", 6)


def test_encode_ac3_reports_a_bad_stream_index(good_mkv, tmp_path):
    plan = AudioPlan(9, "aac", 2, 2, "stereo", 192_000, None)
    with pytest.raises(TranscodeError, match="stream #9"):
        encode_ac3(good_mkv, plan, tmp_path / "audio.mkv")
