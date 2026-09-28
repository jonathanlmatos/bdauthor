"""index.bdmv / MovieObject.bdmv writers.

Two independent checks: the bytes tsMuxeR writes for the same program, and libbluray's own
dump tools reading our files back.
"""

import re
import subprocess

import pytest

from bdauthor.navigation import write_navigation
from bdauthor.navigation.index import Access, HdmvRef, IndexTable, PlaybackType, Title
from bdauthor.navigation.clip_info import ClipInfoError, program_map_pid, relabel_stream
from bdauthor.navigation.playlist import (
    PlaylistError,
    clip_ids,
    relabel_graphics_as_interactive,
    retarget_playlist,
)
from bdauthor.navigation.movie_object import (
    Compare,
    MovieObject,
    break_,
    call_object,
    call_title,
    compare,
    goto,
    imm,
    jump_object,
    jump_title,
    move,
    nop,
    pack_movie_objects,
    play_pl,
    play_pl_mk,
    play_pl_pi,
    reg,
    resume,
    terminate_pl,
)


def tsmuxer_objects() -> list[MovieObject]:
    """The three movie objects tsMuxeR writes for a single-playlist disc (read from its output)."""
    play_title = MovieObject(
        (
            move(reg(10), reg(0)),
            move(reg(0), imm(0)),
            move(reg(0), imm(0)),
            play_pl_mk(imm(0), reg(10)),
            break_(),
        )
    )
    top_menu = MovieObject(
        (
            move(reg(10), reg(3)),
            move(reg(3), imm(0xFFFF)),
            compare(Compare.NE, reg(10), imm(0xFFFF)),
            play_pl(reg(10)),
            move(reg(10), reg(4)),
            move(reg(4), imm(0)),
            compare(Compare.NE, reg(10), imm(0)),
            jump_title(reg(10)),
            jump_title(imm(1)),
        )
    )
    first_playback = MovieObject(
        (
            move(reg(0), imm(0)),
            move(reg(2), imm(1)),
            move(reg(3), imm(0xFFFF)),
            move(reg(4), imm(0)),
            jump_title(imm(0)),
        )
    )
    return [play_title, top_menu, first_playback]


def tsmuxer_index() -> IndexTable:
    return IndexTable(
        first_playback=HdmvRef(2),
        top_menu=HdmvRef(1),
        titles=(Title(HdmvRef(0, PlaybackType.MOVIE)),),
    )


# --- instruction words, copied from a real tsMuxeR MovieObject.bdmv ------------------------------


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        (move(reg(10), reg(0)), "50000001 0000000a 00000000"),
        (move(reg(3), imm(0xFFFF)), "50400001 00000003 0000ffff"),
        (play_pl_mk(imm(0), reg(10)), "42820000 00000000 0000000a"),
        (break_(), "00020000 00000000 00000000"),
        (compare(Compare.NE, reg(10), imm(0xFFFF)), "48400300 0000000a 0000ffff"),
        (play_pl(reg(10)), "22000000 0000000a 00000000"),
        (jump_title(reg(10)), "21010000 0000000a 00000000"),
        (jump_title(imm(1)), "21810000 00000001 00000000"),
    ],
)
def test_instruction_words_match_the_ones_tsmuxer_writes(instruction, expected):
    assert instruction.pack() == bytes.fromhex(expected.replace(" ", ""))


def test_a_register_must_fit_the_general_purpose_range():
    reg(4095)
    with pytest.raises(ValueError, match="register"):
        reg(4096)
    imm(0xFFFFFFFF)  # a constant may be any 32-bit value
    with pytest.raises(ValueError):
        imm(2**32)


def test_move_needs_a_register_destination():
    with pytest.raises(ValueError, match="register"):
        move(imm(1), imm(2))


def test_the_object_flags_and_length_fields_are_consistent():
    data = pack_movie_objects([MovieObject((nop(),), resume_intention=False, menu_call_mask=True)])
    assert data[:8] == b"MOBJ0200"
    assert int.from_bytes(data[40:44], "big") == len(data) - 44  # length field: bytes after it
    assert int.from_bytes(data[48:50], "big") == 1  # one object
    assert data[50:52] == b"\x40\x00"  # only menu_call_mask set
    assert int.from_bytes(data[52:54], "big") == 1


def test_index_layout():
    data = tsmuxer_index().pack()
    assert data[:8] == b"INDX0200"
    assert int.from_bytes(data[8:12], "big") == 78  # where the index starts
    assert int.from_bytes(data[78:82], "big") == len(data) - 82
    assert len(data) == 120


def test_index_rejects_out_of_range_video_format():
    with pytest.raises(ValueError):
        IndexTable(HdmvRef(0), HdmvRef(0), video_format=16).pack()


# --- against the real tsMuxeR output --------------------------------------------------------------


def test_our_files_are_byte_identical_to_tsmuxers(tsmuxer_disc, tmp_path):
    bdmv = tmp_path / "BDMV"
    bdmv.mkdir()
    write_navigation(bdmv, tsmuxer_index(), tsmuxer_objects())

    real = tsmuxer_disc / "BDMV"
    for name in ("index.bdmv", "MovieObject.bdmv"):
        assert (bdmv / name).read_bytes() == (real / name).read_bytes(), name
        assert (bdmv / "BACKUP" / name).read_bytes() == (real / "BACKUP" / name).read_bytes(), name


def test_write_navigation_writes_the_backup_copies(tmp_path):
    write_navigation(tmp_path, tsmuxer_index(), tsmuxer_objects())
    for name in ("index.bdmv", "MovieObject.bdmv"):
        assert (tmp_path / name).read_bytes() == (tmp_path / "BACKUP" / name).read_bytes()


# --- against libbluray's dump tools ---------------------------------------------------------------


def dump_program(libbluray_tool, tmp_path, objects):
    path = tmp_path / "MovieObject.bdmv"
    path.write_bytes(pack_movie_objects(objects))
    result = subprocess.run(
        [str(libbluray_tool("mobj_dump")), "-d", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_mobj_dump_reads_every_instruction_we_can_write(libbluray_tool, tmp_path):
    program = (
        nop(),
        goto(imm(3)),
        break_(),
        jump_object(imm(2)),
        jump_title(imm(1)),
        call_object(reg(5)),
        call_title(imm(4)),
        resume(),
        play_pl(imm(7)),
        play_pl_pi(imm(7), imm(1)),
        play_pl_mk(imm(7), reg(9)),
        terminate_pl(),
        compare(Compare.EQ, reg(1), imm(2)),
        move(reg(6), imm(1234)),
    )
    output = dump_program(libbluray_tool, tmp_path, [MovieObject(program)])

    rows = []
    for line in output.splitlines():
        match = re.match(r"\s+\d{4}: \w{8} \w{8},\w{8}\s+(\S+)\s*(.*)", line)
        if match:
            rows.append(match.groups())
    assert [name.upper() for name, _ in rows] == [
        "NOP",
        "GOTO",
        "BREAK",
        "JUMP_OBJECT",
        "JUMP_TITLE",
        "CALL_OBJECT",
        "CALL_TITLE",
        "RESUME",
        "PLAY_PL",
        "PLAY_PL_PI",
        "PLAY_PL_MK",
        "TERMINATE_PL",
        "EQ",
        "MOVE",
    ]
    operands = {name.upper(): rest for name, rest in rows}
    assert operands["JUMP_OBJECT"].split() == ["2"]
    assert operands["CALL_OBJECT"].split() == ["r5"]
    assert re.split(r"\s*,\s*|\s+", operands["PLAY_PL_MK"].strip()) == ["7", "r9"]
    assert re.split(r"\s*,\s*|\s+", operands["MOVE"].strip()) == ["r6", "1234"]


def test_mobj_dump_reads_the_object_flags(libbluray_tool, tmp_path):
    output = dump_program(
        libbluray_tool,
        tmp_path,
        [MovieObject((nop(),), resume_intention=False, menu_call_mask=True, title_search_mask=True)],
    )
    assert "resume intention flag: 0" in output
    assert "menu call mask:        1" in output
    assert "title search mask:     1" in output


def test_index_dump_reads_our_index(libbluray_tool, tmp_path):
    index = IndexTable(
        first_playback=HdmvRef(2),
        top_menu=HdmvRef(1),
        titles=(Title(HdmvRef(0, PlaybackType.MOVIE)), Title(HdmvRef(5), Access.HIDDEN)),
    )
    bdmv = tmp_path / "BDMV"
    bdmv.mkdir()
    write_navigation(bdmv, index, tsmuxer_objects())

    result = subprocess.run(
        [str(libbluray_tool("index_dump")), str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert re.search(r"First playback:\s+object type\s*: HDMV\s+playback type\s*: Interactive\s+id_ref\s*: 2", out)
    assert re.search(r"Top menu:\s+object type\s*: HDMV\s+playback type\s*: Interactive\s+id_ref\s*: 1", out)
    assert "Titles: 2" in out


# --- playlists ------------------------------------------------------------------------------------


def test_clip_ids_lists_the_clip_of_every_play_item(tsmuxer_disc):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    assert clip_ids(data) == ["00000"]


def test_retarget_playlist_changes_only_the_clip_id(tsmuxer_disc):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    patched = retarget_playlist(data, "00000", "00001")
    assert clip_ids(patched) == ["00001"]
    assert len(patched) == len(data)
    assert sum(a != b for a, b in zip(data, patched)) == 1  # "00000" -> "00001": one digit differs


def test_retarget_playlist_refuses_the_wrong_clip(tsmuxer_disc):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    with pytest.raises(PlaylistError, match="expected 00007"):
        retarget_playlist(data, "00007", "00001")


@pytest.mark.parametrize("bad", ["1", "abcde", "000001"])
def test_retarget_playlist_refuses_a_malformed_clip_id(tsmuxer_disc, bad):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    with pytest.raises(PlaylistError, match="5 digits"):
        retarget_playlist(data, "00000", bad)


def test_retarget_playlist_refuses_sub_paths_and_garbage(tsmuxer_disc):
    data = bytearray((tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes())
    list_pos = int.from_bytes(data[8:12], "big")
    data[list_pos + 8 : list_pos + 10] = b"\x00\x01"  # number_of_SubPaths = 1
    with pytest.raises(PlaylistError, match="sub paths"):
        clip_ids(bytes(data))
    with pytest.raises(PlaylistError, match="not a playlist"):
        clip_ids(b"NOPE" * 10)
    with pytest.raises(PlaylistError, match="truncated"):
        clip_ids(bytes(data[: list_pos + 4]))


# --- clip info and the graphics stream of a playlist ----------------------------------------------

_RELABEL = {"old_pid": 0x1200, "new_pid": 0x1400, "old_coding": 0x90, "new_coding": 0x91}


def test_program_map_pid_of_a_muxed_clip(muxed_menu):
    bdmv, _ = muxed_menu
    assert program_map_pid((bdmv / "CLIPINF" / "00000.clpi").read_bytes()) == 0x100


def test_relabel_stream_changes_only_the_pid_and_coding_type(muxed_menu):
    bdmv, _ = muxed_menu
    data = (bdmv / "CLIPINF" / "00000.clpi").read_bytes()
    patched = relabel_stream(data, **_RELABEL)
    assert len(patched) == len(data)
    changed = [i for i, (a, b) in enumerate(zip(data, patched)) if a != b]
    assert len(changed) == 2  # 0x12 -> 0x14 (PID) and 0x90 -> 0x91 (coding type)


def test_relabel_stream_refuses_a_stream_that_is_not_there(muxed_menu):
    bdmv, _ = muxed_menu
    data = (bdmv / "CLIPINF" / "00000.clpi").read_bytes()
    with pytest.raises(ClipInfoError, match="no stream"):
        relabel_stream(data, **{**_RELABEL, "old_pid": 0x1300})
    with pytest.raises(ClipInfoError, match="not a clip info"):
        relabel_stream(b"MPLS" * 10, **_RELABEL)
    with pytest.raises(ClipInfoError, match="truncated"):
        relabel_stream(data[:20], **_RELABEL)


def test_relabelled_playlist_lists_an_interactive_stream_for_libbluray(muxed_menu, libbluray_tool, tmp_path):
    bdmv, _ = muxed_menu
    data = (bdmv / "PLAYLIST" / "00000.mpls").read_bytes()
    path = tmp_path / "00000.mpls"
    path.write_bytes(relabel_graphics_as_interactive(data, **_RELABEL))
    out = subprocess.run([str(libbluray_tool("mpls_dump")), "-i", str(path)], capture_output=True, text=True).stdout
    assert "Interactive Graphics Stream 0" in out and "Codec (0091)" in out and "PID: 1400" in out
    assert "Presentation Graphics Stream" not in out


def test_relabel_playlist_refuses_a_playlist_without_graphics(tsmuxer_disc):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    with pytest.raises(PlaylistError, match="exactly one"):
        relabel_graphics_as_interactive(data, **_RELABEL)


def test_relabel_playlist_refuses_another_stream(muxed_menu):
    bdmv, _ = muxed_menu
    data = (bdmv / "PLAYLIST" / "00000.mpls").read_bytes()
    with pytest.raises(PlaylistError, match="not the expected"):
        relabel_graphics_as_interactive(data, **{**_RELABEL, "old_pid": 0x1300})
