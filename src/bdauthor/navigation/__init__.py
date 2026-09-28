"""Writers for the BDMV navigation files (index.bdmv, MovieObject.bdmv)."""

from pathlib import Path

from bdauthor.navigation.index import IndexTable
from bdauthor.navigation.movie_object import MovieObject, pack_movie_objects


def write_navigation(bdmv_dir: Path, index: IndexTable, objects: list[MovieObject]) -> None:
    """Write index.bdmv and MovieObject.bdmv into `bdmv_dir`, plus the BACKUP/ copies."""
    files = {"index.bdmv": index.pack(), "MovieObject.bdmv": pack_movie_objects(objects)}
    backup = bdmv_dir / "BACKUP"
    backup.mkdir(exist_ok=True)
    for name, data in files.items():
        (bdmv_dir / name).write_bytes(data)
        (backup / name).write_bytes(data)
