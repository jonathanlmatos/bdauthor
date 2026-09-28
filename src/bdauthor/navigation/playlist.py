"""Patching of PLAYLIST/*.mpls files.

Only what the menu needs: pointing a playlist written by the muxer at a differently numbered
clip. A playlist names its clips in each play item as a 5-character clip id plus "M2TS"; the
match with STREAM/<id>.m2ts and CLIPINF/<id>.clpi is by name only.
"""

import struct

_SIGNATURE = b"MPLS"
_LIST_POS_OFFSET = 8  # u32: where the PlayList() structure starts
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
        _length, _reserved, items, subpaths = struct.unpack_from(">IHHH", data, list_pos)
        if subpaths:
            raise PlaylistError("playlists with sub paths are not supported")
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
_PG, _IG = 2, 3  # positions of the counters: video, audio, PG, IG, ...


def relabel_graphics_as_interactive(data: bytes, *, old_pid: int, new_pid: int, old_coding: int, new_coding: int) -> bytes:
    """Move the single presentation graphics stream of the first play item to the IG section.

    The stream table lists video, audio, PG and IG entries in that order, so with no IG stream
    the entry keeps its position and only its counters, PID and coding type change.
    """
    patched = bytearray(data)
    try:
        first = _play_item_offsets(data)[0]
        stn = first + _PLAY_ITEM_TO_STN
        counts = data[stn + _STN_COUNTS : stn + _STN_COUNTS + 8]
        if counts[_PG] != 1 or counts[_IG] != 0:
            raise PlaylistError("expected exactly one presentation graphics stream and no interactive one")
        position = stn + _STN_ENTRIES
        for _ in range(counts[0] + counts[1]):  # skip the video and audio entries
            position += 1 + data[position]  # stream entry
            position += 1 + data[position]  # stream attributes
        entry_length = data[position]
        pid_at = position + 2  # length(1) type(1) pid(2)
        attributes = position + 1 + entry_length
        coding_at = attributes + 1
        if int.from_bytes(data[pid_at : pid_at + 2], "big") != old_pid or data[coding_at] != old_coding:
            raise PlaylistError("the graphics stream of the playlist is not the expected one")
        patched[pid_at : pid_at + 2] = new_pid.to_bytes(2, "big")
        patched[coding_at] = new_coding
        patched[stn + _STN_COUNTS + _PG] = 0
        patched[stn + _STN_COUNTS + _IG] = 1
    except IndexError as exc:
        raise PlaylistError("the playlist is truncated") from exc
    return bytes(patched)
