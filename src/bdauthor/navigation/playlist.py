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
