"""Patching of CLIPINF/*.clpi files: the streams of a clip are listed in its program info."""

import struct

_SIGNATURE = b"HDMV"
_SEQUENCE_INFO_START = 8  # u32 in the header: where SequenceInfo() starts, right after ClipInfo()
_PROGRAM_INFO_START = 12  # u32 in the header: where ProgramInfo() starts
_CPI_START = 16  # u32 in the header: where CPI() starts
_MARK_START = 20  # u32 in the header: where ClipMark() starts
_EXT_START = 24  # u32 in the header: where ExtensionData() starts, or 0 if there is none

_CLIP_INFO_LENGTH_AT = 40  # ClipInfo() always starts right after the fixed 40-byte header
_ATC_DELTA_FLAG_AT = 51  # is_atc_delta: bit 0 (LSB) of the reserved(31)+is_atc_delta(1) word


class ClipInfoError(Exception):
    """The clip info file is not one this module knows how to patch."""


def program_map_pid(data: bytes) -> int:
    """PID of the program map table of the clip's first (and normally only) program."""
    if data[:4] != _SIGNATURE:
        raise ClipInfoError("not a clip info file")
    try:
        (start,) = struct.unpack_from(">I", data, _PROGRAM_INFO_START)
        # ProgramInfo(): length(4) reserved(1) number_of_program_sequences(1), then spn(4) pmt pid(2)
        return struct.unpack_from(">H", data, start + 6 + 4)[0]
    except struct.error as exc:
        raise ClipInfoError("the clip info file is truncated") from exc


def add_self_referencing_atc_delta(data: bytes, *, clip_id: str) -> bytes:
    """Set `is_atc_delta` and add one ATC delta entry pointing at the clip's own id.

    Matches a real commercial disc's own menu background clip exactly: it has `is_ATC_delta:
    True` with a single entry referencing itself (`File Id`/`File Code` equal to its own clip id
    and "M2TS"). libbluray's own player (`bluray.c`, `navigation.c`) never reads `atc_delta`
    during navigation or playback -- confirmed directly in its source -- so this is pure
    byte-for-byte fidelity with the disc inspected, not a functional fix; see
    docs/hdmv-ig-notes.md.
    """
    if data[:4] != _SIGNATURE:
        raise ClipInfoError("not a clip info file")
    if len(clip_id) != 5 or not clip_id.isdigit():
        raise ClipInfoError(f"a clip id is 5 digits, got {clip_id!r}")
    try:
        flag_byte = data[_ATC_DELTA_FLAG_AT]
        if flag_byte & 1:
            raise ClipInfoError("is_atc_delta is already set")
        (insert_at,) = struct.unpack_from(">I", data, _SEQUENCE_INFO_START)
        clip_info_length = struct.unpack_from(">I", data, _CLIP_INFO_LENGTH_AT)[0]
    except struct.error as exc:
        raise ClipInfoError("the clip info file is truncated") from exc

    # ATC_delta_id(): reserved(8) num_ATC_delta(8), then delta(32) file_id(5 chars) file_code(4 chars) reserved(8)
    entry = struct.pack(">I", 0) + clip_id.encode("ascii") + b"M2TS" + bytes(1)
    atc_delta_block = bytes([0, 1]) + entry

    patched = bytearray(data)
    patched[insert_at:insert_at] = atc_delta_block
    patched[_ATC_DELTA_FLAG_AT] |= 1
    struct.pack_into(">I", patched, _CLIP_INFO_LENGTH_AT, clip_info_length + len(atc_delta_block))
    for offset in (_SEQUENCE_INFO_START, _PROGRAM_INFO_START, _CPI_START, _MARK_START):
        struct.pack_into(">I", patched, offset, struct.unpack_from(">I", patched, offset)[0] + len(atc_delta_block))
    ext_addr = struct.unpack_from(">I", patched, _EXT_START)[0]
    if ext_addr:
        struct.pack_into(">I", patched, _EXT_START, ext_addr + len(atc_delta_block))
    return bytes(patched)
