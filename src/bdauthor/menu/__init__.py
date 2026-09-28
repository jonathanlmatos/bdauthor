"""Adding a menu to a disc directory written by the muxer."""

import shutil
import tempfile
from pathlib import Path

from bdauthor.menu.background import MenuError, mux_black_clip
from bdauthor.menu.programs import menu_navigation
from bdauthor.model import VideoStream
from bdauthor.mux.base import Muxer
from bdauthor.navigation import write_navigation
from bdauthor.navigation.playlist import PlaylistError, retarget_playlist

__all__ = ["MenuError", "add_menu"]

MENU_CLIP = "00001"
_MOVIE_CLIP = "00000"


def _copy_menu_clip(menu_bdmv: Path, bdmv_dir: Path) -> None:
    """Copy the muxed black clip into the disc as clip MENU_CLIP (stream, clip info, playlist)."""
    for folder, suffix in (("STREAM", "m2ts"), ("CLIPINF", "clpi")):
        target = bdmv_dir / folder / f"{MENU_CLIP}.{suffix}"
        if target.exists():
            raise MenuError(f"{target} already exists")
        shutil.copyfile(menu_bdmv / folder / f"{_MOVIE_CLIP}.{suffix}", target)
    playlist = bdmv_dir / "PLAYLIST" / f"{MENU_CLIP}.mpls"
    if playlist.exists():
        raise MenuError(f"{playlist} already exists")
    try:
        playlist.write_bytes(
            retarget_playlist((menu_bdmv / "PLAYLIST" / f"{_MOVIE_CLIP}.mpls").read_bytes(), _MOVIE_CLIP, MENU_CLIP)
        )
    except PlaylistError as exc:
        raise MenuError(f"could not use the menu playlist: {exc}") from exc
    for folder, suffix in (("CLIPINF", "clpi"), ("PLAYLIST", "mpls")):  # BACKUP/ mirrors both
        shutil.copyfile(
            bdmv_dir / folder / f"{MENU_CLIP}.{suffix}", bdmv_dir / "BACKUP" / folder / f"{MENU_CLIP}.{suffix}"
        )


def add_menu(bdmv_dir: Path, video: VideoStream, muxer: Muxer) -> None:
    """Add a looping black menu to the disc in `bdmv_dir` and make it the first thing that plays.

    The movie (clip/playlist 00000, as written by the muxer) becomes title 1. `video` is the
    movie's video stream: the menu clip has its resolution and frame rate.
    """
    with tempfile.TemporaryDirectory(prefix="bdauthor-menu-") as tmp:
        _copy_menu_clip(mux_black_clip(muxer, video, Path(tmp)), bdmv_dir)
    index, objects = menu_navigation(movie_playlist=int(_MOVIE_CLIP), menu_playlist=int(MENU_CLIP))
    write_navigation(bdmv_dir, index, objects)
