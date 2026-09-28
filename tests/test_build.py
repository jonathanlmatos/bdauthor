import shutil
import subprocess

import pytest

from bdauthor.build import REQUIRED_FILES, BuildError, CheckFailed, build_disc
from bdauthor.model import Media
from bdauthor.mux.base import MuxError
from bdauthor.mux.tsmuxer import TsMuxer


class FakeMuxer:
    """Writes the BDMV files without invoking tsMuxeR."""

    def __init__(self, files=REQUIRED_FILES, m2ts_size=0, error=None):
        self.files = files
        self.m2ts_size = m2ts_size
        self.error = error
        self.calls = 0

    def mux(self, request, on_progress=None):
        self.calls += 1
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
