from fractions import Fraction

import pytest

from bdauthor.model import Media, Report, Severity
from bdauthor.probe import probe
from bdauthor.validate import validate
from tests.builders import audio, media_info, subtitle, video

GIB = 2**30


def run(info=None, media=Media.BD25) -> Report:
    return validate(info or media_info(), media)


def error_messages(report: Report, rule: str) -> list[str]:
    return [f.message for f in report.findings if f.severity is Severity.ERROR and f.rule == rule]


def test_compliant_file_passes_without_warnings():
    report = run()
    assert report.passed
    assert not report.warnings
    assert {f.rule for f in report.findings} == {"container", "video", "audio", "size"}


# --- video -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (dict(codec="hevc"), "codec hevc is not allowed"),
        (dict(profile="High 10"), "profile High 10 is not allowed"),
        (dict(level=51), "level 5.1 exceeds"),
        (dict(bit_depth=10), "10-bit"),
        (dict(chroma="4:2:2"), "4:2:2"),
        (dict(width=1920, height=800), "cropped"),
        (dict(width=3840, height=2160), "UHD"),
        (dict(fps=Fraction(25)), "25p at 1920x1080"),
        (dict(fps=Fraction(30000, 1001)), "29.97p at 1920x1080"),
        (dict(fps=Fraction(60000, 1001)), "59.94p at 1920x1080"),
        (dict(interlaced=True, fps=Fraction(24)), "24i at 1920x1080"),
        (dict(width=1280, height=720, fps=Fraction(30000, 1001)), "29.97p at 1280x720"),
        (dict(width=1280, height=720, interlaced=True, fps=Fraction(25)), "interlaced"),
        (dict(ref_frames=5), "5 reference frames exceed the limit of 4"),
        (dict(width=1280, height=720, ref_frames=10), "10 reference frames exceed the limit of 9"),
    ],
)
def test_video_rejects(overrides, expected):
    report = run(media_info(videos=(video(**overrides),)))
    assert not report.passed
    assert any(expected in message for message in error_messages(report, "video"))


@pytest.mark.parametrize(
    "overrides",
    [
        dict(fps=Fraction(24)),
        dict(fps=Fraction(23976, 1000)),
        dict(interlaced=True, fps=Fraction(25)),
        dict(interlaced=True, fps=Fraction(30000, 1001)),
        dict(width=1280, height=720, fps=Fraction(50)),
        dict(width=1280, height=720, fps=Fraction(60000, 1001)),
        dict(width=1280, height=720, ref_frames=9),
        dict(profile="Main"),
        dict(level=40),
        dict(ref_frames=1),
    ],
)
def test_video_accepts(overrides):
    assert run(media_info(videos=(video(**overrides),))).passed


def test_video_unknown_fields_are_warnings_not_errors():
    report = run(media_info(videos=(video(profile=None, level=None, ref_frames=None),)))
    assert report.passed
    assert len(report.warnings) == 3


def test_video_bitrate_over_40_mbps_is_an_error():
    report = run(media_info(videos=(video(bitrate=41_000_000),)))
    assert not report.passed
    assert "bitrate 41.0 Mbps" in error_messages(report, "video")[0]


# --- audio -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (dict(codec="aac"), "codec aac is not allowed"),
        (dict(codec="flac"), "codec flac is not allowed"),
        (dict(codec="opus"), "codec opus is not allowed"),
        (dict(sample_rate=44100), "44.1 kHz"),
        (dict(channels=8), "8 channels exceed"),
        (dict(codec="dts", sample_rate=96000), "96 kHz"),
        (dict(codec="pcm_s24le", sample_rate=44100), "44.1 kHz"),
    ],
)
def test_audio_rejects(overrides, expected):
    report = run(media_info(audios=(audio(**overrides),)))
    assert not report.passed
    assert any(expected in message for message in error_messages(report, "audio"))


@pytest.mark.parametrize(
    "overrides",
    [
        dict(codec="ac3", channels=6),
        dict(codec="eac3", channels=8),
        dict(codec="dts", channels=6),
        dict(codec="dts", profile="DTS-HD MA", sample_rate=96000, channels=8),
        dict(codec="truehd", channels=8),
        dict(codec="pcm_s24le", sample_rate=96000, channels=2),
    ],
)
def test_audio_accepts(overrides):
    assert run(media_info(audios=(audio(**overrides),))).passed


def test_each_incompatible_extra_audio_track_is_reported():
    info = media_info(audios=(audio(), audio(index=2, codec="aac"), audio(index=3, codec="opus")))
    report = run(info)
    assert [f.stream_index for f in report.errors] == [2, 3]


# --- subtitles -------------------------------------------------------------


def test_pgs_subtitles_are_accepted():
    assert run(media_info(subtitles=(subtitle(),))).passed


@pytest.mark.parametrize("codec", ["subrip", "ass", "webvtt"])
def test_text_subtitles_are_rejected(codec):
    report = run(media_info(subtitles=(subtitle(codec=codec),)))
    assert not report.passed
    assert "PGS only" in error_messages(report, "subtitles")[0]


# --- container -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(container="mov,mp4,m4a,3gp,3g2,mj2"), "Matroska"),
        (dict(videos=()), "no video stream"),
        (dict(videos=(video(), video(index=3))), "2 video streams"),
        (dict(audios=()), "no audio stream"),
    ],
)
def test_container_rules(kwargs, expected):
    report = run(media_info(**kwargs))
    assert any(expected in message for message in error_messages(report, "container"))


# --- size / bitrate --------------------------------------------------------


def test_size_over_media_capacity_reports_the_overshoot():
    report = run(media_info(size_bytes=12 * GIB), Media.DVD5)
    (message,) = error_messages(report, "size")
    assert "exceeds DVD-5 capacity" in message
    assert "over by" in message


@pytest.mark.parametrize(
    ("size_gib", "media", "fits"),
    [
        (4.0, Media.DVD5, True),
        (4.2, Media.DVD5, False),
        (7.0, Media.DVD9, True),
        (8.0, Media.DVD9, False),
        (22.0, Media.BD25, True),
        (23.0, Media.BD25, False),
        (44.0, Media.BD50, True),
        (45.0, Media.BD50, False),
    ],
)
def test_size_fit_boundaries_include_muxing_overhead(size_gib, media, fits):
    report = run(media_info(size_bytes=int(size_gib * GIB), duration=20_000.0), media)
    assert (not error_messages(report, "size")) is fits


def test_average_bitrate_over_48_mbps_is_an_error():
    report = run(media_info(size_bytes=40 * GIB, duration=3600.0), Media.BD50)
    (message,) = error_messages(report, "size")
    assert "average bitrate" in message


def test_unknown_duration_skips_bitrate_checks():
    assert run(media_info(size_bytes=40 * GIB, duration=None), Media.BD50).passed


# --- real files ------------------------------------------------------------


def test_synthetic_compliant_file_passes(good_mkv):
    report = validate(probe(good_mkv), Media.BD25)
    assert report.passed, report.errors


@pytest.mark.parametrize(
    ("kwargs", "rule"),
    [
        (dict(width=1920, height=800), "video"),
        (dict(acodec="aac"), "audio"),
        (dict(width=320, height=240, pix_fmt="yuv420p10le"), "video"),
    ],
)
def test_synthetic_incompatible_files_fail(make_mkv, kwargs, rule):
    report = validate(probe(make_mkv("bad.mkv", **kwargs)), Media.BD25)
    assert not report.passed
    assert error_messages(report, rule)
