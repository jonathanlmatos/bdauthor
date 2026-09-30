"""Patching of PLAYLIST/*.mpls files.

Only what the menu needs: pointing a playlist written by the muxer at a differently numbered
clip. A playlist names its clips in each play item as a 5-character clip id plus "M2TS"; the
match with STREAM/<id>.m2ts and CLIPINF/<id>.clpi is by name only.
"""

import struct

_SIGNATURE = b"MPLS"
_LIST_POS_OFFSET = 8  # u32: where the PlayList() structure starts
_MARK_POS_OFFSET = 12  # u32: where PlayListMark() starts, right after PlayList()
_EXT_POS_OFFSET = 16  # u32: where ExtensionData() starts, right after PlayListMark()
_CLIP_ID_LENGTH = 5
_CODEC_ID = b"M2TS"


class PlaylistError(Exception):
    """The playlist is not one this module knows how to patch."""


def clip_ids(data: bytes) -> list[str]:
    """The clip id of every play item, in order."""
    return [data[at : at + _CLIP_ID_LENGTH].decode("ascii") for at in _play_item_offsets(data)]


def retarget_playlist(data: bytes, old: str, new: str) -> bytes:
    """Return `data` with every play item that plays clip `old` playing clip `new` instead."""
    if len(new) != _CLIP_ID_LENGTH or not new.isdigit():
        raise PlaylistError(f"a clip id is {_CLIP_ID_LENGTH} digits, got {new!r}")
    patched = bytearray(data)
    for at in _play_item_offsets(data):
        found = data[at : at + _CLIP_ID_LENGTH].decode("ascii")
        if found != old:
            raise PlaylistError(f"the playlist plays clip {found}, expected {old}")
        patched[at : at + _CLIP_ID_LENGTH] = new.encode("ascii")
    return bytes(patched)


def _play_item_offsets(data: bytes) -> list[int]:
    """Offset of the clip id inside each PlayItem()."""
    if data[:4] != _SIGNATURE or len(data) < 20:
        raise PlaylistError("not a playlist file")
    (list_pos,) = struct.unpack_from(">I", data, _LIST_POS_OFFSET)
    # PlayList(): length(4) reserved(2) number_of_PlayItems(2) number_of_SubPaths(2)
    try:
        _length, _reserved, items, _subpaths = struct.unpack_from(">IHHH", data, list_pos)
        offsets = []
        position = list_pos + 10
        for _ in range(items):
            (item_length,) = struct.unpack_from(">H", data, position)
            clip_at = position + 2
            if data[clip_at + _CLIP_ID_LENGTH : clip_at + _CLIP_ID_LENGTH + 4] != _CODEC_ID:
                raise PlaylistError("a play item does not reference an M2TS clip")
            offsets.append(clip_at)
            position += 2 + item_length
    except struct.error as exc:
        raise PlaylistError("the playlist is truncated") from exc
    return offsets


_PLAY_ITEM_TO_STN = 32  # from the clip id: clip(5) codec(4) flags(2) stc(1) in(4) out(4) uo mask(8) ra(1) still(3)
_STN_COUNTS = 4  # from the STN start: length(2) reserved(2), then one counter byte per stream kind
_STN_ENTRIES = 16  # ... and the counters (8 bytes) plus 4 reserved bytes come before the first entry
_IG = 3  # position of the IG counter: video, audio, PG, IG, ...


_SUBPATH_TYPE_OUT_OF_MUX_IG = 3  # confirmed against a real commercial disc's own menu SubPath
_STREAM_TYPE_SUBPATH = 2  # stream_type of an STN entry pointing at a SubPath+SubClip (see _parse_stream)
_IG_CODING_TYPE = 0x91

_CONNECTION_CONDITION_AT = 12  # from item_length: item_length(2) clip(5) codec(4) reserved(1) -> low nibble of byte 12
_CONNECTION_SEAMLESS = 5

_MARK_RECORD_LEN = 14  # reserved(1) mark_type(1) play_item_ref(2) time(4) entry_es_pid(2) duration(4)
_MARK_PLAY_ITEM_REF_AT = 2  # offset of play_item_ref within one 14-byte mark record


def loop_play_item(data: bytes, *, repeats: int) -> bytes:
    """Repeat the playlist's sole PlayItem `repeats` times in total, connected seamlessly.

    A menu that loops its short background clip by having a MovieObject re-select the same
    short playlist over and over reopens it every time -- which resets an out-of-mux IG menu
    back to its first page and reloads its SubPath every time too (see docs/hdmv-ig-notes.md).
    A real commercial disc instead repeats the SAME PlayItem, seamlessly connected, inside the
    one playlist that is opened just once: 501 back-to-back repeats of a ~47-second clip, only
    the first one `Non-seamless`, on the disc inspected. Each repeat here is a byte-for-byte
    copy of the (already patched) first PlayItem, so whatever is already registered on its STN
    table -- like an IG SubPath stream -- is repeated along with it, for free.

    Every one of those 501 PlayItems also has its own PlayListMark() entry (`mark_type` Entry,
    `play_item_ref` pointing at that specific PlayItem, `time` equal to its own IN_time) -- a
    single playlist-wide mark is not enough; each repeat gets one, confirmed against the raw
    bytes of the disc inspected (see docs/hdmv-ig-notes.md).
    """
    if repeats < 1:
        raise PlaylistError("repeats must be at least 1")
    try:
        (list_pos,) = struct.unpack_from(">I", data, _LIST_POS_OFFSET)
        _length, _reserved, items, _subpaths = struct.unpack_from(">IHHH", data, list_pos)
        if items != 1:
            raise PlaylistError("expected exactly one play item to loop")
        first = list_pos + 10
        (item_length,) = struct.unpack_from(">H", data, first)
        record = bytearray(data[first : first + 2 + item_length])
    except struct.error as exc:
        raise PlaylistError("the playlist is truncated") from exc
    if repeats == 1:
        return bytes(data)

    record[_CONNECTION_CONDITION_AT] = (record[_CONNECTION_CONDITION_AT] & 0xF0) | _CONNECTION_SEAMLESS
    inserted = bytes(record) * (repeats - 1)

    patched = bytearray(data)
    patched[first + 2 + item_length : first + 2 + item_length] = inserted
    struct.pack_into(">H", patched, list_pos + 6, repeats)  # number_of_PlayItems
    struct.pack_into(">I", patched, list_pos, struct.unpack_from(">I", patched, list_pos)[0] + len(inserted))
    for offset in (_MARK_POS_OFFSET, _EXT_POS_OFFSET):
        struct.pack_into(">I", patched, offset, struct.unpack_from(">I", patched, offset)[0] + len(inserted))

    try:
        (mark_pos,) = struct.unpack_from(">I", patched, _MARK_POS_OFFSET)
        mark_length, mark_count = struct.unpack_from(">IH", patched, mark_pos)
        if mark_count != 1:
            raise PlaylistError("expected exactly one play mark to repeat")
        template = bytearray(patched[mark_pos + 6 : mark_pos + 6 + _MARK_RECORD_LEN])
    except struct.error as exc:
        raise PlaylistError("the playlist is truncated") from exc

    new_marks = bytearray()
    for item_index in range(1, repeats):
        mark = bytearray(template)
        struct.pack_into(">H", mark, _MARK_PLAY_ITEM_REF_AT, item_index)
        new_marks += mark
    marks_end = mark_pos + 6 + _MARK_RECORD_LEN
    patched[marks_end:marks_end] = new_marks
    struct.pack_into(">H", patched, mark_pos + 4, repeats)  # mark_count
    struct.pack_into(">I", patched, mark_pos, mark_length + len(new_marks))
    struct.pack_into(">I", patched, _EXT_POS_OFFSET, struct.unpack_from(">I", patched, _EXT_POS_OFFSET)[0] + len(new_marks))
    return bytes(patched)


def add_subpath(data: bytes, *, clip_id: str, in_time: int, out_time: int, pid: int) -> bytes:
    """Add an out-of-mux IG SubPath playing `clip_id`, and register its stream on PlayItem 0.

    `subpath_id`/`subclip_id` are always 0 (a playlist with a single subpath and a single
    subclip in it, exactly the menu's case). `connection_condition`, `sync_play_item_id` and
    `sync_pts` match a real commercial disc's own menu SubPath: non-seamless, synced to the
    start of PlayItem 0. Field layout confirmed against libbluray's own parser
    (`src/libbluray/bdnav/mpls_parse.c`); see docs/hdmv-ig-notes.md.
    """
    if len(clip_id) != _CLIP_ID_LENGTH or not clip_id.isdigit():
        raise PlaylistError(f"a clip id is {_CLIP_ID_LENGTH} digits, got {clip_id!r}")
    patched = bytearray(data)
    try:
        (list_pos,) = struct.unpack_from(">I", data, _LIST_POS_OFFSET)
        _length, _reserved, items, subpaths = struct.unpack_from(">IHHH", data, list_pos)
        first = list_pos + 10
        (item_length,) = struct.unpack_from(">H", data, first)
        stn = first + 2 + _PLAY_ITEM_TO_STN  # +2: _PLAY_ITEM_TO_STN is measured from the clip id, not item_length
        counts = data[stn + _STN_COUNTS : stn + _STN_COUNTS + 8]
        insert_at = stn + _STN_ENTRIES
        for count in counts[: _IG + 1]:  # video, audio, PG entries: the new IG entry goes right after
            for _ in range(count):
                insert_at += 1 + data[insert_at]  # stream entry
                insert_at += 1 + data[insert_at]  # stream attributes
    except (struct.error, IndexError) as exc:
        raise PlaylistError("the playlist is truncated") from exc

    stream_content = bytes([_STREAM_TYPE_SUBPATH, 0, 0]) + pid.to_bytes(2, "big")  # type, subpath_id=subclip_id=0, pid
    stream_entry = bytes([len(stream_content)]) + stream_content
    attribute_content = bytes([_IG_CODING_TYPE]) + b"eng"
    attributes = bytes([len(attribute_content)]) + attribute_content
    new_stream = stream_entry + attributes

    sub_play_item = clip_id.encode("ascii") + _CODEC_ID
    sub_play_item += struct.pack(">I", 1 << 1)  # reserved(27)+connection_condition(4)=1+is_multi_clip(1)=0
    sub_play_item += bytes([0])  # stc_id
    sub_play_item += struct.pack(">II", in_time, out_time)
    sub_play_item += struct.pack(">H", 0)  # sync_play_item_id: PlayItem 0
    sub_play_item += struct.pack(">I", 0)  # sync_pts
    sub_play_item = struct.pack(">H", len(sub_play_item)) + sub_play_item

    sub_path = bytes([0, _SUBPATH_TYPE_OUT_OF_MUX_IG]) + struct.pack(">H", 0) + bytes([0, 1]) + sub_play_item
    sub_path = struct.pack(">I", len(sub_path)) + sub_path

    patched[insert_at:insert_at] = new_stream
    struct.pack_into(">H", patched, stn, struct.unpack_from(">H", patched, stn)[0] + len(new_stream))
    patched[stn + _STN_COUNTS + _IG] += 1
    struct.pack_into(">H", patched, first, item_length + len(new_stream))

    position = list_pos + 10
    for _ in range(items):
        (item_len,) = struct.unpack_from(">H", patched, position)
        position += 2 + item_len
    subpaths_end = position
    for _ in range(subpaths):
        (sp_len,) = struct.unpack_from(">I", patched, subpaths_end)
        subpaths_end += 4 + sp_len
    patched[subpaths_end:subpaths_end] = sub_path

    total_inserted = len(new_stream) + len(sub_path)
    struct.pack_into(">I", patched, list_pos, struct.unpack_from(">I", patched, list_pos)[0] + total_inserted)
    struct.pack_into(">H", patched, list_pos + 8, subpaths + 1)
    for offset in (_MARK_POS_OFFSET, _EXT_POS_OFFSET):
        struct.pack_into(">I", patched, offset, struct.unpack_from(">I", patched, offset)[0] + total_inserted)
    return bytes(patched)
