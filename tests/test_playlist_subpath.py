"""Adding an out-of-mux IG SubPath to a playlist: the piece tsMuxeR never produces for us."""

import struct
import subprocess

import pytest

from bdauthor.navigation.playlist import (
    _PLAY_ITEM_TO_STN,
    _STN_COUNTS,
    _STN_ENTRIES,
    _UO_MASK_OFFSET,
    PlaylistError,
    add_subpath,
    clip_ids,
    loop_play_item,
    mask_user_operations,
    uo_mask,
)


def _decode_subpath(data: bytes) -> dict:
    """A from-scratch, independent re-decode of the SubPath/SubPlayItem/STN structure, following
    libbluray's own parser layout (see docs/hdmv-ig-notes.md) -- not the code under test."""
    (list_pos,) = struct.unpack_from(">I", data, 8)
    _length, _reserved, items, subpaths = struct.unpack_from(">IHHH", data, list_pos)
    position = list_pos + 10
    (item_length,) = struct.unpack_from(">H", data, position)
    stn = position + 2 + _PLAY_ITEM_TO_STN
    counts = data[stn + _STN_COUNTS : stn + _STN_COUNTS + 7]  # 7 real counters (video..pip_pg); more is padding
    entry_pos = stn + _STN_ENTRIES
    entries = []
    for _ in range(sum(counts)):
        length = data[entry_pos]
        content = data[entry_pos + 1 : entry_pos + 1 + length]
        entry_pos += 1 + length
        attr_length = data[entry_pos]
        attr = data[entry_pos + 1 : entry_pos + 1 + attr_length]
        entry_pos += 1 + attr_length
        entries.append((content, attr))

    for _ in range(items):
        (item_len,) = struct.unpack_from(">H", data, position)
        position += 2 + item_len

    (sp_len,) = struct.unpack_from(">I", data, position)
    sp_start = position + 4
    sp_type = data[sp_start + 1]
    is_repeat = struct.unpack_from(">H", data, sp_start + 2)[0] & 1
    num_subplayitems = data[sp_start + 5]
    spi_pos = sp_start + 6
    (spi_len,) = struct.unpack_from(">H", data, spi_pos)
    body = spi_pos + 2
    clip_id = data[body : body + 5].decode()
    codec_id = data[body + 5 : body + 9].decode()
    w = struct.unpack_from(">I", data, body + 9)[0]
    connection_condition = (w >> 1) & 0xF
    is_multi_clip = w & 1
    in_time, out_time = struct.unpack_from(">II", data, body + 14)
    sync_playitem_id = struct.unpack_from(">H", data, body + 22)[0]
    sync_pts = struct.unpack_from(">I", data, body + 24)[0]

    return {
        "item_length": item_length,
        "counts": list(counts),
        "entries": entries,
        "subpaths": subpaths,
        "sp_len": sp_len,
        "sp_type": sp_type,
        "is_repeat": is_repeat,
        "num_subplayitems": num_subplayitems,
        "spi_len": spi_len,
        "clip_id": clip_id,
        "codec_id": codec_id,
        "connection_condition": connection_condition,
        "is_multi_clip": is_multi_clip,
        "in_time": in_time,
        "out_time": out_time,
        "sync_playitem_id": sync_playitem_id,
        "sync_pts": sync_pts,
    }


@pytest.fixture
def video_only_playlist(tsmuxer_disc) -> bytes:
    return (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()


def test_adds_one_subpath_with_one_subplayitem(video_only_playlist):
    patched = add_subpath(video_only_playlist, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    info = _decode_subpath(patched)
    assert info["subpaths"] == 1
    assert info["sp_type"] == 3  # confirmed against a real commercial disc's own menu SubPath
    assert info["is_repeat"] == 0
    assert info["num_subplayitems"] == 1


def test_subplayitem_matches_a_real_disc_menu_subpath(video_only_playlist):
    patched = add_subpath(video_only_playlist, clip_id="00001", in_time=524280, out_time=664064, pid=0x1400)
    info = _decode_subpath(patched)
    assert info["clip_id"] == "00001" and info["codec_id"] == "M2TS"
    assert info["connection_condition"] == 1  # non-seamless, matches the real disc
    assert info["is_multi_clip"] == 0
    assert info["in_time"] == 524280 and info["out_time"] == 664064
    assert info["sync_playitem_id"] == 0 and info["sync_pts"] == 0  # synced to the start of PlayItem 0


def test_registers_the_ig_stream_on_play_item_0(video_only_playlist):
    patched = add_subpath(video_only_playlist, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    info = _decode_subpath(patched)
    ig_count = info["counts"][3]  # video, audio, PG, IG, ...
    assert ig_count == 1
    content, attr = info["entries"][-1]
    stream_type, subpath_id, subclip_id = content[0], content[1], content[2]
    pid = int.from_bytes(content[3:5], "big")
    assert (stream_type, subpath_id, subclip_id, pid) == (2, 0, 0, 0x1400)
    coding_type = attr[0]
    assert coding_type == 0x91  # Interactive Graphics


def test_clip_ids_still_lists_only_the_main_play_item(video_only_playlist):
    patched = add_subpath(video_only_playlist, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    assert clip_ids(patched) == ["00000"]  # the SubPath's clip is not a PlayItem


def test_item_length_grew_by_the_new_stream_entry(video_only_playlist):
    (list_pos,) = struct.unpack_from(">I", video_only_playlist, 8)
    (before_length,) = struct.unpack_from(">H", video_only_playlist, list_pos + 10)
    patched = add_subpath(video_only_playlist, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    after = _decode_subpath(patched)
    assert after["item_length"] == before_length + 11  # the new STN stream entry is 11 bytes


def test_a_short_clip_id_is_refused(video_only_playlist):
    with pytest.raises(PlaylistError, match="5 digits"):
        add_subpath(video_only_playlist, clip_id="1", in_time=0, out_time=90000, pid=0x1400)


def test_libbluray_accepts_the_patched_playlist(tsmuxer_disc, libbluray_tool, tmp_path):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    patched = add_subpath(data, clip_id="00001", in_time=524280, out_time=664064, pid=0x1400)
    path = tmp_path / "00000.mpls"
    path.write_bytes(patched)
    out = subprocess.run([str(libbluray_tool("mpls_dump")), "-i", str(path)], capture_output=True, text=True)
    assert "SubPath Id: 00" in out.stdout
    assert "Interactive Graphics" in out.stdout


# --- looping the background PlayItem (see docs/hdmv-ig-notes.md) ----------------------------------


def test_loop_play_item_repeats_it_and_keeps_only_the_first_non_seamless(video_only_playlist):
    looped = loop_play_item(video_only_playlist, repeats=3)
    (list_pos,) = struct.unpack_from(">I", looped, 8)
    _length, _reserved, items, _subpaths = struct.unpack_from(">IHHH", looped, list_pos)
    assert items == 3

    position = list_pos + 10
    conditions = []
    for _ in range(items):
        (item_len,) = struct.unpack_from(">H", looped, position)
        conditions.append(looped[position + 12] & 0x0F)  # item_length(2) clip(5) codec(4) reserved(1) -> low nibble
        position += 2 + item_len
    assert conditions == [1, 5, 5]  # first Non-seamless, repeats Seamless -- matches a real commercial disc


def test_loop_play_item_repeats_are_byte_identical_except_the_connection_condition(video_only_playlist):
    looped = loop_play_item(video_only_playlist, repeats=2)
    (list_pos,) = struct.unpack_from(">I", looped, 8)
    position = list_pos + 10
    (first_len,) = struct.unpack_from(">H", looped, position)
    first = bytearray(looped[position : position + 2 + first_len])
    position += 2 + first_len
    (second_len,) = struct.unpack_from(">H", looped, position)
    second = looped[position : position + 2 + second_len]
    first[12] = (first[12] & 0xF0) | 5  # flip to Seamless: everything else must now match exactly
    assert bytes(first) == second


def test_loop_play_item_repeats_a_stn_entry_added_by_add_subpath(video_only_playlist):
    with_ig = add_subpath(video_only_playlist, clip_id="00001", in_time=0, out_time=90000, pid=0x1400)
    looped = loop_play_item(with_ig, repeats=2)
    (list_pos,) = struct.unpack_from(">I", looped, 8)
    position = list_pos + 10
    for _ in range(2):
        (item_len,) = struct.unpack_from(">H", looped, position)
        stn = position + 2 + _PLAY_ITEM_TO_STN
        counts = looped[stn + _STN_COUNTS : stn + _STN_COUNTS + 8]
        assert counts[3] == 1  # the IG counter: registered on every repeat, for free
        position += 2 + item_len


def test_loop_play_item_of_one_is_a_no_op(video_only_playlist):
    assert loop_play_item(video_only_playlist, repeats=1) == video_only_playlist


def test_loop_play_item_refuses_a_playlist_with_more_than_one_play_item():
    list_pos = 40
    data = bytearray(b"MPLS" + bytes(list_pos - 4))
    struct.pack_into(">I", data, 8, list_pos)
    data += struct.pack(">IHHH", 0, 0, 2, 0)  # length, reserved, 2 play items, 0 sub paths
    with pytest.raises(PlaylistError, match="exactly one"):
        loop_play_item(bytes(data), repeats=2)


def test_libbluray_accepts_the_looped_playlist(tsmuxer_disc, libbluray_tool, tmp_path):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    looped = loop_play_item(data, repeats=5)
    path = tmp_path / "00000.mpls"
    path.write_bytes(looped)
    out = subprocess.run([str(libbluray_tool("mpls_dump")), "-i", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("Connection Condition: Non-seamless (01)") == 1
    assert out.stdout.count("Connection Condition: Seamless (05)") == 4


# --- PlayListMark: one per repeated PlayItem, confirmed against the real disc's own bytes ---------


def _decode_marks(data: bytes) -> list[tuple[int, int, int]]:
    """Every PlayListMark as (mark_type, play_item_ref, time), independent of the code under test."""
    (mark_pos,) = struct.unpack_from(">I", data, 12)
    _length, count = struct.unpack_from(">IH", data, mark_pos)
    marks = []
    for i in range(count):
        _reserved, mark_type, play_item_ref, time, _pid, _duration = struct.unpack_from(
            ">BBHIHI", data, mark_pos + 6 + i * 14
        )
        marks.append((mark_type, play_item_ref, time))
    return marks


def test_loop_play_item_adds_one_mark_per_repeat_referencing_it(video_only_playlist):
    before = _decode_marks(video_only_playlist)
    assert len(before) == 1  # the single mark tsMuxeR wrote for a one-PlayItem playlist

    looped = loop_play_item(video_only_playlist, repeats=4)
    marks = _decode_marks(looped)
    assert len(marks) == 4
    # every mark matches the disc inspected: same type and time as the original, one per PlayItem
    assert [ref for _type, ref, _time in marks] == [0, 1, 2, 3]
    assert {mark_type for mark_type, _ref, _time in marks} == {before[0][0]}
    assert {time for _type, _ref, time in marks} == {before[0][2]}


def test_loop_play_item_refuses_a_playlist_with_more_than_one_mark():
    # a from-scratch playlist: 1 play item, but 2 marks -- not what add_menu ever produces, but the
    # function should refuse it rather than silently mis-assign play_item_ref on only some marks
    list_pos = 40
    data = bytearray(b"MPLS" + bytes(list_pos - 4))
    struct.pack_into(">I", data, 8, list_pos)
    struct.pack_into(">I", data, 12, 0)  # mark_pos placeholder, fixed below
    item = b"M2TS".rjust(9, b"0") + bytes(23)  # a minimal, well-formed-enough PlayItem body
    play_list = struct.pack(">IHHH", 10 + len(item), 0, 1, 0) + struct.pack(">H", len(item)) + item
    data += play_list
    mark_pos = len(data)
    struct.pack_into(">I", data, 12, mark_pos)
    data += struct.pack(">IH", 2 + 2 * 14, 2) + bytes(14) * 2
    with pytest.raises(PlaylistError, match="exactly one"):
        loop_play_item(bytes(data), repeats=2)


def test_libbluray_accepts_the_looped_playlist_marks(tsmuxer_disc, libbluray_tool, tmp_path):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    looped = loop_play_item(data, repeats=5)
    path = tmp_path / "00000.mpls"
    path.write_bytes(looped)
    out = subprocess.run([str(libbluray_tool("mpls_dump")), "-c", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("PlayMark Count 5") or "PlayMark Count 5\n" in out.stdout
    assert out.stdout.count("Type: Entry") == 5


# --- AppInfoPlayList()'s own UO_mask_table (see docs/hdmv-ig-notes.md) -----------------------------


def test_uo_mask_matches_the_reference_disc_menu_playlist_byte_for_byte():
    # confirmed against libbluray's own src/libbluray/bdnav/uo_mask_table.h / uo_mask.c bit order
    mask = uo_mask(
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
    assert mask == bytes.fromhex("3cb805ff40000000")  # the reference disc's own menu, byte-for-byte


def test_uo_mask_refuses_an_unknown_operation_name():
    with pytest.raises(PlaylistError, match="unknown"):
        uo_mask(masked=frozenset({"not_a_real_operation"}))


def test_mask_user_operations_patches_only_the_uo_mask_bytes(video_only_playlist):
    mask = uo_mask(masked=frozenset({"stop"}))
    patched = mask_user_operations(video_only_playlist, mask)
    assert patched[_UO_MASK_OFFSET : _UO_MASK_OFFSET + 8] == mask
    assert patched[:_UO_MASK_OFFSET] == video_only_playlist[:_UO_MASK_OFFSET]
    assert patched[_UO_MASK_OFFSET + 8 :] == video_only_playlist[_UO_MASK_OFFSET + 8 :]


def test_libbluray_accepts_the_masked_playlist(tsmuxer_disc, libbluray_tool, tmp_path):
    data = (tsmuxer_disc / "BDMV" / "PLAYLIST" / "00000.mpls").read_bytes()
    patched = mask_user_operations(data, uo_mask(masked=frozenset({"stop", "move_up"})))
    path = tmp_path / "00000.mpls"
    path.write_bytes(patched)
    out = subprocess.run([str(libbluray_tool("mpls_dump")), "-c", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
