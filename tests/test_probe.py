from fractions import Fraction

import pytest

from bdauthor.probe import ProbeError, probe


def test_probe_compliant_file(good_mkv):
    info = probe(good_mkv)

    assert info.container == "matroska,webm"
    assert info.size_bytes == good_mkv.stat().st_size
    assert info.duration == pytest.approx(1.0, abs=0.1)
    assert info.subtitles == () and info.others == () and info.chapters == ()

    (video,) = info.video
    assert video.codec == "h264"
    assert video.profile == "High"
    assert (video.width, video.height) == (1920, 1080)
    assert video.fps == Fraction(24000, 1001)
    assert video.rate_label == "23.976p"
    assert (video.bit_depth, video.chroma) == (8, "4:2:0")
    assert video.interlaced is False
    assert video.level is not None and video.level <= 41
    assert video.ref_frames is not None

    (audio,) = info.audio
    assert (audio.codec, audio.channels, audio.sample_rate) == ("ac3", 2, 48000)


def test_probe_reports_stream_start_times(make_mkv):
    info = probe(make_mkv("late.mkv", acodec="ac3", audio_offset=0.5))
    assert info.video[0].start_time == pytest.approx(0.0, abs=0.01)
    assert info.audio[0].start_time == pytest.approx(0.5, abs=0.01)


def test_probe_reports_cropped_resolution(make_mkv):
    (video,) = probe(make_mkv("scope.mkv", width=1920, height=800)).video
    assert (video.width, video.height) == (1920, 800)


def test_probe_reports_non_bd_audio_codec(make_mkv):
    (audio,) = probe(make_mkv("aac.mkv", acodec="aac")).audio
    assert audio.codec == "aac"


def test_probe_audio_language_title_and_chapters(make_mkv):
    path = make_mkv(
        "meta.mkv",
        audio_language="por",
        audio_title="Portugues 2.0",
        chapters=[(0.0, 0.5, "Intro"), (0.5, 1.0, "Fim")],
    )
    info = probe(path)
    (audio,) = info.audio
    assert (audio.language, audio.title) == ("por", "Portugues 2.0")
    assert [(c.start, c.end, c.title) for c in info.chapters] == [
        (0.0, 0.5, "Intro"),
        (0.5, 1.0, "Fim"),
    ]


def test_probe_high_bit_depth_is_reported(make_mkv):
    path = make_mkv("hi10.mkv", width=320, height=240, pix_fmt="yuv420p10le")
    (video,) = probe(path).video
    assert video.bit_depth == 10


def test_probe_garbage_file_raises_probe_error(tmp_path):
    path = tmp_path / "notes.mkv"
    path.write_text("this is not a media file")
    with pytest.raises(ProbeError):
        probe(path)
