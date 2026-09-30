"""index.bdmv / MovieObject.bdmv writers.

Two independent checks: the bytes tsMuxeR writes for the same program, and libbluray's own
dump tools reading our files back.
"""

import re
import struct
import subprocess

import pytest

from bdauthor.navigation import write_navigation
from bdauthor.navigation.index import Access, HdmvRef, IndexTable, PlaybackType, Title
from bdauthor.navigation.playlist import (
    PlaylistError,
    add_subpath,
    clip_ids,
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


def test_clip_ids_refuses_garbage_and_truncated_files(tsmuxer_disc):
    data = bytearray((tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes())
    list_pos = int.from_bytes(data[8:12], "big")
    with pytest.raises(PlaylistError, match="not a playlist"):
        clip_ids(b"NOPE" * 10)
    with pytest.raises(PlaylistError, match="truncated"):
        clip_ids(bytes(data[: list_pos + 4]))


def test_clip_ids_ignores_sub_paths_added_by_add_subpath(tsmuxer_disc):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    patched = add_subpath(data, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    assert clip_ids(patched) == clip_ids(data)  # the SubPath's clip is not a PlayItem


# --- SET_BUTTON_PAGE (IG button command) -----------------------------------------------------------


def test_set_button_page_word_layout(libbluray_tool, tmp_path):
    from bdauthor.navigation.movie_object import set_button_page

    output = dump_program(libbluray_tool, tmp_path, [MovieObject(set_button_page(page_id=3, button_id=7))])
    assert re.search(r"move\s+r4076\s*,\s*7", output)  # button id moved into the scratch register first
    assert re.search(r"move\s+r4077\s*,\s*3", output)  # ... then the page id
    # SET_BUTTON_PAGE references those registers -- 0x80000fec/0x80000fed = flag bit | register number,
    # matching a real commercial disc's own encoding (see docs/hdmv-ig-notes.md), never a plain immediate
    assert re.search(r"SET_BUTTON_PAGE\s+0x80000fec,\s*0x80000fed", output)


def test_set_button_page_rejects_out_of_range_ids():
    from bdauthor.navigation.movie_object import set_button_page

    with pytest.raises(ValueError, match="page id"):
        set_button_page(page_id=0xFF, button_id=0)
    with pytest.raises(ValueError, match="button id"):
        set_button_page(page_id=0, button_id=0x10000)


# --- is_atc_delta (a real commercial disc's own menu background clip has this set) -----------------


def test_add_self_referencing_atc_delta_sets_the_flag_and_one_entry(tsmuxer_disc):
    from bdauthor.navigation.clip_info import add_self_referencing_atc_delta

    data = (tsmuxer_disc / "BDMV" / "CLIPINF" / "00000.clpi").read_bytes()
    patched = add_self_referencing_atc_delta(data, clip_id="00001")
    assert not data[51] & 1  # unset before
    assert patched[51] & 1  # is_atc_delta set after
    # ClipInfo() grew by exactly one ATC delta entry (reserved(1) count(1) delta(4) file_id(5) file_code(4) reserved(1))
    assert len(patched) == len(data) + 16
    (seq_before,) = struct.unpack_from(">I", data, 8)
    (seq_after,) = struct.unpack_from(">I", patched, 8)
    assert seq_after == seq_before + 16  # every later block address shifted by the insertion


def test_add_self_referencing_atc_delta_refuses_to_set_it_twice(tsmuxer_disc):
    from bdauthor.navigation.clip_info import ClipInfoError, add_self_referencing_atc_delta

    data = (tsmuxer_disc / "BDMV" / "CLIPINF" / "00000.clpi").read_bytes()
    patched = add_self_referencing_atc_delta(data, clip_id="00001")
    with pytest.raises(ClipInfoError, match="already set"):
        add_self_referencing_atc_delta(patched, clip_id="00001")


def test_libbluray_reads_the_atc_delta_entry(tsmuxer_disc, libbluray_tool, tmp_path):
    from bdauthor.navigation.clip_info import add_self_referencing_atc_delta

    data = (tsmuxer_disc / "BDMV" / "CLIPINF" / "00000.clpi").read_bytes()
    patched = add_self_referencing_atc_delta(data, clip_id="00001")
    path = tmp_path / "00000.clpi"
    path.write_bytes(patched)
    out = subprocess.run([str(libbluray_tool("clpi_dump")), "-c", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "is_ATC_delta: True" in out.stdout
    assert "File Id 00001" in out.stdout
    assert "File Code M2TS" in out.stdout
