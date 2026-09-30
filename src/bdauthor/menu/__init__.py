"""Adding a menu to a disc directory written by the muxer."""

import shutil
import tempfile
from pathlib import Path

from bdauthor.bdav import IG_PID, IG_STREAM_TYPE, SOURCE_PACKET
from bdauthor.menu.background import MENU_SECONDS, MenuError, check_video_supported, mux_menu_clip
from bdauthor.menu.ig import PTS_CLOCK_HZ, decode_gap
from bdauthor.menu.ig_clip import PCR_PID, PMT_PID, build_ig_clip
from bdauthor.menu.programs import menu_navigation
from bdauthor.menu.simple import MenuGraphicsError, frame_rate_code, simple_menu
from bdauthor.model import Chapter, VideoStream
from bdauthor.mux.base import Muxer
from bdauthor.navigation import write_navigation
from bdauthor.navigation.clip_info import ClipInfoError, add_self_referencing_atc_delta
from bdauthor.navigation.clip_info_writer import build_clip_info
from bdauthor.navigation.playlist import (
    PlaylistError,
    add_subpath,
    loop_play_item,
    mask_user_operations,
    retarget_playlist,
    uo_mask,
)

__all__ = ["MenuError", "add_menu", "check_menu_supported"]

MENU_CLIP = "00001"
_MENU_IG_CLIP = "00002"
_MOVIE_CLIP = "00000"
_MENU_LOOP_REPEATS = 500  # matches a real commercial disc's own repeat count (501, its first PlayItem inclusive)

# How long the IG SubPath's own clip/SubPlayItem lasts: NOT tied to the background loop's own
# duration (`MENU_SECONDS`/`menu_seconds`). A real commercial disc's own menu SubPath is a short
# clip (~3.1s observed) synced to the very start of the main path and never repeated, regardless
# of how long the background video loops for underneath it -- once the composition is decoded,
# it stays on the graphics plane on its own (composition_timeout_pts=0, "never expires"; see
# docs/hdmv-ig-notes.md). Tying this to the background loop's own duration instead (as done
# before) made the IG clip, and its SubPlayItem's Out_Time, last as long as one loop repeat
# (10s by default) for no structural reason.
_IG_CLIP_SECONDS = 3

# The reference disc's own authoring tool never starts a clip's real content at PTS/DTS 0: its
# menu's IG clip, its menu's own background video, and even an unrelated still-image title's clip
# all start at this exact same PTS (90 kHz)/45 kHz-clock lead-in instead (524280 in the CLPI/MPLS
# 45 kHz clock; this is its 90 kHz PES-clock equivalent). It is a fixed convention of that
# toolchain, confirmed identical across multiple, otherwise unrelated clips on that disc -- not
# something tied to this menu's own content, but reproduced anyway for full field-for-field
# parity. See docs/hdmv-ig-notes.md. (Note: PCR is not affected -- that clip's own PCR values sit
# on a much larger, disc-wide absolute clock already established as unrelated busywork to chase;
# see the refuted PCR-discontinuity investigation in docs/hdmv-ig-notes.md.)
_IG_LEAD_IN_PTS = 1_048_560

# Confirmed against the reference disc's own menu playlist's AppInfoPlayList().UO_mask_table
# (decoded with libbluray's own bit layout, src/libbluray/bdnav/uo_mask_table.h/uo_mask.c -- see
# docs/hdmv-ig-notes.md): every operation with no meaning for a chapterless, single-angle,
# button-only menu clip with no subtitles or secondary audio/video is masked, while button
# navigation (move_*/select/activate/select_and_activate) and stop/pause_off/resume/menu_call/
# title_search stay unmasked. This is a fixed, structural property of a menu like ours, not a
# guess: any disc's own out-of-mux HDMV button menu (no chapters, one angle, no PG/secondary
# streams) has the same feature set, so the same mask applies.
_MENU_UO_MASK = uo_mask(
    masked=frozenset(
        {
            "chapter_search",
            "time_search",
            "skip_to_next_point",
            "skip_to_prev_point",
            "pause_on",
            "still_off",
            "forward",
            "backward",
            "primary_audio_change",
            "angle_change",
            "popup_on",
            "popup_off",
            "pg_enable_disable",
            "pg_change",
            "secondary_video_enable_disable",
            "secondary_video_change",
            "secondary_audio_enable_disable",
            "secondary_audio_change",
            "pip_pg_change",
        }
    )
)


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


def _mask_menu_user_operations(bdmv_dir: Path) -> None:
    """Patch the menu playlist's own `AppInfoPlayList().UO_mask_table` to `_MENU_UO_MASK`."""
    playlist_target = bdmv_dir / "PLAYLIST" / f"{MENU_CLIP}.mpls"
    try:
        playlist = mask_user_operations(playlist_target.read_bytes(), _MENU_UO_MASK)
    except PlaylistError as exc:
        raise MenuError(f"could not set the menu's UO mask: {exc}") from exc
    playlist_target.write_bytes(playlist)
    shutil.copyfile(playlist_target, bdmv_dir / "BACKUP" / "PLAYLIST" / playlist_target.name)


def _add_ig_subpath(bdmv_dir: Path, segments: list[bytes]) -> None:
    """Write the menu's buttons as a standalone, out-of-mux IG clip and reference it as a SubPath
    of the menu's PlayItem (MENU_CLIP's playlist, already written by `_copy_menu_clip`).

    Its own duration is `_IG_CLIP_SECONDS`, independent of the background loop's length: see there.
    Its content starts at `_IG_LEAD_IN_PTS`, not at PTS/DTS 0: see there.
    """
    stream_target = bdmv_dir / "STREAM" / f"{_MENU_IG_CLIP}.m2ts"
    clip_info_target = bdmv_dir / "CLIPINF" / f"{_MENU_IG_CLIP}.clpi"
    if stream_target.exists() or clip_info_target.exists():
        raise MenuError(f"{stream_target} already exists")

    gap = decode_gap(sum(len(data) for data in segments))
    if gap >= _IG_CLIP_SECONDS * PTS_CLOCK_HZ:
        raise MenuError("the menu's graphics are too large to decode within the IG clip's own duration")

    clip = build_ig_clip(segments, _IG_CLIP_SECONDS, pts=_IG_LEAD_IN_PTS)
    stream_target.write_bytes(clip)

    duration_pts = round(_IG_CLIP_SECONDS * PTS_CLOCK_HZ)  # 90 kHz, matching the clip's own PES PTS/DTS
    presentation_start = _IG_LEAD_IN_PTS // 2  # the CLPI/MPLS 45 kHz clock, see docs/hdmv-ig-notes.md
    presentation_end = presentation_start + duration_pts // 2
    clip_info = build_clip_info(
        pmt_pid=PMT_PID,
        pcr_pid=PCR_PID,
        stream_pid=IG_PID,
        stream_coding_type=IG_STREAM_TYPE,
        num_source_packets=len(clip) // SOURCE_PACKET,
        presentation_start=presentation_start,
        presentation_end=presentation_end,
    )
    clip_info_target.write_bytes(clip_info)
    shutil.copyfile(clip_info_target, bdmv_dir / "BACKUP" / "CLIPINF" / clip_info_target.name)

    playlist_target = bdmv_dir / "PLAYLIST" / f"{MENU_CLIP}.mpls"
    try:
        playlist = add_subpath(
            playlist_target.read_bytes(),
            clip_id=_MENU_IG_CLIP,
            in_time=presentation_start,
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
    _mask_menu_user_operations(bdmv_dir)
    _add_ig_subpath(bdmv_dir, graphics.segments())
    _loop_menu_background(bdmv_dir)
    index, objects = menu_navigation(movie_playlist=int(_MOVIE_CLIP), menu_playlist=int(MENU_CLIP))
    write_navigation(bdmv_dir, index, objects)
