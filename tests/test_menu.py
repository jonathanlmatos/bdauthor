"""Adding a looping black menu to a disc written by tsMuxeR."""

import re
import shutil
import subprocess
from fractions import Fraction

import av
import pytest

from bdauthor.menu import MENU_CLIP, MenuError, add_menu
from bdauthor.menu.background import MENU_SECONDS, title_position, write_black_video
from bdauthor.menu.render import draw_title
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
        # the background is black except for the moving bar (bright, limited-range white);
        # nothing else (like a green tint) should show up anywhere
        assert all(15 <= value <= 17 or value >= 200 for value in row)
        assert any(value >= 200 for value in row)  # the bar is actually present in every row


def test_black_video_with_a_title_draws_white_text_near_the_top_and_stays_black_elsewhere(tmp_path):
    v = stream(width=640, height=360)
    title = draw_title("My Movie", 300, 30)
    path = tmp_path / "black.mkv"
    write_black_video(path, v, seconds=1, title=title)

    x, y = title_position(v, title.width, title.height)
    with av.open(str(path)) as container:
        frame = next(container.decode(video=0))
        plane = frame.planes[0]
        data = bytes(plane)

        def row(r):
            return data[r * plane.line_size : r * plane.line_size + v.width]

        # somewhere in the title's row there is bright text; elsewhere it's black or the bar
        assert max(row(y + title.height // 2)) > 200
        assert all(15 <= value <= 17 or value >= 200 for value in row(v.height - 1))


def test_title_position_is_centred_horizontally_and_near_the_top():
    v = stream(width=1280, height=720)
    x, y = title_position(v, 400, 60)
    assert x == (1280 - 400) // 2
    assert 0 < y < 100
    assert x % 2 == 0 and y % 2 == 0  # even, so chroma subsampling stays aligned


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
    assert re.search(r"JUMP_TITLE\s+0\b", menu)  # back to the top menu (itself), like a real commercial disc
    assert re.search(r"JUMP_TITLE\s+0\b", output.split("Object 2:")[1])


# --- the assembled disc ---------------------------------------------------------------------------


@pytest.fixture
def disc_with_menu(tsmuxer_disc, tmp_path, tsmuxer):
    disc = tmp_path / "disc"
    shutil.copytree(tsmuxer_disc, disc)
    movie = probe(next((tsmuxer_disc / "BDMV" / "STREAM").glob("00000.m2ts"))).video[0]
    add_menu(disc / "BDMV", movie, TsMuxer(tsmuxer), title="My Movie")
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
    # the background PlayItem is repeated (seamlessly) to outlast any realistic session
    # instead of the MovieObject reopening a short playlist in a loop, see docs/hdmv-ig-notes.md
    ids = clip_ids((bdmv / "PLAYLIST" / f"{MENU_CLIP}.mpls").read_bytes())
    assert len(ids) > 1 and set(ids) == {MENU_CLIP}


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


def test_the_menu_clip_shows_the_title_above_the_button(disc_with_menu):
    movie = probe(disc_with_menu / "BDMV" / "STREAM" / "00000.m2ts").video[0]
    title = draw_title("My Movie", 300, round(movie.height * 0.08))
    x, y = title_position(movie, title.width, title.height)
    with av.open(str(disc_with_menu / "BDMV" / "STREAM" / f"{MENU_CLIP}.m2ts")) as container:
        frame = next(container.decode(video=0))
        plane = frame.planes[0]
        data = bytes(plane)
        row = data[(y + title.height // 2) * plane.line_size : (y + title.height // 2) * plane.line_size + movie.width]
    assert max(row[x : x + title.width]) > 200  # bright text somewhere in the title's row


def test_a_menu_without_a_title_has_no_text_in_the_clip(tsmuxer_disc, tmp_path, tsmuxer):
    disc = tmp_path / "disc"
    shutil.copytree(tsmuxer_disc, disc)
    movie = probe(next((tsmuxer_disc / "BDMV" / "STREAM").glob("00000.m2ts"))).video[0]
    add_menu(disc / "BDMV", movie, TsMuxer(tsmuxer))  # no title
    for row in luma_rows(disc / "BDMV" / "STREAM" / f"{MENU_CLIP}.m2ts", movie.width):
        # black or the moving bar only -- no text/graphics snuck in without a title
        assert all(15 <= value <= 17 or value >= 200 for value in row)


@pytest.fixture
def disc_with_scenes(tmp_path_factory, tmp_path, tsmuxer):
    """A disc with a Scenes page: 3 chapters at 0s, 2s and 4s of a 6-second movie."""
    from bdauthor.build import build_disc
    from bdauthor.model import Media
    from tests.conftest import write_mkv

    source = tmp_path_factory.mktemp("scenes_mkv") / "movie.mkv"
    write_mkv(
        source,
        width=1280,
        height=720,
        seconds=6,
        chapters=[(0.0, 2.0, "One"), (2.0, 4.0, "Two"), (4.0, 6.0, "Three")],
    )
    out = tmp_path / "disc"
    build_disc(source, out, Media.BD25, TsMuxer(tsmuxer))
    add_menu(out / "BDMV", probe(source).video[0], TsMuxer(tsmuxer), chapters=probe(source).chapters)
    return out


def test_libbluray_navigates_into_scenes_and_a_chosen_scene_jumps_to_its_mark(disc_with_scenes, libbluray_tool):
    lines = play_disc(libbluray_tool, disc_with_scenes, "down", "enter", "right", "right", "enter")
    statuses = [line for line in lines if line.startswith("STATUS")]
    assert statuses == [
        "STATUS page=0 button=2",  # down: Scenes selected on the main page
        "STATUS page=1 button=1",  # enter: the Scenes page opens, scene 1 selected by default
        "STATUS page=1 button=2",  # right
        "STATUS page=1 button=3",  # right
        "STATUS page=1 button=3",  # enter: activating scene 3 does not change the selection
    ]
    after_pick = lines[[i for i, line in enumerate(lines) if line == "KEY enter"][1] :]  # the second enter
    assert "EVENT TITLE 1" in after_pick  # the movie starts
    assert "EVENT CHAPTER 3" in after_pick  # at scene 3 (1-based)
    assert after_pick.index("EVENT TITLE 1") < after_pick.index("EVENT CHAPTER 3")


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
    """The IG stream is a separate, out-of-mux SubPath clip, matching a real commercial disc."""
    bdmv = disc_with_menu / "BDMV"
    playlist = subprocess.run(
        [str(libbluray_tool("mpls_dump")), "-i", str(bdmv / "PLAYLIST" / f"{MENU_CLIP}.mpls")],
        capture_output=True,
        text=True,
    ).stdout
    assert "SubPath Id: 00" in playlist
    assert "Interactive Graphics Stream 0" in playlist and "PID: 1400" in playlist
    clip = subprocess.run(
        [str(libbluray_tool("clpi_dump")), "-p", str(bdmv / "CLIPINF" / "00002.clpi")],
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
