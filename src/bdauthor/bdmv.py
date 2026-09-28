"""Facts about a BDMV directory shared by build and iso. No I/O beyond reading the tree."""

from pathlib import Path

REQUIRED_FILES = (
    "index.bdmv",
    "MovieObject.bdmv",
    "PLAYLIST/00000.mpls",
    "CLIPINF/00000.clpi",
    "STREAM/00000.m2ts",
)
DISC_DIRECTORIES = ("BDMV", "CERTIFICATE")  # top-level directories that belong on the disc


def missing_files(bdmv_dir: Path) -> list[str]:
    return [name for name in REQUIRED_FILES if not (bdmv_dir / name).is_file()]


def directory_size(path: Path) -> int:
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())
