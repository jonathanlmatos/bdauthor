import io

import pycdlib
import pytest

from bdauthor.iso import (
    IsoError,
    build_iso,
    find_disc_root,
    iso_dir_name,
    iso_file_name,
    volume_label,
)
from bdauthor.model import Media


def udf_files(iso_path) -> dict[str, bytes]:
    """Every file in the image's UDF tree, read back."""
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    files = {}
    try:
        for dirpath, _dirs, names in iso.walk(udf_path="/"):
            for name in names:
                full = f"{dirpath.rstrip('/')}/{name}"
                buffer = io.BytesIO()
                iso.get_file_from_iso_fp(buffer, udf_path=full)
                files[full] = buffer.getvalue()
    finally:
        iso.close()
    return files


def test_round_trip_contains_the_disc_files_byte_for_byte(make_disc, tmp_path):
    root = make_disc()
    result = build_iso(root, tmp_path / "disc.iso", Media.BD25)

    files = udf_files(result.path)
    assert set(files) == {
        "/BDMV/index.bdmv",
        "/BDMV/MovieObject.bdmv",
        "/BDMV/PLAYLIST/00000.mpls",
        "/BDMV/CLIPINF/00000.clpi",
        "/BDMV/STREAM/00000.m2ts",
        "/BDMV/BACKUP/index.bdmv",
        "/BDMV/BACKUP/PLAYLIST/00000.mpls",
        "/CERTIFICATE/id.bdmv",
    }
    for path, data in files.items():
        assert data == (root / path.lstrip("/")).read_bytes(), path
    assert result.file_count == 8
    assert result.size_bytes == result.path.stat().st_size
    assert result.size_bytes % 2048 == 0


def test_files_outside_the_disc_directories_are_left_out(make_disc, tmp_path):
    result = build_iso(make_disc(), tmp_path / "disc.iso", Media.BD25)
    assert not any(p.startswith(("/notes", "/extra")) for p in udf_files(result.path))


def test_iso_is_a_udf_image_readable_by_pycdlib(make_disc, tmp_path):
    result = build_iso(make_disc(), tmp_path / "disc.iso", Media.BD25)
    iso = pycdlib.PyCdlib()
    iso.open(str(result.path))
    try:
        assert iso.has_udf()
    finally:
        iso.close()


def test_accepts_the_bdmv_directory_itself_as_the_source(make_disc, tmp_path):
    root = make_disc()
    assert find_disc_root(root / "BDMV") == root
    result = build_iso(root / "BDMV", tmp_path / "disc.iso", Media.BD25)
    assert "/BDMV/index.bdmv" in udf_files(result.path)


def test_source_without_bdmv_is_rejected(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(IsoError, match="does not contain a BDMV"):
        build_iso(tmp_path / "empty", tmp_path / "disc.iso", Media.BD25)


def test_incomplete_disc_is_rejected(make_disc, tmp_path):
    root = make_disc()
    (root / "BDMV" / "STREAM" / "00000.m2ts").unlink()
    with pytest.raises(IsoError, match="STREAM/00000.m2ts"):
        build_iso(root, tmp_path / "disc.iso", Media.BD25)
    assert not (tmp_path / "disc.iso").exists()


def test_existing_iso_is_kept_unless_forced(make_disc, tmp_path):
    root = make_disc()
    dest = tmp_path / "disc.iso"
    dest.write_bytes(b"old")
    with pytest.raises(IsoError, match="--force"):
        build_iso(root, dest, Media.BD25)
    assert dest.read_bytes() == b"old"

    build_iso(root, dest, Media.BD25, force=True)
    assert dest.stat().st_size > 3


def test_destination_that_is_a_directory_is_rejected(make_disc, tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(IsoError, match="is a directory"):
        build_iso(make_disc(), tmp_path / "out", Media.BD25, force=True)


def test_content_over_the_media_capacity_is_rejected_before_writing(make_disc, tmp_path):
    root = make_disc()
    with open(root / "BDMV" / "STREAM" / "00000.m2ts", "wb") as f:
        f.truncate(5 * 2**30)  # sparse: no real space used
    with pytest.raises(IsoError, match="exceeds DVD-5 capacity"):
        build_iso(root, tmp_path / "disc.iso", Media.DVD5)
    assert not (tmp_path / "disc.iso").exists()
    assert not list(tmp_path.glob("*.part"))


def test_failed_write_leaves_no_partial_file(make_disc, tmp_path, monkeypatch):
    def broken_write(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pycdlib.PyCdlib, "write", broken_write)
    with pytest.raises(IsoError, match="disk full"):
        build_iso(make_disc(), tmp_path / "disc.iso", Media.BD25)
    assert not (tmp_path / "disc.iso").exists()
    assert not list(tmp_path.glob("*.part"))


def test_progress_is_reported_up_to_100(make_disc, tmp_path):
    seen: list[float] = []
    build_iso(make_disc(), tmp_path / "disc.iso", Media.BD25, on_progress=seen.append)
    assert seen and seen[-1] == 100.0
    assert seen == sorted(seen)


def test_verification_catches_a_wrong_image(make_disc, tmp_path, monkeypatch):
    root = make_disc()

    def write_only_part(self, filename, **kwargs):
        with open(filename, "wb") as f:
            f.write(b"\0" * 4096)

    monkeypatch.setattr(pycdlib.PyCdlib, "write", write_only_part)
    with pytest.raises(IsoError):
        build_iso(root, tmp_path / "disc.iso", Media.BD25)


# --- names -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("index.bdmv", "INDEX.BDMV;1"),
        ("MovieObject.bdmv", "MOVIEOBJECT.BDMV;1"),
        ("00000.m2ts", "00000.M2TS;1"),
        ("id.bdmv", "ID.BDMV;1"),
        ("no-extension", "NO_EXTENSION.;1"),
        ("my file.v2.txt", "MY_FILE_V2.TXT;1"),
    ],
)
def test_iso_file_names(name, expected):
    assert iso_file_name(name) == expected


def test_iso_names_that_are_too_long_are_rejected():
    with pytest.raises(IsoError, match="too long"):
        iso_file_name("x" * 40 + ".bdmv")
    with pytest.raises(IsoError, match="too long"):
        iso_dir_name("d" * 40)


@pytest.mark.parametrize(
    ("directory", "label", "expected"),
    [
        ("MY DISC", None, "MY_DISC"),
        ("SAMPLE", None, "SAMPLE"),
        ("Ação e Emoção", None, "A__O_E_EMO__O"),
        ("x", "Custom label!", "CUSTOM_LABEL"),
        ("x", "a" * 50, "A" * 32),
        ("---", None, "BDAUTHOR"),
    ],
)
def test_volume_label(tmp_path, directory, label, expected):
    root = tmp_path / directory
    root.mkdir()
    assert volume_label(root, label) == expected


# --- with the real tsMuxeR -------------------------------------------------


def test_iso_of_a_real_build_matches_the_directory(good_mkv, tmp_path, tsmuxer):
    from bdauthor.build import build_disc
    from bdauthor.mux.tsmuxer import TsMuxer

    build_disc(good_mkv, tmp_path / "disc", Media.BD25, TsMuxer(tsmuxer))
    result = build_iso(tmp_path / "disc", tmp_path / "disc.iso", Media.BD25)

    files = udf_files(result.path)
    m2ts = (tmp_path / "disc" / "BDMV" / "STREAM" / "00000.m2ts").read_bytes()
    assert files["/BDMV/STREAM/00000.m2ts"] == m2ts
    assert "/BDMV/index.bdmv" in files
