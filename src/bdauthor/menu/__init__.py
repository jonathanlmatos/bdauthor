"""Adding a menu to a disc directory written by the muxer."""

import shutil
import tempfile
from pathlib import Path

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE, PGS_PID, PGS_STREAM_TYPE, BdavError, convert_graphics_to_interactive
from bdauthor.menu.background import MENU_SECONDS, MenuError, check_video_supported, mux_menu_clip
from bdauthor.menu.programs import menu_navigation
from bdauthor.menu.simple import MenuGraphicsError, frame_rate_code, simple_menu
from bdauthor.model import Chapter, VideoStream
from bdauthor.mux.base import Muxer
from bdauthor.navigation import write_navigation
from bdauthor.navigation.clip_info import ClipInfoError, program_map_pid, relabel_stream
from bdauthor.navigation.playlist import PlaylistError, relabel_graphics_as_interactive, retarget_playlist

__all__ = ["MenuError", "add_menu", "check_menu_supported"]

MENU_CLIP = "00001"
_MOVIE_CLIP = "00000"


def _copy_menu_clip(menu_bdmv: Path, bdmv_dir: Path) -> None:
    """Copy the muxed menu clip into the disc as clip MENU_CLIP (stream, clip info, playlist).

    On the way, the graphics stream tsMuxeR wrote as PGS becomes an interactive graphics stream.
    """
    targets = {
        folder: bdmv_dir / folder / f"{MENU_CLIP}.{suffix}"
        for folder, suffix in (("STREAM", "m2ts"), ("CLIPINF", "clpi"), ("PLAYLIST", "mpls"))
    }
    for target in targets.values():
        if target.exists():
            raise MenuError(f"{target} already exists")

    relabel = {"old_pid": PGS_PID, "new_pid": IG_PID, "old_coding": PGS_STREAM_TYPE, "new_coding": IG_STREAM_TYPE}
    try:
        clip_info = (menu_bdmv / "CLIPINF" / f"{_MOVIE_CLIP}.clpi").read_bytes()
        targets["CLIPINF"].write_bytes(relabel_stream(clip_info, **relabel))
        playlist = (menu_bdmv / "PLAYLIST" / f"{_MOVIE_CLIP}.mpls").read_bytes()
        playlist = relabel_graphics_as_interactive(retarget_playlist(playlist, _MOVIE_CLIP, MENU_CLIP), **relabel)
        targets["PLAYLIST"].write_bytes(playlist)
        shutil.copyfile(menu_bdmv / "STREAM" / f"{_MOVIE_CLIP}.m2ts", targets["STREAM"])
        convert_graphics_to_interactive(targets["STREAM"], program_map_pid(clip_info))
    except (ClipInfoError, PlaylistError, BdavError) as exc:
        raise MenuError(f"could not use the menu clip: {exc}") from exc

    for folder in ("CLIPINF", "PLAYLIST"):  # BACKUP/ mirrors both
        shutil.copyfile(targets[folder], bdmv_dir / "BACKUP" / folder / targets[folder].name)


def check_menu_supported(video: VideoStream) -> None:
    """Raise MenuError if a menu cannot be made for a movie with this video (cheap; do it before muxing)."""
    check_video_supported(video)
    try:
        frame_rate_code(video)
    except MenuGraphicsError as exc:
        raise MenuError(str(exc)) from exc


def add_menu(
    bdmv_dir: Path,
    video: VideoStream,
    muxer: Muxer,
    title: str | None = None,
    chapters: tuple[Chapter, ...] = (),
    menu_seconds: int | None = None,
) -> None:
    """Add a looping menu with a Play button to the disc in `bdmv_dir`; it plays first.

    The movie (clip/playlist 00000, as written by the muxer) becomes title 1. `video` is the
    movie's video stream: the menu clip has its resolution and frame rate. `title` is shown above
    the button, rendered onto the menu's background clip (it is static, so it is not part of the IG).
    `chapters` adds a Scenes page when there is more than one chapter mark. `menu_seconds`
    overrides how long the looping background clip is (mainly for tests: a longer clip gives a
    slow, unpaced reader like our test oracle room to navigate several pages before it loops).
    """
    try:
        graphics = simple_menu(video, title=title, chapters=chapters)
    except MenuGraphicsError as exc:
        raise MenuError(str(exc)) from exc
    seconds = MENU_SECONDS if menu_seconds is None else menu_seconds
    with tempfile.TemporaryDirectory(prefix="bdauthor-menu-") as tmp:
        menu_bdmv = mux_menu_clip(muxer, video, graphics.segments(), Path(tmp), title=graphics.title, seconds=seconds)
        _copy_menu_clip(menu_bdmv, bdmv_dir)
    index, objects = menu_navigation(movie_playlist=int(_MOVIE_CLIP), menu_playlist=int(MENU_CLIP))
    write_navigation(bdmv_dir, index, objects)
