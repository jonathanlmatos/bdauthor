"""index.bdmv / MovieObject.bdmv writers.

Two independent checks: the bytes tsMuxeR writes for the same program, and libbluray's own
dump tools reading our files back.
"""

import re
import subprocess

import pytest

from bdauthor.navigation import write_navigation
from bdauthor.navigation.index import Access, HdmvRef, IndexTable, PlaybackType, Title
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
