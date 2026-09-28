from pathlib import Path

import pytest

from bdauthor.model import Chapter
from bdauthor.mux.base import MuxError, MuxRequest
from bdauthor.mux.tsmuxer import (
    Entry,
    Track,
    TsMuxer,
    _check_tracks,
    build_meta,
    parse_tracks,
    plan_tracks,
)
from tests.builders import audio, media_info, subtitle, video

DETECT_OUTPUT = """\
tsMuxeR version 2.7.0. github.com/justdan96/tsMuxer
Track ID:    1
Stream type: H.264
Stream ID:   V_MPEG4/ISO/AVC
Stream info: Profile: High@4.0  Resolution: 1920:1080p  Frame rate: 23.976
Stream lang: und

Track ID:    2
Stream type: AC3
Stream ID:   A_AC3
Stream info: Bitrate: 192Kbps Sample Rate: 48KHz Channels: 2
Stream lang: por

Track ID:    4608
Stream type: PGS
Stream ID:   S_HDMV/PGS
Stream info:
Stream lang:

Duration: 00:00:01.000

"""

SOURCE = Path("/media/movie.mkv")
VIDEO = Track(1, "V_MPEG4/ISO/AVC", "und")
AUDIO = Track(2, "A_AC3", "por")
SUBTITLE = Track(3, "S_HDMV/PGS", None)
ENTRIES = [Entry(SOURCE, VIDEO), Entry(SOURCE, AUDIO), Entry(SOURCE, SUBTITLE)]


def test_parse_tracks_reads_ids_stream_ids_and_languages():
    assert parse_tracks(DETECT_OUTPUT) == [
        Track(1, "V_MPEG4/ISO/AVC", "und"),
        Track(2, "A_AC3", "por"),
        Track(4608, "S_HDMV/PGS", None),
    ]


def test_parse_tracks_of_unrelated_output_is_empty():
    assert parse_tracks("") == []
    assert parse_tracks("tsMuxeR version 2.7.0. github.com/justdan96/tsMuxer\n") == []


def test_track_kind():
    assert [t.kind for t in (VIDEO, AUDIO, SUBTITLE)] == ["video", "audio", "subtitle"]


def test_build_meta_lists_every_track_with_chapters():
    chapters = (
        Chapter(0.0, 10.0, "a"),
        Chapter(600.5, 700.0, "b"),
        Chapter(3661.001, 4000.0, None),
    )
    meta = build_meta(ENTRIES, chapters)
    assert meta.splitlines() == [
        "MUXOPT --no-pcr-on-video-pid --new-audio-pes --blu-ray --vbr "
        "--custom-chapters=00:00:00.000;00:10:00.500;01:01:01.001",
        'V_MPEG4/ISO/AVC, "/media/movie.mkv", insertSEI, contSPS, track=1, lang=und',
        'A_AC3, "/media/movie.mkv", track=2, lang=por',
        'S_HDMV/PGS, "/media/movie.mkv", track=3',
    ]


def test_build_meta_adds_a_chapter_at_zero_when_the_first_one_starts_later():
    meta = build_meta([Entry(SOURCE, VIDEO)], (Chapter(5.0, 9.0, None),))
    assert "--custom-chapters=00:00:00.000;00:00:05.000" in meta


@pytest.mark.parametrize("chapters", [(), (Chapter(0.0, 100.0, "only"),)])
def test_build_meta_omits_chapters_when_there_is_nothing_to_split(chapters):
    assert "--custom-chapters" not in build_meta([Entry(SOURCE, VIDEO)], chapters)


def test_build_meta_rejects_double_quotes_in_the_path():
    with pytest.raises(MuxError):
        build_meta([Entry(Path('/media/my "movie".mkv'), VIDEO)])


def test_check_tracks_accepts_matching_counts():
    _check_tracks([VIDEO, AUDIO, SUBTITLE], media_info(subtitles=(subtitle(),)))


@pytest.mark.parametrize(
    "info",
    [
        media_info(videos=(video(), video(index=5))),  # probe sees a second video stream
        media_info(subtitles=(subtitle(),)),  # probe sees a subtitle tsMuxeR did not list
    ],
)
def test_check_tracks_refuses_to_guess_on_mismatch(info):
    with pytest.raises(MuxError, match="refusing to guess"):
        _check_tracks([VIDEO, AUDIO], info)


# --- plan_tracks: which file/track each meta line uses ---------------------

AAC = Track(2, "A_AAC", "por")
DTS = Track(3, "A_DTS", "eng")
REPLACEMENT = (Path("/tmp/audio-1.mkv"), Track(1, "A_AC3", "por"))
AAC_INFO = media_info(audios=(audio(index=1, codec="aac"),))


def test_plan_tracks_without_replacements_keeps_detection_order():
    info = media_info(audios=(audio(),), subtitles=(subtitle(),))
    source = info.path.resolve()
    assert plan_tracks([VIDEO, AUDIO, SUBTITLE], info, {}) == [
        Entry(source, VIDEO),
        Entry(source, AUDIO),
        Entry(source, SUBTITLE),
    ]


def test_plan_tracks_swaps_a_replaced_audio_track_in_place():
    source = AAC_INFO.path.resolve()
    entries = plan_tracks([VIDEO, AAC], AAC_INFO, {1: REPLACEMENT})
    assert entries == [Entry(source, VIDEO), Entry(*REPLACEMENT)]


def test_plan_tracks_keeps_the_audio_order_of_the_probe():
    info = media_info(audios=(audio(index=1), audio(index=2, codec="aac")))
    source = info.path.resolve()
    entries = plan_tracks([VIDEO, AUDIO, Track(3, "A_AAC", "eng")], info, {2: REPLACEMENT})
    assert entries == [Entry(source, VIDEO), Entry(source, AUDIO), Entry(*REPLACEMENT)]


def test_plan_tracks_replaced_stream_that_tsmuxer_cannot_read_is_not_detected():
    info = media_info(audios=(audio(index=1, codec="flac"),))
    entries = plan_tracks([VIDEO], info, {1: REPLACEMENT})
    assert entries == [Entry(info.path.resolve(), VIDEO), Entry(*REPLACEMENT)]


def test_plan_tracks_refuses_when_the_detected_track_is_another_codec():
    with pytest.raises(MuxError, match="refusing to guess"):
        plan_tracks([VIDEO, AUDIO], AAC_INFO, {1: REPLACEMENT})  # detected A_AC3, probe says aac


def test_plan_tracks_refuses_unaccounted_detected_audio():
    with pytest.raises(MuxError, match="refusing to guess"):
        plan_tracks([VIDEO, AUDIO, DTS], media_info(audios=(audio(),)), {})


def test_plan_tracks_refuses_when_an_audio_track_is_missing():
    with pytest.raises(MuxError, match="refusing to guess"):
        plan_tracks([VIDEO], media_info(audios=(audio(),)), {})


# --- audio delay -----------------------------------------------------------


@pytest.mark.parametrize(
    ("video_start", "audio_start", "shift_ms"),
    [
        (0.0, 0.0, 0),
        (0.0, 0.5, 500),
        (0.0, 0.0004, 0),  # below 1 ms: nothing to correct
        (0.042, 0.0, -42),
        (1.0, 1.15, 150),  # only the difference matters, not the absolute start
        (None, 0.5, 0),  # unknown start times: leave alone
        (0.0, None, 0),
    ],
)
def test_plan_tracks_shifts_audio_by_its_delay_relative_to_the_video(video_start, audio_start, shift_ms):
    info = media_info(
        videos=(video(start_time=video_start),), audios=(audio(index=1, start_time=audio_start),)
    )
    entries = plan_tracks([VIDEO, AUDIO], info, {})
    assert entries[0].timeshift_ms == 0  # video is the reference
    assert entries[1].timeshift_ms == shift_ms


def test_plan_tracks_applies_the_source_delay_to_the_replacement_too():
    info = media_info(
        videos=(video(start_time=0.0),), audios=(audio(index=1, codec="aac", start_time=0.5),)
    )
    entries = plan_tracks([VIDEO, AAC], info, {1: REPLACEMENT})
    assert entries[1] == Entry(*REPLACEMENT, timeshift_ms=500)


def test_build_meta_writes_timeshift_only_when_there_is_one():
    meta = build_meta(
        [
            Entry(SOURCE, VIDEO),
            Entry(SOURCE, AUDIO, timeshift_ms=-42),
            Entry(SOURCE, Track(4, "A_DTS", None), timeshift_ms=0),
        ]
    )
    assert meta.splitlines()[2:] == [
        'A_AC3, "/media/movie.mkv", track=2, lang=por, timeshift=-42ms',
        'A_DTS, "/media/movie.mkv", track=4',
    ]


def fake_tsmuxer(tmp_path: Path, *, mux_output: str, exit_code: int = 0) -> Path:
    """A shell script that mimics tsMuxeR's detection and muxing output."""
    script = tmp_path / "tsMuxeR"
    script.write_text(
        f"""#!/bin/sh
if [ "$#" -eq 1 ]; then
cat <<'EOF'
{DETECT_OUTPUT}
EOF
else
cat <<'EOF'
{mux_output}
EOF
exit {exit_code}
fi
"""
    )
    script.chmod(0o755)
    return script


def request(tmp_path: Path):
    info = media_info(subtitles=(subtitle(),))
    return MuxRequest(info=info, output_dir=tmp_path / "out")


def test_mux_reports_progress_from_tsmuxer_output(tmp_path):
    muxer = TsMuxer(
        fake_tsmuxer(tmp_path, mux_output="5.5% complete\nnoise\n50.0% complete\n100.0% complete\nMux successful complete")
    )
    seen: list[float] = []
    muxer.mux(request(tmp_path), seen.append)
    assert seen == [5.5, 50.0, 100.0]


def test_mux_failure_includes_the_tail_of_the_output(tmp_path):
    muxer = TsMuxer(fake_tsmuxer(tmp_path, mux_output="Decoding\nERROR: disk full", exit_code=1))
    with pytest.raises(MuxError, match="ERROR: disk full"):
        muxer.mux(request(tmp_path))


def test_mux_without_success_message_is_a_failure_even_with_exit_code_0(tmp_path):
    muxer = TsMuxer(fake_tsmuxer(tmp_path, mux_output="50.0% complete"))
    with pytest.raises(MuxError):
        muxer.mux(request(tmp_path))


def test_mux_reports_a_missing_executable(tmp_path):
    with pytest.raises(MuxError, match="could not run tsMuxeR"):
        TsMuxer(tmp_path / "nope").mux(request(tmp_path))
