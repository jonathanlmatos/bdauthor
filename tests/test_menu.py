"""Adding a looping black menu to a disc written by tsMuxeR."""

import re
import shutil
import subprocess
from fractions import Fraction

import av
import pytest

from bdauthor.menu import MENU_CLIP, MenuError, add_menu
from bdauthor.menu.background import MENU_SECONDS, write_black_video
from bdauthor.menu.programs import menu_navigation
from bdauthor.menu.simple import simple_menu
from bdauthor.model import VideoStream
from bdauthor.mux.tsmuxer import TsMuxer
from bdauthor.probe import probe
from bdauthor.navigation.playlist import clip_ids
from tests.builders import video as make_video


def stream(**overrides) -> VideoStream:
    return make_video(**{"width": 1280, "height": 720, "fps": Fraction(24000, 1001), **overrides})


def luma_rows(path, width):
    """The luma of the first frame of a video file: a few rows, without the line padding."""
    with av.open(str(path)) as container:
        frame = next(container.decode(video=0))
        plane = frame.planes[0]
        data = bytes(plane)
        return [data[row * plane.line_size : row * plane.line_size + width] for row in (0, frame.height // 2, frame.height - 1)]


# --- the black video ------------------------------------------------------------------------------


def test_black_video_has_the_size_and_frame_rate_of_the_movie(tmp_path):
    path = tmp_path / "black.mkv"
    write_black_video(path, stream(), seconds=2)
    info = probe(path)
    assert (info.video[0].width, info.video[0].height) == (1280, 720)
    assert info.video[0].fps == Fraction(24000, 1001)
    assert info.duration == pytest.approx(2, abs=0.2)
    assert not info.audio


def test_black_video_is_black_not_green(tmp_path):
    path = tmp_path / "black.mkv"
    write_black_video(path, stream(width=640, height=360), seconds=1)
    for row in luma_rows(path, 640):
        assert all(15 <= value <= 17 for value in row)  # limited-range black is 16


def test_black_video_refuses_interlaced_and_unknown_rates(tmp_path):
    with pytest.raises(MenuError, match="interlaced"):
        write_black_video(tmp_path / "a.mkv", stream(interlaced=True))
    with pytest.raises(MenuError, match="frame rate"):
        write_black_video(tmp_path / "b.mkv", stream(fps=None))


# --- the navigation program -----------------------------------------------------------------------


def test_menu_navigation_wires_first_playback_to_the_top_menu_and_the_movie_to_title_1():
    index, objects = menu_navigation()
    assert index.first_playback.movie_object == 2
    assert index.top_menu.movie_object == 1
    assert [title.ref.movie_object for title in index.titles] == [0]
    assert len(objects) == 3


def test_the_menu_object_plays_the_menu_playlist_in_a_loop(libbluray_tool, tmp_path):
    from bdauthor.navigation.movie_object import pack_movie_objects

    _, objects = menu_navigation(movie_playlist=0, menu_playlist=1)
    path = tmp_path / "MovieObject.bdmv"
    path.write_bytes(pack_movie_objects(objects))
    output = subprocess.run(
        [str(libbluray_tool("mobj_dump")), "-d", str(path)], capture_output=True, text=True
    ).stdout
    menu = output.split("Object 1:")[1].split("Object 2:")[0]
    assert re.search(r"PLAY_PL\s+1\b", menu)
    assert re.search(r"JUMP_OBJECT\s+1\b", menu)  # back to itself
    assert re.search(r"JUMP_TITLE\s+0\b", output.split("Object 2:")[1])


# --- the assembled disc ---------------------------------------------------------------------------


@pytest.fixture
def disc_with_menu(tsmuxer_disc, tmp_path, tsmuxer):
    disc = tmp_path / "disc"
    shutil.copytree(tsmuxer_disc, disc)
    movie = probe(next((tsmuxer_disc / "BDMV" / "STREAM").glob("00000.m2ts"))).video[0]
    add_menu(disc / "BDMV", movie, TsMuxer(tsmuxer))
    return disc


def test_the_menu_clip_playlist_and_backups_are_added(disc_with_menu):
    bdmv = disc_with_menu / "BDMV"
    for folder, suffix in (("STREAM", "m2ts"), ("CLIPINF", "clpi"), ("PLAYLIST", "mpls")):
        assert (bdmv / folder / f"{MENU_CLIP}.{suffix}").is_file()
    for folder, suffix in (("CLIPINF", "clpi"), ("PLAYLIST", "mpls")):
        assert (bdmv / "BACKUP" / folder / f"{MENU_CLIP}.{suffix}").read_bytes() == (
            bdmv / folder / f"{MENU_CLIP}.{suffix}"
        ).read_bytes()
    for name in ("index.bdmv", "MovieObject.bdmv"):
        assert (bdmv / name).read_bytes() == (bdmv / "BACKUP" / name).read_bytes()
    assert clip_ids((bdmv / "PLAYLIST" / f"{MENU_CLIP}.mpls").read_bytes()) == [MENU_CLIP]


def test_the_movie_is_left_untouched(disc_with_menu, tsmuxer_disc):
    for name in ("STREAM/00000.m2ts", "CLIPINF/00000.clpi", "PLAYLIST/00000.mpls"):
        assert (disc_with_menu / "BDMV" / name).read_bytes() == (tsmuxer_disc / "BDMV" / name).read_bytes()


def test_the_menu_clip_is_a_black_video_of_the_expected_length(disc_with_menu):
    info = probe(disc_with_menu / "BDMV" / "STREAM" / f"{MENU_CLIP}.m2ts")
    assert len(info.video) == 1
    assert info.duration == pytest.approx(MENU_SECONDS, abs=1)


def test_libbluray_reads_the_menu_playlist_and_clip_info(disc_with_menu, libbluray_tool):
    bdmv = disc_with_menu / "BDMV"
    mpls = subprocess.run(
        [str(libbluray_tool("mpls_dump")), "-c", str(bdmv / "PLAYLIST" / f"{MENU_CLIP}.mpls")],
        capture_output=True,
        text=True,
    )
    assert mpls.returncode == 0
    assert f"PlayItem: {MENU_CLIP}" in mpls.stdout
    clpi = subprocess.run(
        [str(libbluray_tool("clpi_dump")), "-i", str(bdmv / "CLIPINF" / f"{MENU_CLIP}.clpi")],
        capture_output=True,
        text=True,
    )
    assert clpi.returncode == 0, clpi.stderr


def test_libbluray_runs_first_playback_into_a_looping_menu(disc_with_menu, libbluray_tool):
    """hdmv_test executes the disc's navigation: first playback -> top menu -> menu playlist, repeatedly."""
    process = subprocess.Popen(
        [str(libbluray_tool("hdmv_test")), "-v", str(disc_with_menu)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:  # it loops for ever; what it printed so far is enough
        process.kill()
        output, _ = process.communicate()
    assert "jumping to object 2" in output  # first playback
    assert "_jump_title(0)" in output  # ... to the top menu
    assert "jumping to object 1" in output
    assert f"PLAYLIST:                 {MENU_CLIP}.mpls" in output
    assert output.count("Playing playlist done") >= 2  # it started again by itself


def test_adding_a_menu_twice_is_refused(disc_with_menu, tsmuxer):
    movie = probe(disc_with_menu / "BDMV" / "STREAM" / "00000.m2ts").video[0]
    with pytest.raises(MenuError, match="already exists"):
        add_menu(disc_with_menu / "BDMV", movie, TsMuxer(tsmuxer))


# --- the interactive graphics of the menu, as libbluray plays them --------------------------------


def play_disc(libbluray_tool, disc, *keys):
    """Run bd_menu_test on `disc`, pressing `keys` once the menu is on screen; returns its output lines."""
    command = [str(libbluray_tool("bd_menu_test"))]
    if keys:
        command += ["-k", ",".join(keys)]
    result = subprocess.run([*command, str(disc)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_libbluray_lists_an_interactive_graphics_stream_in_the_menu_clip(disc_with_menu, libbluray_tool):
    bdmv = disc_with_menu / "BDMV"
    playlist = subprocess.run(
        [str(libbluray_tool("mpls_dump")), "-i", str(bdmv / "PLAYLIST" / f"{MENU_CLIP}.mpls")],
        capture_output=True,
        text=True,
    ).stdout
    assert "Interactive Graphics Stream 0" in playlist and "PID: 1400" in playlist
    clip = subprocess.run(
        [str(libbluray_tool("clpi_dump")), "-p", str(bdmv / "CLIPINF" / f"{MENU_CLIP}.clpi")],
        capture_output=True,
        text=True,
    ).stdout
    assert "Codec (0091): Interactive Graphics" in clip


def test_the_menu_is_drawn_where_the_button_was_placed(disc_with_menu, libbluray_tool):
    movie = probe(disc_with_menu / "BDMV" / "STREAM" / "00000.m2ts").video[0]
    graphics = simple_menu(movie)
    button = graphics.composition.pages[0].buttons[0]
    image = graphics.images[0]

    lines = play_disc(libbluray_tool, disc_with_menu)
    assert f"OVERLAY plane=IG cmd=INIT pts=-1 x=0 y=0 w={movie.width} h={movie.height}" in lines
    draws = [line for line in lines if line.startswith("OVERLAY plane=IG cmd=DRAW")]
    expected = f"x={button.x} y={button.y} w={image.width} h={image.height}"
    assert draws and all(line.endswith(expected) for line in draws)  # the Play button
    assert "EVENT MENU 1" in lines
    assert "EVENT TITLE 1" not in lines  # nothing starts the movie by itself


def test_the_menu_starts_before_the_movie_and_plays_its_own_playlist(disc_with_menu, libbluray_tool):
    lines = play_disc(libbluray_tool, disc_with_menu)
    assert lines.index("EVENT TITLE 0") < lines.index("EVENT PLAYLIST 1")  # top menu, then the menu playlist
    assert "EVENT PLAYLIST 0" not in lines


def test_pressing_enter_on_play_starts_the_movie_and_closes_the_menu(disc_with_menu, libbluray_tool):
    lines = play_disc(libbluray_tool, disc_with_menu, "enter")
    key = lines.index("KEY enter")
    after = lines[key:]
    assert "EVENT TITLE 1" in after  # the button jumps to title 1
    assert "EVENT PLAYLIST 0" in after  # ... which plays the movie's playlist
    assert "EVENT MENU 0" in after  # and the menu goes away
    assert after.index("EVENT TITLE 1") < after.index("EVENT PLAYLIST 0")
