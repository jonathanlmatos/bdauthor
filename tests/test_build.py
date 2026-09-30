import shutil
import subprocess

import pytest

from bdauthor.build import REQUIRED_FILES, BuildError, CheckFailed, auto_chapters, build_disc
from bdauthor.model import Media
from bdauthor.mux.base import MuxError
from bdauthor.mux.tsmuxer import TsMuxer
from bdauthor.probe import ProbeError


class FakeMuxer:
    """Writes the BDMV files without invoking tsMuxeR."""

    def __init__(self, files=REQUIRED_FILES, m2ts_size=0, error=None):
        self.files = files
        self.m2ts_size = m2ts_size
        self.error = error
        self.calls = 0

    def mux(self, request, on_progress=None):
        self.calls += 1
        self.last_request = request
        if self.error:
            raise self.error
        for name in self.files:
            path = request.output_dir / "BDMV" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        if self.m2ts_size:
            with open(request.output_dir / "BDMV" / "STREAM" / "00000.m2ts", "wb") as f:
                f.truncate(self.m2ts_size)


def test_incompatible_source_raises_check_failed_and_creates_nothing(make_mkv, tmp_path):
    muxer = FakeMuxer()
    out = tmp_path / "out"
    with pytest.raises(CheckFailed) as excinfo:
        build_disc(make_mkv("aac.mkv", acodec="aac"), out, Media.BD25, muxer)
    assert not excinfo.value.report.passed
    assert not out.exists()
    assert muxer.calls == 0


def test_existing_bdmv_is_not_overwritten_without_force(good_mkv, tmp_path):
    (tmp_path / "BDMV").mkdir()
    muxer = FakeMuxer()
    with pytest.raises(BuildError, match="--force"):
        build_disc(good_mkv, tmp_path, Media.BD25, muxer)
    assert muxer.calls == 0


def test_force_removes_only_generated_directories(good_mkv, tmp_path):
    (tmp_path / "BDMV").mkdir()
    (tmp_path / "BDMV" / "old.txt").write_text("old")
    (tmp_path / "CERTIFICATE").mkdir()
    (tmp_path / "notes.txt").write_text("keep me")

    with pytest.raises(MuxError):
        build_disc(good_mkv, tmp_path, Media.BD25, FakeMuxer(error=MuxError("boom")), force=True)

    assert not (tmp_path / "BDMV").exists()
    assert not (tmp_path / "CERTIFICATE").exists()
    assert (tmp_path / "notes.txt").read_text() == "keep me"


def test_output_path_that_is_a_file_is_rejected(good_mkv, tmp_path):
    target = tmp_path / "out"
    target.write_text("a file")
    with pytest.raises(BuildError, match="not a directory"):
        build_disc(good_mkv, target, Media.BD25, FakeMuxer())


def test_missing_bdmv_files_are_reported(good_mkv, tmp_path):
    with pytest.raises(BuildError, match="did not produce"):
        build_disc(good_mkv, tmp_path, Media.BD25, FakeMuxer(files=("index.bdmv",)))


def test_actual_size_over_media_capacity_is_an_error(good_mkv, tmp_path):
    with pytest.raises(BuildError, match="exceeds DVD-5 capacity"):
        build_disc(good_mkv, tmp_path, Media.DVD5, FakeMuxer(m2ts_size=5 * 2**30))


# --- with the real tsMuxeR -------------------------------------------------


def test_real_mux_produces_a_verified_bdmv(good_mkv, tmp_path, tsmuxer):
    progress: list[float] = []
    result = build_disc(
        good_mkv, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer), on_progress=progress.append
    )
    for name in REQUIRED_FILES:
        assert (result.bdmv_dir / name).is_file(), name
    assert result.size_bytes > 0
    assert result.report.passed
    assert progress and progress[-1] == 100.0


def test_real_mux_handles_accents_in_paths(good_mkv, tmp_path, tsmuxer):
    source = tmp_path / "Ação e Emoção.mkv"
    shutil.copy(good_mkv, source)
    result = build_disc(source, tmp_path / "saída", Media.BD25, TsMuxer(tsmuxer))
    assert (result.bdmv_dir / "STREAM" / "00000.m2ts").is_file()


def test_real_mux_carries_chapters_and_audio_language(make_mkv, tmp_path, tsmuxer):
    source = make_mkv(
        "chapters.mkv",
        audio_language="por",
        chapters=[(0.0, 0.5, "Intro"), (0.5, 1.0, "Fim")],
    )
    result = build_disc(source, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer))

    playlist = result.bdmv_dir / "PLAYLIST" / "00000.mpls"
    output = subprocess.run([str(tsmuxer), str(playlist)], capture_output=True, text=True).stdout
    assert "Marks: 00:00:00.000 00:00:00.500" in output
    assert "Stream lang: por" in output


# --- auto chapters ----------------------------------------------------------


def test_auto_chapters_marks_every_interval_starting_at_zero():
    chapters = auto_chapters(duration=650.0, interval_minutes=5)
    assert [c.start for c in chapters] == [0.0, 300.0, 600.0]
    assert [c.end for c in chapters] == [300.0, 600.0, 650.0]


def test_auto_chapters_is_empty_when_too_short_for_a_second_mark():
    assert auto_chapters(duration=250.0, interval_minutes=5) == ()
    assert auto_chapters(duration=300.0, interval_minutes=5) == ()


def test_build_disc_adds_auto_chapters_when_the_source_has_none(make_mkv, tmp_path):
    source = make_mkv("no_chapters.mkv", seconds=2)
    muxer = FakeMuxer()
    # FakeMuxer's stub .m2ts fails the post-mux probe done for verification; that happens after
    # mux(), which is all this test needs -- what the muxer actually received.
    with pytest.raises(ProbeError):
        build_disc(source, tmp_path / "disc", Media.BD25, muxer, auto_chapters_minutes=0.01)  # 0.6s
    starts = [c.start for c in muxer.last_request.info.chapters]
    assert starts[0] == 0.0
    assert len(starts) > 1


def test_build_disc_keeps_the_source_chapters_when_it_already_has_some(make_mkv, tmp_path):
    source = make_mkv("chapters.mkv", seconds=2, chapters=[(0.0, 1.0, "A"), (1.0, 2.0, "B")])
    muxer = FakeMuxer()
    with pytest.raises(ProbeError):
        build_disc(source, tmp_path / "disc", Media.BD25, muxer, auto_chapters_minutes=0.01)
    assert [c.start for c in muxer.last_request.info.chapters] == [0.0, 1.0]


# --- audio transcoding with the real tsMuxeR -------------------------------


def muxed_streams(bdmv_dir):
    import av

    with av.open(str(bdmv_dir / "STREAM" / "00000.m2ts")) as container:
        return container.streams.video[0], container.streams.audio


def test_incompatible_audio_is_rejected_unless_transcoding_is_requested(make_mkv, tmp_path):
    source = make_mkv("aac.mkv", acodec="aac")
    with pytest.raises(CheckFailed):
        build_disc(source, tmp_path / "a", Media.BD25, FakeMuxer())


def test_real_mux_reencodes_aac_to_ac3_and_leaves_the_video_alone(make_mkv, tmp_path, tsmuxer):
    source = make_mkv("aac.mkv", acodec="aac", audio_language="por", seconds=2)
    transcoded: list[float] = []

    result = build_disc(
        source,
        tmp_path / "disc",
        Media.BD25,
        TsMuxer(tsmuxer),
        transcode_audio=True,
        on_transcode_progress=transcoded.append,
    )

    _, audio_streams = muxed_streams(result.bdmv_dir)
    assert [s.codec_context.name for s in audio_streams] == ["ac3"]
    playlist = result.bdmv_dir / "PLAYLIST" / "00000.mpls"
    detected = subprocess.run([str(tsmuxer), str(playlist)], capture_output=True, text=True).stdout
    assert "Stream lang: por" in detected
    assert transcoded and transcoded[-1] == 100.0
    assert any(f.rule == "audio" and "re-encoded" in f.message for f in result.report.warnings)


def audio_delay(bdmv_dir) -> float:
    video, (audio_stream,) = muxed_streams(bdmv_dir)
    return float((audio_stream.start_time - video.start_time) * audio_stream.time_base)


@pytest.mark.parametrize(
    ("acodec", "transcode"), [("ac3", False), ("aac", True)], ids=["remux", "transcoded"]
)
def test_real_mux_keeps_the_audio_delay_of_the_source(make_mkv, tmp_path, tsmuxer, acodec, transcode):
    source = make_mkv("late.mkv", acodec=acodec, audio_offset=0.5, seconds=2)
    result = build_disc(
        source, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer), transcode_audio=transcode
    )
    assert audio_delay(result.bdmv_dir) == pytest.approx(0.5, abs=0.06)


def test_real_mux_of_in_sync_audio_stays_in_sync(good_mkv, tmp_path, tsmuxer):
    result = build_disc(good_mkv, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer))
    assert audio_delay(result.bdmv_dir) == pytest.approx(0.0, abs=0.06)


def test_real_mux_downmixes_71_to_51(make_mkv, tmp_path, tsmuxer):
    source = make_mkv("flac71.mkv", acodec="flac", audio_layout="7.1")
    result = build_disc(
        source, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer), transcode_audio=True
    )
    _, (audio_stream,) = muxed_streams(result.bdmv_dir)
    assert (audio_stream.codec_context.name, audio_stream.codec_context.channels) == ("ac3", 6)


def test_real_mux_transcoding_does_nothing_when_the_audio_is_already_fine(good_mkv, tmp_path, tsmuxer):
    result = build_disc(
        good_mkv, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer), transcode_audio=True
    )
    assert not result.report.warnings


def test_real_mux_creates_certificate_and_the_empty_bdmv_directories(good_mkv, tmp_path, tsmuxer):
    result = build_disc(good_mkv, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer))
    disc = result.bdmv_dir.parent
    assert (disc / "CERTIFICATE" / "BACKUP").is_dir()
    for name in ("AUXDATA", "BDJO", "JAR", "META"):
        assert (result.bdmv_dir / name).is_dir(), name
