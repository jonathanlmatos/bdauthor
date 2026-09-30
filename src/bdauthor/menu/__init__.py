"""Adding a menu to a disc directory written by the muxer."""

import shutil
import tempfile
from pathlib import Path

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE, SOURCE_PACKET
from bdauthor.menu.background import MENU_SECONDS, MenuError, check_video_supported, mux_menu_clip
from bdauthor.menu.ig import PTS_CLOCK_HZ
from bdauthor.menu.ig_clip import PCR_PID, PMT_PID, build_ig_clip
from bdauthor.menu.programs import menu_navigation
from bdauthor.menu.simple import MenuGraphicsError, frame_rate_code, simple_menu
from bdauthor.model import Chapter, VideoStream
from bdauthor.mux.base import Muxer
from bdauthor.navigation import write_navigation
from bdauthor.navigation.clip_info import ClipInfoError, add_self_referencing_atc_delta
from bdauthor.navigation.clip_info_writer import build_clip_info
from bdauthor.navigation.playlist import PlaylistError, add_subpath, loop_play_item, retarget_playlist

__all__ = ["MenuError", "add_menu", "check_menu_supported"]

MENU_CLIP = "00001"
_MENU_IG_CLIP = "00002"
_MOVIE_CLIP = "00000"
_MENU_LOOP_REPEATS = 500  # matches a real commercial disc's own repeat count (501, its first PlayItem inclusive)


def _copy_menu_clip(menu_bdmv: Path, bdmv_dir: Path) -> None:
    """Copy the muxed menu background clip into the disc as clip MENU_CLIP (stream, clip info, playlist).

    This clip carries only the black background video: the menu's buttons are a separate,
    out-of-mux IG clip added by `_add_ig_subpath`, matching how a real commercial disc delivers
    a menu (see docs/hdmv-ig-notes.md).
    """
    targets = {
        folder: bdmv_dir / folder / f"{MENU_CLIP}.{suffix}"
        for folder, suffix in (("STREAM", "m2ts"), ("CLIPINF", "clpi"), ("PLAYLIST", "mpls"))
    }
    for target in targets.values():
        if target.exists():
            raise MenuError(f"{target} already exists")

    try:
        clip_info = (menu_bdmv / "CLIPINF" / f"{_MOVIE_CLIP}.clpi").read_bytes()
        targets["CLIPINF"].write_bytes(add_self_referencing_atc_delta(clip_info, clip_id=MENU_CLIP))
        playlist = (menu_bdmv / "PLAYLIST" / f"{_MOVIE_CLIP}.mpls").read_bytes()
        targets["PLAYLIST"].write_bytes(retarget_playlist(playlist, _MOVIE_CLIP, MENU_CLIP))
        shutil.copyfile(menu_bdmv / "STREAM" / f"{_MOVIE_CLIP}.m2ts", targets["STREAM"])
    except (ClipInfoError, PlaylistError) as exc:
        raise MenuError(f"could not use the menu clip: {exc}") from exc

    for folder in ("CLIPINF", "PLAYLIST"):  # BACKUP/ mirrors both
        shutil.copyfile(targets[folder], bdmv_dir / "BACKUP" / folder / targets[folder].name)


def _add_ig_subpath(bdmv_dir: Path, segments: list[bytes], seconds: int) -> None:
    """Write the menu's buttons as a standalone, out-of-mux IG clip and reference it as a SubPath
    of the menu's PlayItem (MENU_CLIP's playlist, already written by `_copy_menu_clip`).
    """
    stream_target = bdmv_dir / "STREAM" / f"{_MENU_IG_CLIP}.m2ts"
    clip_info_target = bdmv_dir / "CLIPINF" / f"{_MENU_IG_CLIP}.clpi"
    if stream_target.exists() or clip_info_target.exists():
        raise MenuError(f"{stream_target} already exists")

    clip = build_ig_clip(segments, seconds)
    stream_target.write_bytes(clip)

    duration_pts = round(seconds * PTS_CLOCK_HZ)  # 90 kHz, matching the clip's own PES PTS/DTS
    presentation_end = duration_pts // 2  # the CLPI's own 45 kHz clock, see docs/hdmv-ig-notes.md
    clip_info = build_clip_info(
        pmt_pid=PMT_PID,
        pcr_pid=PCR_PID,
        stream_pid=IG_PID,
        stream_coding_type=IG_STREAM_TYPE,
        num_source_packets=len(clip) // SOURCE_PACKET,
        presentation_end=presentation_end,
    )
    clip_info_target.write_bytes(clip_info)
    shutil.copyfile(clip_info_target, bdmv_dir / "BACKUP" / "CLIPINF" / clip_info_target.name)

    playlist_target = bdmv_dir / "PLAYLIST" / f"{MENU_CLIP}.mpls"
    try:
        playlist = add_subpath(
            playlist_target.read_bytes(),
            clip_id=_MENU_IG_CLIP,
            in_time=0,
            out_time=presentation_end,
            pid=IG_PID,
        )
    except PlaylistError as exc:
        raise MenuError(f"could not add the menu's IG subpath: {exc}") from exc
    playlist_target.write_bytes(playlist)
    shutil.copyfile(playlist_target, bdmv_dir / "BACKUP" / "PLAYLIST" / playlist_target.name)


def _loop_menu_background(bdmv_dir: Path) -> None:
    """Repeat the menu's background PlayItem so it outlasts any realistic session.

    A MovieObject that re-selects the same short playlist to loop it (`programs.menu_navigation`'s
    `PLAY_PL`/`JUMP_TITLE`) reopens that playlist every time it does -- which resets an
    out-of-mux IG menu back to its first page and reloads its SubPath, confirmed against
    libbluray's own source (see docs/hdmv-ig-notes.md). A real commercial disc avoids this by
    repeating its background PlayItem, seamlessly connected, inside the one playlist that is
    opened just once; this mirrors that, and it needs to run after `_add_ig_subpath` so every
    repeat copy already carries the IG SubPath's STN entry.

    The repeat *count* matters more than the total duration it adds up to: opening a playlist
    re-reads and re-parses its clip's CLIPINF once per PlayItem, even when they all name the
    same clip (`_fill_clip` in libbluray's own `navigation.c` calls `clpi_get` unconditionally
    for every PlayItem). `_MENU_LOOP_REPEATS` matches the real disc's own repeat count instead of
    its total duration -- chasing the same total duration with our own, much shorter background
    clip would need many more repeats (and many more redundant CLIPINF reads) to get there, which
    measurably slowed down navigating the menu; see docs/hdmv-ig-notes.md.
    """
    playlist_target = bdmv_dir / "PLAYLIST" / f"{MENU_CLIP}.mpls"
    try:
        playlist = loop_play_item(playlist_target.read_bytes(), repeats=_MENU_LOOP_REPEATS)
    except PlaylistError as exc:
        raise MenuError(f"could not loop the menu's background: {exc}") from exc
    playlist_target.write_bytes(playlist)
    shutil.copyfile(playlist_target, bdmv_dir / "BACKUP" / "PLAYLIST" / playlist_target.name)


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
        menu_bdmv = mux_menu_clip(muxer, video, Path(tmp), title=graphics.title, seconds=seconds)
        _copy_menu_clip(menu_bdmv, bdmv_dir)
    _add_ig_subpath(bdmv_dir, graphics.segments(), seconds)
    _loop_menu_background(bdmv_dir)
    index, objects = menu_navigation(movie_playlist=int(_MOVIE_CLIP), menu_playlist=int(MENU_CLIP))
    write_navigation(bdmv_dir, index, objects)
